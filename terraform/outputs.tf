output "instance_id" {
  description = "Linode instance ID."
  value       = linode_instance.elk.id
}

locals {
  public_ipv4 = tolist(linode_instance.elk.ipv4)[0]
  rdns_host   = "${replace(local.public_ipv4, ".", "-")}.ip.linodeusercontent.com"
}

output "public_ipv4" {
  description = "Public IPv4 of the compute instance. Also what the Reverse DNS resolves to for the DS2 endpoint."
  value       = local.public_ipv4
}

output "ipv6" {
  description = "Public IPv6."
  value       = linode_instance.elk.ipv6
}

output "reverse_dns_hint" {
  description = "Suggested Reverse DNS hostname for the DS2 destination endpoint."
  value       = local.rdns_host
}

output "kibana_url" {
  description = "URL for the Kibana UI."
  value       = "http://${local.public_ipv4}:${var.kibana_port}/"
}

output "elasticsearch_bulk_endpoint" {
  description = "DataStream 2 destination endpoint. Point your stream here."
  value = (
    var.enable_lets_encrypt && var.public_hostname != ""
    ? "https://${var.public_hostname}/_bulk"
    : "http://${local.rdns_host}:${var.elasticsearch_port}/_bulk"
  )
}

output "firewall_id" {
  description = "Cloud Firewall ID."
  value       = linode_firewall.elk.id
}

output "volume_id" {
  description = "Block Storage volume ID (null if data_volume_size_gb = 0)."
  value       = var.data_volume_size_gb > 0 ? linode_volume.es_data[0].id : null
}
