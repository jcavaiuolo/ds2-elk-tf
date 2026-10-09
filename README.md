# datastream2-elk-blog

Companion assets for the 2026 refresh of Hideki Okamoto's post
["Visualizing Akamai DataStream 2 logs with Elasticsearch and Kibana on Linode"](https://dev.to/hokamoto/visualizing-akamai-datastream-2-logs-with-elasticsearch-and-kibana-2c94).

## Layout

- `post.md`, the updated blog post (markdown).
- `queries/esql.md`, copy-paste pack of ES|QL queries over DataStream 2 CDN indices.
- `stackscript-fork-notes.md`, plan for forking Hideki's StackScript to 2026 defaults.
- `terraform/`, Terraform module that provisions the single-node Elasticsearch + Kibana box on Akamai Cloud, locked to the DataStream 2 IP ACL ranges.

## Quick deploy

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # edit it
export LINODE_TOKEN=xxxx
terraform init
terraform apply
```

See `terraform/README.md` for the full walkthrough.

## Credits

Original architecture, StackScript, and dashboard shape by
[Hideki Okamoto](https://dev.to/hokamoto). This repo keeps his bones and adds
four years of DataStream 2, Elastic, and Akamai Cloud evolution.
