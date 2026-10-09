output "instance_id" {
  description = "Linode instance ID."
  value       = linode_instance.elk.id
}

output "public_ipv4" {
  description = "Public IPv4 of the compute instance."
  value       = local.public_ipv4
}

output "ipv6" {
  description = "Public IPv6."
  value       = linode_instance.elk.ipv6
}

output "reverse_dns_hint" {
  description = "Default reverse DNS hostname of the instance."
  value       = local.rdns_host
}

output "ssh_user" {
  description = "Limited sudo user created by the StackScript."
  value       = var.ssh_user
}

output "kibana_url" {
  description = "URL for the Kibana UI."
  value       = "http://${local.public_ipv4}:${var.kibana_port}/"
}

output "enable_tls" {
  description = "Whether the DS2 endpoint is served over HTTPS."
  value       = var.enable_tls
}

output "tls_hostname" {
  description = "Hostname the Let's Encrypt certificate is issued for (only meaningful when enable_tls)."
  value       = local.tls_hostname
}

output "elasticsearch_bulk_endpoint" {
  description = "DataStream 2 destination endpoint. Point your stream here."
  value = (
    var.enable_tls
    ? "https://${local.tls_hostname}/_bulk"
    : "http://${local.rdns_host}:${var.elasticsearch_port}/_bulk"
  )
}

output "datastream2_ip_acl" {
  description = "Ranges allowed to reach the ingest endpoint."
  value       = local.ds2_acl
}

output "firewall_id" {
  description = "Cloud Firewall ID."
  value       = linode_firewall.elk.id
}

output "volume_id" {
  description = "Block Storage volume ID (null if data_volume_size_gb = 0)."
  value       = var.data_volume_size_gb > 0 ? linode_volume.es_data[0].id : null
}

output "volume_filesystem_path" {
  description = "Device path of the data volume inside the instance (null if none)."
  value       = var.data_volume_size_gb > 0 ? linode_volume.es_data[0].filesystem_path : null
}
