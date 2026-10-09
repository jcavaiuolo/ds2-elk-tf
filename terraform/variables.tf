########################################
# Deploy context
########################################

variable "label" {
  type        = string
  description = "Prefix for all created resources (instance, firewall, volume)."
  default     = "ds2-elk"
}

variable "region" {
  type        = string
  description = "Akamai Cloud (Linode) region, e.g. us-east, ap-northeast, br-gru, se-sto."
  default     = "us-east"
}

variable "instance_type" {
  type        = string
  description = "Compute instance type. 8 GB is the minimum. Bump for production."
  default     = "g6-dedicated-4" # 8 GB RAM, 4 vCPU dedicated
}

variable "image" {
  type        = string
  description = "Image slug. StackScript 1059555 only supports Ubuntu 22.04."
  default     = "linode/ubuntu22.04"
}

variable "tags" {
  type        = list(string)
  description = "Tags to apply to created resources."
  default     = ["ds2", "elasticsearch", "kibana"]
}

variable "backups_enabled" {
  type        = bool
  description = "Enable the Linode Backup service on the instance (billed separately)."
  default     = false
}

########################################
# SSH & root access
########################################

variable "root_password" {
  type        = string
  description = "Root password for the compute instance. Required by the Linode API."
  sensitive   = true
}

variable "authorized_keys" {
  type        = list(string)
  description = "SSH public keys. The first one is also installed for ssh_user by the StackScript."
  default     = []
}

variable "ssh_user" {
  type        = string
  description = "Limited sudo user created by the StackScript."
  default     = "elkadmin"
}

variable "ssh_user_password" {
  type        = string
  description = "Password for ssh_user (used for sudo)."
  sensitive   = true
}

variable "disable_root_ssh" {
  type        = bool
  description = "StackScript UDF disable_root: disables root login and password authentication over SSH."
  default     = true
}

########################################
# StackScript (Hideki Okamoto, 1059555)
########################################

variable "stackscript_id" {
  type        = number
  description = "ID of the StackScript that installs Elasticsearch and Kibana."
  default     = 1059555
}

variable "es_admin_password" {
  type        = string
  description = "Password for the Elasticsearch 'elastic' user (also the Kibana login)."
  sensitive   = true
}

variable "ds2_ingest_user" {
  type        = string
  description = "Elasticsearch user DataStream 2 uses to POST to the _bulk endpoint."
  default     = "ds2_ingest"
}

variable "ds2_ingest_password" {
  type        = string
  description = "Password for the DS2 ingest user."
  sensitive   = true
}

########################################
# Storage
########################################

variable "data_volume_size_gb" {
  type        = number
  description = "Extra Block Storage volume for Elasticsearch data, mounted by the post-install step. 0 = use the disk included in the plan (160 GB on g6-dedicated-4)."
  default     = 0
}

########################################
# Networking
########################################

variable "allowed_admin_cidrs" {
  type        = list(string)
  description = "CIDRs allowed to reach SSH (22) and Kibana (5601). deploy.py proposes your current public IP."

  validation {
    condition     = length(var.allowed_admin_cidrs) > 0 && alltrue([for c in var.allowed_admin_cidrs : can(cidrhost(c, 0))])
    error_message = "allowed_admin_cidrs must be a non-empty list of valid CIDRs, e.g. [\"203.0.113.10/32\"]."
  }
}

variable "datastream2_ip_acl" {
  type        = list(string)
  description = "Pin the DataStream 2 source ranges. Leave null to download Akamai's current list on every plan."
  default     = null
}

variable "akamai_ipv4_acl_url" {
  type        = string
  description = "Akamai's published IPv4 Origin IP ACL list (also used by DataStream 2)."
  default     = "https://techdocs.akamai.com/property-manager/pdfs/akamai_ipv4_CIDRs.txt"
}

variable "akamai_ipv6_acl_url" {
  type        = string
  description = "Akamai's published IPv6 Origin IP ACL list (also used by DataStream 2)."
  default     = "https://techdocs.akamai.com/property-manager/pdfs/akamai_ipv6_CIDRs.txt"
}

variable "elasticsearch_port" {
  type        = number
  description = "Elasticsearch HTTP port exposed to the DS2 ACL when TLS is off."
  default     = 9200
}

variable "kibana_port" {
  type        = number
  description = "Kibana HTTP port exposed to the admin CIDRs."
  default     = 5601
}

########################################
# Optional HTTPS for the DS2 endpoint
########################################
#
# When enabled, the post-install step puts nginx with a Let's Encrypt
# certificate in front of Elasticsearch on 443, and the firewall swaps
# 9200 for 443 (DS2 ACL) plus 80 (world, ACME HTTP-01 challenge + renewals).

variable "enable_tls" {
  type        = bool
  description = "Serve the DS2 endpoint over HTTPS (nginx + Let's Encrypt)."
  default     = false
}

variable "tls_hostname" {
  type        = string
  description = "Hostname for the certificate. Empty = the instance's reverse DNS name (<ip-dashed>.ip.linodeusercontent.com)."
  default     = ""
}
