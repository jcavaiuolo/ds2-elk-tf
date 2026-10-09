########################################
# Cloud Firewall
########################################
#
# Three rule groups:
#   1. SSH + Kibana to the admin CIDRs you control.
#   2. Elasticsearch _bulk endpoint, locked to the DS2 IP ACL ranges only.
#   3. Egress open (needed for apt, Let's Encrypt, Object Storage snapshots).
#

resource "linode_firewall" "elk" {
  label           = "${var.label}-fw"
  inbound_policy  = "DROP"
  outbound_policy = "ACCEPT"
  tags            = var.tags

  # SSH from admin
  inbound {
    label    = "ssh-admin"
    action   = "ACCEPT"
    protocol = "TCP"
    ports    = "22"
    ipv4     = var.allowed_admin_cidrs
  }

  # Kibana from admin
  inbound {
    label    = "kibana-admin"
    action   = "ACCEPT"
    protocol = "TCP"
    ports    = tostring(var.kibana_port)
    ipv4     = var.allowed_admin_cidrs
  }

  # Elasticsearch _bulk, DataStream 2 ingest IPs only
  inbound {
    label    = "es-bulk-ds2"
    action   = "ACCEPT"
    protocol = "TCP"
    ports    = tostring(var.elasticsearch_port)
    ipv4     = [for cidr in var.datastream2_ip_acl : cidr if length(regexall(":", cidr)) == 0]
    ipv6     = [for cidr in var.datastream2_ip_acl : cidr if length(regexall(":", cidr)) > 0]
  }

  # Optional HTTPS on ES, same ACL
  dynamic "inbound" {
    for_each = var.enable_lets_encrypt ? [1] : []
    content {
      label    = "es-bulk-ds2-tls"
      action   = "ACCEPT"
      protocol = "TCP"
      ports    = "443"
      ipv4     = [for cidr in var.datastream2_ip_acl : cidr if length(regexall(":", cidr)) == 0]
      ipv6     = [for cidr in var.datastream2_ip_acl : cidr if length(regexall(":", cidr)) > 0]
    }
  }

  # Short-lived port 80 window for certbot --standalone on first boot
  dynamic "inbound" {
    for_each = var.enable_lets_encrypt ? [1] : []
    content {
      label    = "le-http01"
      action   = "ACCEPT"
      protocol = "TCP"
      ports    = "80"
      ipv4     = ["0.0.0.0/0"]
      ipv6     = ["::/0"]
    }
  }

  linodes = [linode_instance.elk.id]
}

########################################
# Compute instance (invokes the StackScript)
########################################

resource "linode_instance" "elk" {
  label           = var.label
  region          = var.region
  type            = var.instance_type
  image           = var.image
  tags            = var.tags
  root_pass       = var.root_password
  authorized_keys = var.authorized_keys
  backups_enabled = true

  stackscript_id = var.stackscript_id

  # UDF names match Hideki Okamoto's StackScript 1059555. Rename when you fork
  # and your UDFs diverge.
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
# Block storage for ES data
########################################

resource "linode_volume" "es_data" {
  count     = var.data_volume_size_gb > 0 ? 1 : 0
  label     = "${var.label}-data"
  region    = var.region
  size      = var.data_volume_size_gb
  linode_id = linode_instance.elk.id
  tags      = var.tags
}
