# ds2-elk-tf

A deploy wrapper for Hideki Okamoto's [*"Visualizing Akamai DataStream 2 logs with Elasticsearch and Kibana on Linode"*](https://dev.to/hokamoto/visualizing-akamai-datastream-2-logs-with-elasticsearch-and-kibana-2c94).

Hideki's StackScript [`1059555`](https://cloud.linode.com/stackscripts/1059555) does the real work: it installs Elasticsearch and Kibana, the DataStream 2 ingest pipelines (CMCD and breadcrumbs), the index template, an ILM policy, the `ds2_ingest` user and two dashboards (`Akamai` and `Akamai Common Media Client Data`). This repo wraps it so that the whole experience is:

```bash
python3 deploy.py
```

answer a few questions (press Enter to accept the value in `[brackets]`, or run `python3 deploy.py --defaults` to take them all), wait about 15 minutes, then paste the printed settings into the DataStream 2 destination form.

## What the wrapper adds

| | |
|---|---|
| **Guided setup** | Asks for everything Terraform needs, with defaults: region, instance type, generated passwords, your public IP for the admin allowlist, an SSH key (created if missing). Answers are saved and become the defaults on the next run. |
| **Locked-down firewall** | SSH and Kibana only from your IP. Elasticsearch only from the Akamai IP ACL, downloaded fresh from Akamai on every deploy. The firewall is attached at creation, so the instance is never open while installing. |
| **Optional data volume** | By default Elasticsearch uses the plan's local disk (already paid for, and faster). If you need more space, the wrapper attaches a Block Storage volume, formats it and mounts it as `/var/lib/elasticsearch`. |
| **Retention** | Hideki's ILM policy never deletes, so the disk eventually fills up. The wrapper adds a delete phase (7 days by default). |
| **Optional HTTPS** | nginx + Let's Encrypt on 443 in front of Elasticsearch, so DataStream 2 credentials and logs travel encrypted. |
| **Akamai Debug dashboard** | 15 extra panels for troubleshooting, imported automatically (see below). |
| **ES\|QL pack** | [`docs/esql-queries.md`](docs/esql-queries.md), copy-paste queries that match Hideki's field names. |

## Prerequisites

- `terraform` >= 1.6, `python3` >= 3.8, `ssh`, `scp`, `ssh-keygen`.
- A Linode Personal Access Token with Read/Write on Linodes, Firewalls and Volumes, and Read on StackScripts: https://cloud.linode.com/profile/tokens. The wrapper reads `LINODE_TOKEN`, then your `linode-cli` config, and otherwise asks for it (never saved to disk).
- Network access from your machine to `techdocs.akamai.com` (the IP ACL download).

## 1. Deploy

```bash
git clone https://github.com/jcavaiuolo/ds2-elk-tf.git
cd ds2-elk-tf
python3 deploy.py
```

What it asks:

| Question | Default |
|----------|---------|
| Label, region, instance type | `ds2-elk`, `us-east`, `g6-dedicated-4` (8 GB) |
| Extra data volume | `0` (data on the plan's local disk); a size in GB adds a Block Storage volume |
| Linode Backups | no |
| Tags | `ds2,elasticsearch,kibana` |
| CIDRs allowed to SSH and Kibana | your current public IP `/32` |
| SSH key | `~/.ssh/ds2-elk-tf` (generated if missing) |
| SSH sudo user and its password | `elkadmin`, generated |
| Root password | generated |
| `elastic` password (Kibana login) | generated |
| DataStream 2 ingest user and password | `ds2_ingest`, generated |
| Retention | `7` days (`0` = keep forever) |
| Import the Akamai Debug dashboard | yes |
| HTTPS with Let's Encrypt | no; if yes: hostname (empty = the instance's reverse DNS name) and an optional email |

Passwords are limited to letters, digits, `_` and `-`, because the StackScript pastes them into JSON and shell strings without escaping.

Then it:

