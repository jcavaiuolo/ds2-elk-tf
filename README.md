# ds2-elk-tf

2026 refresh of Hideki Okamoto's post [*"Visualizing Akamai DataStream 2 logs with Elasticsearch and Kibana on Linode"*](https://dev.to/hokamoto/visualizing-akamai-datastream-2-logs-with-elasticsearch-and-kibana-2c94), with a Terraform deployer for Akamai Cloud and an extra debug-focused Kibana dashboard.

The goal is plug-and-play. Any SE, Partner or customer can run three commands and get a working stack to analyze DataStream 2 logs end to end.

## What you get

- A single-node Elasticsearch + Kibana box on Akamai Cloud (Linode) provisioned by Terraform.
- Firewall locked to the Akamai Origin IP ACL on port 9200 (the same list DataStream 2 pushes from).
- Hideki Okamoto's StackScript (`1059555`) handles the install, including an ingest pipeline for CMCD and breadcrumbs, an ILM policy, index template, and two pre-built dashboards:
  - `Akamai`, CDN business view.
  - `Akamai Common Media Client Data`, video QoS view.
- Extra `Akamai Debug` dashboard shipped as NDJSON in this repo. Imports in one API call. Covers:
  - Status class and cache HIT/MISS trending.
  - Top 4xx and 5xx by host, method, path.
  - **Client IPs with errors** (click a row to pin that IP across the whole dashboard).
  - Top errorCode, top securityRules, hit ratio by host.
  - Origin RTT p50/p95/p99 filtered to cache MISS.
  - Origin retries, errorCode by host and path, DNS cold lookups, breadcrumbs, non-cacheable paths.
  - Raw log table saved search for drill-down.
  - 5 Options List comboboxes for Host, Status code, Method, Cache status and Client IP.

## Replicate

Full step-by-step is in [DEPLOY.md](DEPLOY.md). TL;DR:

```bash
# 1. Clone
git clone https://github.com/jcavaiuolo/ds2-elk-tf.git
cd ds2-elk-tf

# 2. Terraform
cd terraform
cp terraform.tfvars.example terraform.tfvars    # edit it
export LINODE_TOKEN=...                         # from cloud.linode.com/profile/tokens
terraform init
terraform apply

# 3. Wait ~10 minutes for the StackScript to finish installing ES + Kibana
# Then import the debug dashboard
cd ..
KIBANA_URL=http://$(terraform -chdir=terraform output -raw public_ipv4):5601 \
KIBANA_USER=elastic \
KIBANA_PASS=<your_es_admin_password> \
curl -X POST "$KIBANA_URL/api/saved_objects/_import?overwrite=true" \
     -u "$KIBANA_USER:$KIBANA_PASS" \
     -H 'kbn-xsrf: true' \
     --form file=@kibana/akamai-debug.ndjson

# 4. Configure a DataStream 2 destination pointing at the Terraform output
#    'elasticsearch_bulk_endpoint' with credentials ds2_ingest + ds2_ingest_password
#    (both set in terraform.tfvars).
```

Costs roughly USD 75/month while running (g6-dedicated-4 + 100 GB Block Storage + Backups). `terraform destroy` cleans everything up.

## Layout

| Path | Purpose |
|------|---------|
| `README.md` | This file. Entry point. |
| `DEPLOY.md` | Full deploy walkthrough with troubleshooting. |
| `post.md` | The updated blog post, ready to publish. |
| `queries/esql.md` | Copy-paste pack of ES|QL queries over DS2 CDN indices. |
| `stackscript-fork-notes.md` | Delta plan for when we fork Hideki's StackScript. |
| `terraform/` | Terraform module (providers, variables, main, outputs). |
| `kibana/akamai-debug.ndjson` | Importable Kibana debug dashboard. |
| `scripts/build-debug-dashboard.py` | Regenerates the NDJSON above via the Kibana API. Only needed if you edit the dashboard. |

## Credits

Original architecture, StackScript, ingest pipelines and `Akamai` / `Akamai CMCD` dashboards by [Hideki Okamoto](https://dev.to/hokamoto). This repo keeps his bones, wraps Terraform around them for Akamai Cloud 2026 defaults, and adds the `Akamai Debug` dashboard.
