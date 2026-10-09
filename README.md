# ds2-elk-tf

2026 refresh of Hideki Okamoto's post [*"Visualizing Akamai DataStream 2 logs with Elasticsearch and Kibana on Linode"*](https://dev.to/hokamoto/visualizing-akamai-datastream-2-logs-with-elasticsearch-and-kibana-2c94), with a Terraform deployer for Akamai Cloud and an extra debug-focused Kibana dashboard.

Plug-and-play: any SE, Partner or customer can clone, run `terraform apply`, import one NDJSON, and get a working stack to analyze DataStream 2 logs end to end.

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

## Prereqs

On your machine: `terraform` >= 1.6, `ssh`, `curl`, and Python 3 if you plan to regenerate the dashboard NDJSON. `linode-cli` is handy but optional.

## 1. Clone

```bash
git clone https://github.com/jcavaiuolo/ds2-elk-tf.git
cd ds2-elk-tf
```

## 2. Get a Linode Personal Access Token

1. Log in to https://cloud.linode.com/profile/tokens
2. Create a Personal Access Token with Read/Write on:
   - Linodes
   - Firewalls
   - Volumes
   - StackScripts (Read)
3. Export it:
   ```bash
   export LINODE_TOKEN=xxxxxxxxxxxxxxxx
   ```
4. If you already use `linode-cli`, the Terraform provider can read your CLI token too:
   ```bash
   export LINODE_TOKEN=$(awk -F '[ =]+' '/^token *=/{print $2; exit}' ~/.config/linode-cli)
   ```
   You can stash that one-liner in `~/.zshrc`.

## 3. Create an SSH key for the deploy (optional but recommended)

A dedicated key per project keeps blast radius small:

```bash
ssh-keygen -t ed25519 -N '' -C "ds2-elk-tf@$(hostname)" -f ~/.ssh/ds2-elk-tf
```

The public key path (`~/.ssh/ds2-elk-tf.pub`) goes into `terraform.tfvars` below.

## 4. Fill `terraform.tfvars`

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit every `CHANGE-ME` value. Minimum:

- `root_password`, `ssh_user_password`, `es_admin_password`, `ds2_ingest_password`: four strong passwords. Easy generator: `openssl rand -base64 24 | tr -d '/+='`.
- `authorized_keys`: paste the contents of `~/.ssh/ds2-elk-tf.pub`.
- `allowed_admin_cidrs`: your public IP in `/32` form. Get it with `curl ifconfig.me`.
- `region`, `instance_type`: sensible defaults are `us-east` and `g6-dedicated-4` (8 GB RAM). See [Sizing](#sizing) below for larger traffic.
- `label`, `ssh_user`: cosmetic.

Save the passwords in a password manager now, the tfvars file is `gitignored` for a reason.

## 5. Apply the Terraform

```bash
terraform init
terraform plan -out tfplan
terraform apply tfplan
```

Expect 3 resources: `linode_firewall`, `linode_instance`, `linode_volume`. VPC is not used by default because older Akamai Cloud regions (such as `us-east`) do not support it. The firewall accepts:

| Rule | From | To | Why |
|------|------|----|-----|
| SSH 22/TCP | `allowed_admin_cidrs` | VM | You |
| Kibana 5601/TCP | `allowed_admin_cidrs` | VM | Your browser |
| ES 9200/TCP | `datastream2_ip_acl` | VM | DS2 push ingest |

Terraform prints outputs like:

```
elasticsearch_bulk_endpoint = "http://<rdns>:9200/_bulk"
kibana_url                  = "http://<ip>:5601/"
public_ipv4                 = "x.x.x.x"
reverse_dns_hint            = "<ip-dashed>.ip.linodeusercontent.com"
```

Save the Reverse DNS value, DataStream 2 needs it.

## 6. Wait for the StackScript to finish

The compute instance is `running` right away but Hideki's StackScript (`1059555`) runs for about 10 minutes inside, installing Elasticsearch, Kibana, index templates, ingest pipelines, and importing his dashboards. Monitor:

```bash
IP=$(terraform output -raw public_ipv4)
ssh -i ~/.ssh/ds2-elk-tf <ssh_user>@$IP 'sudo tail -f /var/log/stackscript.log'
```

You are done when the log ends with a Kibana saved objects import success JSON (~25 objects created). Ctrl+C to leave the tail.

Sanity check Elasticsearch from the box (the Cloud Firewall allows 9200 only to the DS2 ACL, not your admin IP, so curl from your laptop will not reach it):

```bash
ssh -i ~/.ssh/ds2-elk-tf <ssh_user>@$IP \
    'curl -s -u elastic:<es_admin_password> http://localhost:9200/_cluster/health?pretty'
```

Expect `status: yellow` (normal on a single-node cluster, the replica of the write index has nowhere to go).

Open Kibana in your browser at the `kibana_url` output. Log in as `elastic` with the Elasticsearch password from `terraform.tfvars`.

## 7. Import the `Akamai Debug` dashboard

The repo ships `kibana/akamai-debug.ndjson`. Import it in one call from the repo root:

```bash
IP=$(terraform -chdir=terraform output -raw public_ipv4)
curl -s -u elastic:<es_admin_password> \
     -H 'kbn-xsrf: true' \
     -X POST "http://$IP:5601/api/saved_objects/_import?overwrite=true" \
     --form file=@kibana/akamai-debug.ndjson
```

Or in the UI: **Stack Management -> Saved Objects -> Import** -> pick `kibana/akamai-debug.ndjson`.

You will see a new data view `akamai-debug`, a saved search `Akamai Debug: raw log table`, and a dashboard `Akamai Debug` with 15 panels and 5 Options List combobox filters (Host, Status code, Method, Cache status, Client IP) at the top.

## 8. Configure the DataStream 2 destination

Akamai Control Center -> COMMON SERVICES -> DataStream -> Create a stream.

1. Log type: `CDN`.
2. Pick the delivery properties.
3. Data sets: Include all for a first install, trim later. Respect PII.
4. Format: `JSON`.
5. Destination:
   - Destination: `Elasticsearch`
   - Display name: your choice
   - Endpoint: `http://<reverse_dns_hint>:9200/_bulk` (from Terraform outputs)
   - Index name: `datastream2`
   - User name: `ds2_ingest`
   - Password: value of `ds2_ingest_password` from `terraform.tfvars`
   - Send compressed data: `ON`
6. Click **Validate & Save**.

> **Validation note.** The Control Center validator does a probe push from an internal Control Center IP that is not in the Origin IP ACL. You will see "Validation failed" with a dialog suggesting Skip. **Click Skip validation.** The actual DS2 push IPs (edge-side, in the Origin IP ACL) will work fine.

7. Tick "Activate stream upon saving". Activation takes about 90 minutes.

## 9. Enable DataStream in Property Manager

In the delivery property fronting your traffic:

- Add `DataStream` and `Log Request Details` behaviors to the default rule.
- Stream version: `v2`.
- Stream names: the stream you created.
- Sampling rate: `100` for the demo.
- Log Akamai Edge Server IP Address: `ON` (required).
- Save and activate on **Staging** first, then **Production**.

## 10. Verify

Generate a few requests against your property (`curl -v https://<hostname>/`). After 1 to 2 minutes you should see docs in Kibana:

- Discover with the `datastream2` or `akamai-debug` data view -> raw rows appear.
- Dashboard `Akamai` -> business panels light up.
- Dashboard `Akamai Debug` -> trending panels light up. Panels like Origin retries, DNS cold lookups and Multi-hop breadcrumbs may stay empty, which is a **positive health signal**, not a bug.

Generate error traffic to exercise the debug dashboard:

```bash
for i in {1..10}; do curl -s -o /dev/null https://<hostname>/intentional-404-$i; done
curl -s -o /dev/null -X TRACE https://<hostname>/
```

In 1 to 2 minutes the `Top 4xx` table and the `errorCode by host and path` table will populate.

## Sizing

Rough rule of thumb based on ~1-1.5 KB uncompressed per DS2 CDN log doc (CMCD and breadcrumbs add ~30%):

| Sustained RPS at edge | Daily raw log volume | Instance type | Block storage |
|-----------------------|----------------------|---------------|----------------|
| < 50 | 1-2 GB | `g6-dedicated-4` (8 GB) | 50 GB |
| 50-200 | 5-20 GB | `g6-dedicated-4` | 100 GB (default) |
| 200-500 | 20-50 GB | `g6-dedicated-8` (16 GB) | 200 GB |
| 500-2000 | 50-200 GB | multi-node, 32 GB each | 500 GB each |
| > 2000 | 200+ GB | LKE + ECK | per-node |

Change `instance_type` and `data_volume_size_gb` in `terraform.tfvars` and re-apply. Sampling rate in the DataStream behavior is your fastest escape valve: 10 or 25 instead of 100 cuts volume linearly.

See `post.md` for the deep dive and when to graduate to ECK on LKE.

## Retention

Hideki's StackScript ships an ILM policy with a `rollover` action but **no `delete` phase**, so indices grow forever. For a debug stack, 7 days of hot data covers almost every "what happened at 3am" question. Apply this policy once:

```bash
IP=$(terraform -chdir=terraform output -raw public_ipv4)
ssh -i ~/.ssh/ds2-elk-tf <ssh_user>@$IP \
    'curl -s -u elastic:<es_admin_password> \
         -H "Content-Type: application/json" \
         -X PUT "http://localhost:9200/_ilm/policy/datastream2-ilm" \
         -d "{\"policy\":{\"phases\":{\"hot\":{\"min_age\":\"0ms\",\"actions\":{\"set_priority\":{\"priority\":100},\"rollover\":{\"max_age\":\"1d\",\"max_primary_shard_size\":\"10gb\"}}},\"delete\":{\"min_age\":\"7d\",\"actions\":{\"delete\":{}}}}}}"'
```

Hot indices roll over daily (or at 10 GB), old indices get deleted after 7 days. Tune the window:

| Use case | Hot rollover | Delete after |
|----------|--------------|--------------|
| Live debug stack | 1 day or 10 GB | 7 days (default I recommend) |
| Weekly report feed | 7 days or 50 GB | 30 days |
| Compliance archive | 1 day or 10 GB | never (snapshot repo to Object Storage) |

## Troubleshooting

- **Terraform error "unauthorized"**: `LINODE_TOKEN` not exported, or expired.
- **Terraform error "This region does not support VPCs at this time"**: the Terraform module no longer uses VPCs by default, update to the current version.
- **Terraform error "The requested distribution is not supported by this stackscript"**: Hideki's StackScript 1059555 is pinned to `linode/ubuntu22.04`. The module's default matches. If you overrode `image`, revert.
- **DataStream 2 shows "100% Uploads failed" right after activating**: the Cloud Firewall ACL may have been updated after the stream was activated. The last 7/N count in Control Center tracks the past 24h, it recovers as new successes accumulate. Confirm pushes are landing by checking the ES doc count:
  ```bash
  ssh -i ~/.ssh/ds2-elk-tf <ssh_user>@$IP \
      'curl -s -u elastic:<pw> http://localhost:9200/datastream2*/_count'
  ```
- **DS2 push IPs look different from the ACL**: open the firewall briefly to `0.0.0.0/0`, capture with tcpdump on the server to see the actual push IPs, then narrow back down:
  ```bash
  export LINODE_TOKEN=...
  terraform -chdir=terraform apply -var='datastream2_ip_acl=["0.0.0.0/0","::/0"]' -auto-approve
  # ... capture / verify ...
  terraform -chdir=terraform apply -auto-approve   # reverts to the ACL default
  ```
- **"Validation failed" during the DS2 destination test**: click **Skip validation**. The validator probes from a Control Center IP that is not in the Origin IP ACL, this is expected.
- **StackScript stuck**: `systemctl status elasticsearch kibana`. Check `/var/log/elasticsearch/*.log`. Common cause is too little RAM, bump the `instance_type` to `g6-dedicated-8`.
- **Kibana login loops**: reset the admin password on the box:
  ```bash
  sudo /usr/share/elasticsearch/bin/elasticsearch-reset-password -u elastic
  ```
- **DS2 ACL has to be refreshed**: Akamai publishes the current list at https://techdocs.akamai.com/property-manager/pdfs/akamai_ipv4_CIDRs.txt and `..._ipv6_CIDRs.txt`. Replace the `datastream2_ip_acl` default and re-apply.

## Regenerate the debug dashboard

Only needed if you want to edit the dashboard definitions:

```bash
KIBANA_URL=http://$(terraform -chdir=terraform output -raw public_ipv4):5601 \
KIBANA_USER=elastic \
KIBANA_PASS=<es_admin_password> \
python3 scripts/build-debug-dashboard.py
```

The script creates the saved objects via the Kibana API, then exports them back to `kibana/akamai-debug.ndjson`. Commit the regenerated file so others see your changes.

## Teardown

```bash
cd terraform
terraform destroy
```

Removes the instance, firewall, and Block Storage volume. The DataStream 2 stream and the Property Manager changes are not managed by Terraform, delete them from Control Center manually.

## Layout

| Path | Purpose |
|------|---------|
| `README.md` | This file. Full walkthrough. |
| `post.md` | The updated blog post, ready to publish. Deeper narrative, sizing deep dive, ES|QL section. |
| `queries/esql.md` | Copy-paste pack of ES|QL queries over DS2 CDN indices. |
| `terraform/` | Terraform module (providers, variables, main, outputs). |
| `kibana/akamai-debug.ndjson` | Importable Kibana debug dashboard. |
| `scripts/build-debug-dashboard.py` | Regenerates the NDJSON above via the Kibana API. Only needed if you edit the dashboard. |

## Credits

Original architecture, StackScript, ingest pipelines and `Akamai` / `Akamai CMCD` dashboards by [Hideki Okamoto](https://dev.to/hokamoto). This repo keeps his bones, wraps Terraform around them for Akamai Cloud 2026 defaults, and adds the `Akamai Debug` dashboard.