1. Shows a review and runs `terraform init`, `plan`, and (after you confirm) `apply`. That creates the firewall, the instance running Hideki's StackScript, and the volume if you asked for one.
2. Waits for the StackScript to finish, about 10 minutes. It prints an `ssh ... tail -f /var/log/stackscript.log` command if you want to watch.
3. Runs [`remote/post-install.sh`](remote/post-install.sh) on the instance over SSH (with sudo). It moves the Elasticsearch data onto the volume (if any), applies retention, sets up HTTPS if requested and imports the Akamai Debug dashboard. Every step is idempotent.
4. Prints the Kibana URL and login, the SSH command, and the DataStream 2 destination settings.

Settings and passwords are saved in `terraform/terraform.tfvars.json` and `terraform/deploy.local.json` (gitignored, `chmod 600`). Keep a copy in your password manager.

## 2. Configure the DataStream 2 stream

Akamai Control Center -> COMMON SERVICES -> DataStream -> Create stream. Use the values `deploy.py` printed (`python3 deploy.py summary` shows them again):

1. Log type: `CDN`. Pick the delivery properties.
2. Data sets: include all for a first install, trim later. Respect PII.
3. Format: `JSON`.
4. Destination: `Elasticsearch`.
   - Endpoint: `http://<reverse-dns>:9200/_bulk`, or `https://<hostname>/_bulk` with HTTPS.
   - Index name: `datastream2`.
   - User name / Password: the DS2 ingest user and password.
   - Send compressed data: `ON`.
5. **Validate & Save** reports a failure because the validator probes from a Control Center IP that is not in the ACL. Click **Skip validation**.
6. Tick "Activate stream upon saving". Activation takes about 90 minutes.

## 3. Enable DataStream in Property Manager

In the property fronting your traffic, default rule:

- Add the `DataStream` behavior: stream version `v2`, your stream, sampling rate `100` for the demo, **Log Akamai Edge Server IP Address `ON`** (required).
- Add the `Log Request Details` behavior.
- Activate on Staging, then Production.

## 4. Verify

Send a few requests (`curl -v https://<hostname>/`). After 1 to 2 minutes:

- Discover, `datastream2` data view: raw rows.
- `Akamai` dashboard: business panels.
- `Akamai Debug` dashboard: trending panels. Origin retries, DNS cold lookups and Multi-hop breadcrumbs may stay empty. That means the CDN is healthy, not that something is broken.

To exercise the error panels:

```bash
for i in {1..10}; do curl -s -o /dev/null https://<hostname>/intentional-404-$i; done
curl -s -o /dev/null -X TRACE https://<hostname>/
```

## Commands

| Command | What it does |
|---------|--------------|
| `python3 deploy.py` | Ask, apply, post-install, summary. Re-run it to change settings (it reuses your previous answers). |
| `python3 deploy.py --defaults` (`-d`) | Same, but takes every default without asking and skips the confirmations: previous answers if any, otherwise the defaults in the table above, generated passwords and your detected public IP. Needs `LINODE_TOKEN` or a `linode-cli` config. |
| `python3 deploy.py post-install` | Re-run only the post-install step, for example after fixing DNS for HTTPS. |
| `python3 deploy.py summary` | Print the Kibana and DataStream 2 settings again. |
| `python3 deploy.py destroy` | `terraform destroy`. The DataStream 2 stream and Property Manager behaviors are not managed here; remove them in Control Center. |

## The Akamai Debug dashboard

