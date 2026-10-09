########################################
# DataStream 2 IP ACL (downloaded on every plan)
########################################
#
# DataStream 2 pushes from the same ranges Akamai publishes for Origin IP ACL.
# The lists are fetched live so the firewall follows Akamai's updates on every
# apply. Set var.datastream2_ip_acl to pin your own list instead.

data "http" "akamai_acl" {
  for_each = var.datastream2_ip_acl == null ? {
    ipv4 = var.akamai_ipv4_acl_url
    ipv6 = var.akamai_ipv6_acl_url
  } : {}

  url = each.value

  lifecycle {
    postcondition {
      condition     = self.status_code == 200
      error_message = "Could not download the Akamai IP ACL from ${each.value} (HTTP ${self.status_code}). Retry, or set datastream2_ip_acl explicitly."
    }
  }
}

locals {
  fetched_ipv4 = try(regexall("(?:[0-9]{1,3}\\.){3}[0-9]{1,3}/[0-9]{1,2}", data.http.akamai_acl["ipv4"].response_body), [])
  fetched_ipv6 = try(regexall("[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{0,4})+/[0-9]{1,3}", data.http.akamai_acl["ipv6"].response_body), [])

  ds2_acl = var.datastream2_ip_acl != null ? var.datastream2_ip_acl : distinct([
    for cidr in concat(local.fetched_ipv4, local.fetched_ipv6) : cidr if can(cidrhost(cidr, 0))
  ])

  # The Linode provider rejects empty ipv4/ipv6 lists in a firewall rule, so an
  # address family with no entries becomes null.
  ds2_acl_ipv4 = try(coalescelist([for cidr in local.ds2_acl : cidr if !strcontains(cidr, ":")]), null)
  ds2_acl_ipv6 = try(coalescelist([for cidr in local.ds2_acl : cidr if strcontains(cidr, ":")]), null)
  admin_ipv4   = try(coalescelist([for cidr in var.allowed_admin_cidrs : cidr if !strcontains(cidr, ":")]), null)
  admin_ipv6   = try(coalescelist([for cidr in var.allowed_admin_cidrs : cidr if strcontains(cidr, ":")]), null)
  public_ipv4  = tolist(linode_instance.elk.ipv4)[0]
  rdns_host    = "${replace(local.public_ipv4, ".", "-")}.ip.linodeusercontent.com"
  tls_hostname = var.tls_hostname != "" ? var.tls_hostname : local.rdns_host
}

########################################
# Cloud Firewall
########################################
#
#   1. SSH + Kibana from the admin CIDRs you control.
#   2. Elasticsearch, from the DataStream 2 IP ACL only:
#        - plain HTTP on 9200 by default (same as Hideki's setup), or
#        - HTTPS on 443 through nginx + Let's Encrypt when enable_tls = true
#          (port 80 then opens to the world for the ACME HTTP-01 challenge).
#   3. Egress open (apt, Let's Encrypt, Elastic artifacts).

resource "linode_firewall" "elk" {
  label           = "${var.label}-fw"
  inbound_policy  = "DROP"
  outbound_policy = "ACCEPT"
  tags            = var.tags

  inbound {
    label    = "ssh-admin"
    action   = "ACCEPT"
    protocol = "TCP"
    ports    = "22"
    ipv4     = local.admin_ipv4
    ipv6     = local.admin_ipv6
  }

  inbound {
    label    = "kibana-admin"
    action   = "ACCEPT"
    protocol = "TCP"
    ports    = tostring(var.kibana_port)
    ipv4     = local.admin_ipv4
    ipv6     = local.admin_ipv6
  }

  dynamic "inbound" {
    for_each = var.enable_tls ? [] : [1]
    content {
      label    = "es-http-ds2"
      action   = "ACCEPT"
      protocol = "TCP"
      ports    = tostring(var.elasticsearch_port)
      ipv4     = local.ds2_acl_ipv4
      ipv6     = local.ds2_acl_ipv6
    }
  }

  dynamic "inbound" {
    for_each = var.enable_tls ? [1] : []
    content {
      label    = "es-https-ds2"
      action   = "ACCEPT"
      protocol = "TCP"
      ports    = "443"
      ipv4     = local.ds2_acl_ipv4
      ipv6     = local.ds2_acl_ipv6
    }
  }

  dynamic "inbound" {
    for_each = var.enable_tls ? [1] : []
    content {
      label    = "acme-http01"
      action   = "ACCEPT"
      protocol = "TCP"
      ports    = "80"
      ipv4     = ["0.0.0.0/0"]
      ipv6     = ["::/0"]
    }
  }

  lifecycle {
    precondition {
      condition     = length(local.ds2_acl) > 0
      error_message = "The DataStream 2 IP ACL is empty. Check the Akamai ACL URLs or set datastream2_ip_acl."
    }
  }
}

########################################
# Compute instance (runs Hideki's StackScript)
########################################

resource "linode_instance" "elk" {
  label           = var.label
  region          = var.region
  type            = var.instance_type
  image           = var.image
  tags            = var.tags
  root_pass       = var.root_password
  authorized_keys = var.authorized_keys
  backups_enabled = var.backups_enabled

  # Attach the firewall at creation so the instance is never exposed while
  # the StackScript is installing.
  firewall_id = linode_firewall.elk.id

  stackscript_id = var.stackscript_id

  # UDF names of StackScript 1059555.
  stackscript_data = {
    username               = var.ssh_user
    password               = var.ssh_user_password
    pubkey                 = length(var.authorized_keys) > 0 ? var.authorized_keys[0] : ""
    disable_root           = var.disable_root_ssh ? "Yes" : "No"
    elasticsearch_password = var.es_admin_password
    ds2_username           = var.ds2_ingest_user
    ds2_password           = var.ds2_ingest_password
  }
}

########################################
# Block storage for Elasticsearch data
########################################
#
# Terraform only attaches the volume. remote/post-install.sh (run by deploy.py)
# formats it, moves /var/lib/elasticsearch onto it and mounts it via fstab.

resource "linode_volume" "es_data" {
  count     = var.data_volume_size_gb > 0 ? 1 : 0
  label     = "${var.label}-data"
  region    = var.region
  size      = var.data_volume_size_gb
  linode_id = linode_instance.elk.id
  tags      = var.tags
}
