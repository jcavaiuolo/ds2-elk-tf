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
# Optional VPC
########################################

resource "linode_vpc" "elk" {
  count       = var.use_vpc ? 1 : 0
  label       = "${var.label}-vpc"
  region      = var.region
  description = "VPC for ${var.label} Elasticsearch + Kibana"
}

resource "linode_vpc_subnet" "elk" {
  count  = var.use_vpc ? 1 : 0
  vpc_id = linode_vpc.elk[0].id
  label  = "${var.label}-subnet"
  ipv4   = var.vpc_subnet_cidr
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

  # UDF values passed to the StackScript. Field names must match the UDFs in the
  # forked StackScript. Rename to match the original if you keep stackscript_id = 1059555.
  stackscript_data = {
    SSH_USER                  = var.ssh_user
    SSH_USER_PASSWORD         = var.ssh_user_password
    ES_ADMIN_PASSWORD         = var.es_admin_password
    KIBANA_ADMIN_PASSWORD     = var.kibana_admin_password
    DS2_INGEST_USER           = var.ds2_ingest_user
    DS2_INGEST_PASSWORD       = var.ds2_ingest_password
    OBJECT_STORAGE_BUCKET     = var.object_storage_bucket
    OBJECT_STORAGE_REGION     = var.object_storage_region
    OBJECT_STORAGE_ACCESS_KEY = var.object_storage_access_key
    OBJECT_STORAGE_SECRET_KEY = var.object_storage_secret_key
    ENABLE_LETS_ENCRYPT       = tostring(var.enable_lets_encrypt)
    PUBLIC_HOSTNAME           = var.public_hostname
  }

  # If use_vpc is on, attach a VPC interface in addition to the default public one.
  dynamic "interface" {
    for_each = var.use_vpc ? [1] : []
    content {
      purpose = "public"
    }
  }

  dynamic "interface" {
    for_each = var.use_vpc ? [1] : []
    content {
      purpose   = "vpc"
      subnet_id = linode_vpc_subnet.elk[0].id
      ipv4 {
        vpc = cidrhost(var.vpc_subnet_cidr, 10)
      }
    }
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