Imported as a data view `akamai-debug` (same indices as Hideki's), a saved search `Akamai Debug: raw log table` and a dashboard with 5 filter comboboxes (Host, Status code, Method, Cache status, Client IP):

- Status class and cache HIT/MISS over time.
- Top 4xx and 5xx by host, method, path.
- Client IPs with errors (click a row to pin that IP across the dashboard).
- Top errorCode, top securityRules, hit ratio by host.
- Turnaround p50/p95/p99 on cache MISS.
- Origin retries, errorCode by host and path, DNS cold lookups, breadcrumbs, non-cacheable paths.
- Raw log table for drill-down.

To change it, edit and run `tools/build-debug-dashboard.py` against a running Kibana. It re-creates the objects through the API and re-exports `kibana/akamai-debug.ndjson`:

```bash
KIBANA_URL=http://$(terraform -chdir=terraform output -raw public_ipv4):5601 \
KIBANA_USER=elastic KIBANA_PASS=<es_admin_password> \
python3 tools/build-debug-dashboard.py
```

## HTTPS

Without HTTPS (the default, same as Hideki's setup), DataStream 2 posts logs and its basic-auth credentials over plain HTTP to port 9200. Only Akamai's ranges can reach that port.

With HTTPS:

- The post-install installs nginx and certbot and gets a Let's Encrypt certificate via HTTP-01 (webroot). nginx then serves `https://<hostname>/` on 443 and proxies to Elasticsearch on localhost.
- The firewall closes 9200, opens 443 to the Akamai ACL and opens 80 to everyone, because Let's Encrypt validates from undisclosed IPs, both at issuance and on every renewal. nginx answers only ACME challenges on port 80. Renewals run from certbot's systemd timer and reload nginx.
- Hostname: leave it empty to use the instance's reverse DNS name (`<ip-dashed>.ip.linodeusercontent.com`), which needs no DNS work. For your own name, `deploy.py` prints the A record to create after `apply` and waits until it resolves.
- Kibana itself stays on HTTP 5601, reachable only from your admin CIDRs.

## DataStream 2 IP ACL

DataStream 2 pushes from the same ranges Akamai publishes for Origin IP ACL. Terraform downloads them on every `plan`:

- https://techdocs.akamai.com/property-manager/pdfs/akamai_ipv4_CIDRs.txt
- https://techdocs.akamai.com/property-manager/pdfs/akamai_ipv6_CIDRs.txt

If Akamai changes the list, the next `python3 deploy.py` shows the firewall diff in the plan and applies it. Subscribe to the Firewall Rules Notification in Control Center to know when to re-run. To pin your own list instead, set `datastream2_ip_acl` in `terraform/terraform.tfvars.json`.

## Sizing

Rough rule of thumb, based on about 1 to 1.5 KB uncompressed per DS2 CDN log document (CMCD and breadcrumbs add about 30%):

| Sustained RPS at edge | Daily raw log volume | Instance type (local disk) | Extra volume, 7-day retention |
|-----------------------|----------------------|----------------------------|-------------------------------|
| < 50 | 1-2 GB | `g6-dedicated-4` (8 GB RAM, 160 GB) | none (default) |
| 50-200 | 5-20 GB | `g6-dedicated-4` (8 GB RAM, 160 GB) | none up to ~15 GB/day, else 100-200 GB |
| 200-500 | 20-50 GB | `g6-dedicated-8` (16 GB RAM, 320 GB) | 200 GB at the top of the range |
| 500-2000 | 50-200 GB | multi-node, 32 GB RAM each | 500 GB each |
| > 2000 | 200+ GB | LKE + ECK | per-node |

The plan's local disk comes with the instance price and is faster than network Block Storage, so use it first. Keep about 25% free: Elasticsearch stops allocating shards above its 85% disk watermark. Add a volume only when the data outgrows the local disk (longer retention, higher RPS) and you don't want a bigger plan.

Re-run `python3 deploy.py` with a bigger instance type or volume size. Adding a volume to an existing deploy moves the data onto it. Going back to `0` deletes the volume and the logs on it (the wrapper asks first). Linode resizes volumes online, but you then grow the ext4 filesystem by hand (`sudo resize2fs /dev/disk/by-id/scsi-0Linode_Volume_<label>-data`). The single-node rows are what this repo deploys. Above that, see `docs/blog-post.md`. The sampling rate in the DataStream behavior is the fastest escape valve: 10 or 25 instead of 100 cuts volume linearly.

## Retention

Hideki's `datastream2-ilm` policy rolls the write index over at 30 days or 50 GB and never deletes anything. With retention set to N days, the post-install rewrites the policy to roll over daily (or at 10 GB) and delete each index N days after its rollover. Disk usage then stays at roughly N+1 days of logs.

| Use case | Retention |
|----------|-----------|
| Live debug stack | 7 days (default) |
| Weekly report feed | 30 days |
| Compliance archive | 0 (keep forever) and snapshot to Object Storage |

To change it, re-run `python3 deploy.py` (or `post-install`) with a new value. Setting `0` later does not restore Hideki's original policy.

## Using Terraform directly

`terraform/` is a plain module, and `deploy.py` only writes `terraform/terraform.tfvars.json` and calls it. Required variables: `root_password`, `ssh_user_password`, `es_admin_password`, `ds2_ingest_password`, `allowed_admin_cidrs`, plus `authorized_keys` with your public key. See `terraform/variables.tf` for the rest. The post-install step (volume, retention, HTTPS, dashboard) still needs `python3 deploy.py post-install`, which reads the same `terraform.tfvars.json`.

## Troubleshooting

- **"unauthorized" from Terraform**: the token is missing or expired. `deploy.py` checks it against the Linode API before starting.
- **"Could not download the Akamai IP ACL"**: `techdocs.akamai.com` was unreachable from your machine. Retry, or pin `datastream2_ip_acl`.
- **"The requested distribution is not supported by this stackscript"**: StackScript 1059555 only supports `linode/ubuntu22.04`, the module's default image.
- **Timed out waiting for the StackScript**: SSH in and read `/var/log/stackscript.log`. The usual cause is too little RAM; use `g6-dedicated-8`. Then run `python3 deploy.py post-install`.
- **Post-install failed**: it is idempotent. Fix the cause shown in the output and run `python3 deploy.py post-install`.
- **Let's Encrypt fails**: the hostname must resolve to the instance and port 80 must be reachable. Check `sudo journalctl -u nginx` and `/var/log/letsencrypt/letsencrypt.log`.
- **DataStream 2 shows "100% Uploads failed" right after activating**: the counter covers the last 24 hours and recovers as successes accumulate. Check that documents are arriving:
  ```bash
  ssh -i ~/.ssh/ds2-elk-tf elkadmin@<ip> \
      'curl -s -u elastic:<es_admin_password> http://localhost:9200/datastream2*/_count'
  ```
- **Pushes come from IPs outside the ACL**: pin `datastream2_ip_acl` to `["0.0.0.0/0", "::/0"]` briefly, capture with `tcpdump` on the instance, then remove the pin.
- **Kibana login loops**: `sudo /usr/share/elasticsearch/bin/elasticsearch-reset-password -u elastic`, then put the new password in `terraform.tfvars.json`.

## Layout

| Path | Purpose |
|------|---------|
| `deploy.py` | The interactive wrapper. Python standard library only. |
| `terraform/` | Firewall, instance (Hideki's StackScript) and optional data volume. |
| `remote/post-install.sh` | Runs on the instance after the StackScript: volume, retention, HTTPS, dashboard import. |
| `kibana/akamai-debug.ndjson` | The Akamai Debug dashboard. |
| `tools/build-debug-dashboard.py` | Regenerates the NDJSON above. Only needed to edit the dashboard. |
| `docs/esql-queries.md` | ES\|QL query pack for Hideki's index. |
| `docs/blog-post.md` | The updated blog post: background, sizing deep dive, hardening. |

## Credits

Original architecture, StackScript, ingest pipelines and the `Akamai` / `Akamai Common Media Client Data` dashboards by [Hideki Okamoto](https://dev.to/hokamoto). This repo only wraps his work for a one-command deploy and adds the `Akamai Debug` dashboard.
