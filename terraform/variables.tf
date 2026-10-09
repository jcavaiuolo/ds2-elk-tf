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
  description = "Image slug for the instance."
  default     = "linode/ubuntu24.04"
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
# StackScript (forked from Hideki Okamoto's original)
########################################

variable "stackscript_id" {
  type        = number
  description = "ID of the StackScript that installs Elasticsearch and Kibana. Default is Hideki's original (1059555); replace with your fork once published."
  default     = 1059555
}

variable "es_admin_password" {
  type        = string
  description = "Password for the Elasticsearch 'elastic' admin user."
  sensitive   = true
}

variable "kibana_admin_password" {
  type        = string
  description = "Password for the Kibana admin user."
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

########################################
# Storage
########################################

variable "data_volume_size_gb" {
  type        = number
  description = "Size of the attached Block Storage volume used for Elasticsearch data. Set 0 to disable."
  default     = 100
}

########################################
# Networking: VPC (optional) and Firewall
########################################

variable "use_vpc" {
  type        = bool
  description = "If true, create a VPC and attach the instance to it. Public IP is still issued (edit if you need pure-private)."
  default     = true
}

variable "vpc_subnet_cidr" {
  type        = string
  description = "VPC subnet CIDR when use_vpc is true."
  default     = "10.42.0.0/24"
}

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
# ILM snapshot repo (Akamai Cloud Object Storage)
########################################

variable "object_storage_bucket" {
  type        = string
  description = "Optional. If set, StackScript registers an S3 snapshot repo targeting this bucket."
  default     = ""
}

variable "object_storage_region" {
  type        = string
  description = "Object Storage region slug for the endpoint, e.g. us-east-1, ap-south-1."
  default     = "us-east-1"
}

variable "object_storage_access_key" {
  type        = string
  description = "Object Storage access key for the snapshot repo."
  sensitive   = true
  default     = ""
}

variable "object_storage_secret_key" {
  type        = string
  description = "Object Storage secret key for the snapshot repo."
  sensitive   = true
  default     = ""
}

########################################
# Let's Encrypt (optional, enables HTTPS on ES HTTP layer)
########################################

variable "enable_lets_encrypt" {
  type        = bool
  default     = false
  description = "If true, StackScript runs certbot --standalone and swaps HTTPS on."
}

variable "public_hostname" {
  type        = string
  default     = ""
  description = "Hostname for Let's Encrypt. Required when enable_lets_encrypt is true."
}
