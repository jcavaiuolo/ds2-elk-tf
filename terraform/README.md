# Terraform: Elasticsearch + Kibana on Akamai Cloud for DataStream 2

Deploys a single-node Elasticsearch + Kibana box on Akamai Cloud (Linode),
locked down with Cloud Firewall rules that only accept the Elasticsearch
`_bulk` endpoint from Akamai DataStream 2 IP ranges, and optionally places it
in a VPC with a Block Storage data volume. Elasticsearch itself is installed
by the StackScript (forked from Hideki Okamoto's original 1059555); this
Terraform module only provisions the infrastructure around it.

## Files

- `providers.tf`, pins `linode/linode` and `hashicorp/random`.
- `variables.tf`, inputs.
- `main.tf`, firewall, optional VPC, instance (invokes StackScript), block volume.
- `outputs.tf`, IP, Reverse DNS hint, Kibana URL, DS2 bulk endpoint.
- `terraform.tfvars.example`, copy to `terraform.tfvars` and edit.

## Usage

```bash
export LINODE_TOKEN=xxxx
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars

terraform init
terraform plan -out tfplan
terraform apply tfplan
```

When the apply completes, Terraform prints:

- `kibana_url`, browse here, log in as `elastic` with `es_admin_password`.
- `elasticsearch_bulk_endpoint`, paste this into DataStream 2 as the destination endpoint.
- `reverse_dns_hint`, the hostname DS2 should reach when HTTPS is off.

The StackScript install runs in the background and takes about 10 minutes.
Watch progress with `ssh ${ssh_user}@${public_ipv4} 'tail -f /root/stackscript.log'`.

## What the firewall allows

| Rule | From | To | Why |
|------|------|----|-----|
| SSH 22/TCP | `allowed_admin_cidrs` | VM | You, not the world |
| Kibana 5601/TCP | `allowed_admin_cidrs` | VM | Your browser |
| ES 9200/TCP | `datastream2_ip_acl` | VM | DS2 `_bulk` push |
| ES 443/TCP | `datastream2_ip_acl` | VM | Only if `enable_lets_encrypt` |
| HTTP 80/TCP | `0.0.0.0/0` | VM | Only if `enable_lets_encrypt` (certbot) |

Everything else inbound is dropped. Egress is open so apt, Elastic repos,
Let's Encrypt, and Object Storage snapshots work.

## Keeping the DS2 IP ACL current

Akamai publishes the DataStream 2 IP ranges in the January 2026 changelog
and refreshes them over time:
https://techdocs.akamai.com/datastream2/changelog/jan-7-2026-ip-acl-support

A good pattern is to fetch the list in CI and feed it into
`datastream2_ip_acl` via a `-var-file` so Terraform resets the firewall on
every pipeline run.

## Production notes

This module intentionally stops at a single node to match the blog post.
For production:

- Multi-node ECK on LKE (Akamai's managed Kubernetes) instead.
- Attach a dedicated BLK volume per data node.
- Put ES in a private VPC subnet and expose the ingest path only through an
  Akamaized hostname property fronted by App & API Protector.
- Enable Terraform remote state (S3-compatible backend on Object Storage).
