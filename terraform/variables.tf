########################################
# Deploy context
########################################

variable "label" {
  type        = string
  description = "Prefix for all created resources (instance, firewall, volume, VPC)."
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
  description = "Image slug for the instance. Must match the StackScript's supported image list; Hideki's 1059555 is pinned to Ubuntu 22.04."
  default     = "linode/ubuntu22.04"
}

variable "tags" {
  type        = list(string)
  description = "Tags to apply to created resources."
  default     = ["ds2", "elasticsearch", "kibana"]
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
  description = "SSH public keys installed into root's authorized_keys at provision time."
  default     = []
}

variable "ssh_user" {
  type        = string
  description = "Non-root user created by the StackScript for SSH login."
  default     = "ec2-user"
}

variable "ssh_user_password" {
  type        = string
  description = "Initial password for the non-root SSH user."
  sensitive   = true
}

########################################
# StackScript
########################################
#
# Default points at Hideki Okamoto's original StackScript (1059555). Its UDFs
# are: username, password, pubkey, disable_root, elasticsearch_password,
# ds2_username, ds2_password. The elasticsearch_password is shared with Kibana.
# Swap the ID when the fork is published.

variable "stackscript_id" {
  type        = number
  description = "ID of the StackScript that installs Elasticsearch and Kibana."
  default     = 1059555
}

variable "es_admin_password" {
  type        = string
  description = "Password for the Elasticsearch 'elastic' user (shared with Kibana in Hideki's StackScript)."
  sensitive   = true
}

variable "ds2_ingest_user" {
  type        = string
  description = "Elasticsearch user DataStream 2 will use to POST to the _bulk endpoint."
  default     = "ds2_ingest"
}

variable "ds2_ingest_password" {
  type        = string
  description = "Password for the DS2 ingest user."
  sensitive   = true
}

variable "disable_root_ssh" {
  type        = bool
  description = "Disable root login over SSH. Recommended."
  default     = true
}

########################################
# Storage
########################################

variable "data_volume_size_gb" {
  type        = number
  description = "Size of the attached Block Storage volume used for Elasticsearch data. Set 0 to disable."
  default     = 100
}

########################################
# Networking: Firewall
########################################

variable "allowed_admin_cidrs" {
  type        = list(string)
  description = "CIDR blocks allowed to reach SSH (22) and Kibana (5601). Lock this down."
  default     = ["0.0.0.0/0"]
}

variable "datastream2_ip_acl" {
  type        = list(string)
  description = <<EOT
IPv4/IPv6 ranges from which DataStream 2 pushes logs. Published by Akamai in January 2026
(same set used by Origin IP ACL for the CDN). Keep this up to date via:
  https://techdocs.akamai.com/datastream2/changelog/jan-7-2026-ip-acl-support
Example (illustrative, replace with the live list):
EOT
  default = [
    "23.32.0.0/11",
    "23.192.0.0/11",
    "104.64.0.0/10",
    "184.24.0.0/13",
    "2600:1400::/32",
  ]
}

variable "elasticsearch_port" {
  type        = number
  description = "ES HTTP port exposed to DS2 ACL ranges."
  default     = 9200
}

variable "kibana_port" {
  type        = number
  description = "Kibana HTTP port exposed to admin ACL ranges."
  default     = 5601
}

########################################
# Post-install HTTPS (manual)
########################################
# Hideki's StackScript does not configure Let's Encrypt. If you flip this on,
# the firewall opens ports 80 and 443 so you can SSH in and run certbot
# yourself after boot, then flip xpack.security.http.ssl on in elasticsearch.yml
# and swap the DS2 endpoint to https://. The fork plan automates this.

variable "enable_lets_encrypt" {
  type        = bool
  default     = false
  description = "Open ports 80 and 443 on the firewall so you can run certbot post-install."
}

variable "public_hostname" {
  type        = string
  default     = ""
  description = "Hostname you plan to serve HTTPS from. Used in outputs when enable_lets_encrypt is true."
}
