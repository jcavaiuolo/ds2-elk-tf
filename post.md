---
title: "Visualizing Akamai DataStream 2 logs with Elasticsearch and Kibana on Akamai Cloud (2026 refresh)"
date: 2026-10-09
tags: [akamai, elasticsearch, kibana, datastream2, akamaicloud, esql, cmcd]
canonical_original: "https://dev.to/hokamoto/visualizing-akamai-datastream-2-logs-with-elasticsearch-and-kibana-2c94"
---

> This post is a 2026 refresh of Hideki Okamoto's excellent 2022 walkthrough,
> ["Visualizing Akamai DataStream 2 logs with Elasticsearch and Kibana on Linode"][orig].
> The original pipeline still works, but a lot has shifted in four years: Linode is now
> Akamai Cloud, DataStream 2 added destinations and native security features, Elastic
> relicensed again, and Kibana shipped ES|QL. I kept Hideki's structure where it holds
> up and rewrote the rest. All credit for the original architecture and StackScript goes
> to him.

[orig]: https://dev.to/hokamoto/visualizing-akamai-datastream-2-logs-with-elasticsearch-and-kibana-2c94

## Why republish?

Four things changed since late 2023 that make the original worth revisiting:

1. **Linode is now Akamai Cloud Computing.** Same hypervisor, same StackScripts, new name and a lot of new primitives (VPC, Cloud Firewall, LKE Enterprise, Object Storage with S3 compatibility, more regions).
2. **DataStream 2 got better and bigger.** New destinations, native IP access lists (January 2026), Akamaized hostname support so you can proxy the log stream through your own property, mTLS, and dedicated log types for Security, EdgeWorkers, Edge DNS and GTM.
3. **Elastic shipped ES|QL.** It is now the easiest way to query DS2 logs, replacing most of the point-and-click KQL + Lens juggling for ad-hoc analysis.
4. **Kibana dashboards aged.** Hideki's "Akamai" dashboard was great for the CDN fields in 2022. In 2026 we want views for WAF/Bot/Account Protector, API Protector, EdgeWorkers runtime, and CMCD v2 for video.

Everything else, the choice of ELK as a visualization layer, the StackScript install pattern, the custom field trick with EdgeWorkers, the WAF correlation, is still a very reasonable 2026 pattern.

## What is DataStream 2 in 2026

DataStream 2 is still the free, near real-time log stream from the Akamai Intelligent Edge Platform. The headline changes versus the original post:

**More destinations.** As of this writing, DS2 pushes to Amazon S3, Azure Storage, Datadog, **Dynatrace**, Elasticsearch, Google Cloud Storage, Loggly, New Relic, Oracle Cloud, S3-compatible (including Akamai Cloud Object Storage), Splunk, Sumo Logic, **TrafficPeak**, and custom HTTPS. Dynatrace and TrafficPeak are new since Hideki's post. See the [destinations reference][dest] for the current feature matrix (JSON vs structured, custom headers, mTLS, dynamic variables, Akamaized hostname support).

**Separate log types.** You now choose a log type when you create a stream: `CDN`, `EdgeWorkers`, `Edge DNS`, `GTM`, or `SIEM` for Application Security events (App & API Protector, Kona Site Defender, Client Reputation, Web Application Protector, Bot Manager, Account Protector). The old post implicitly covered CDN; in this refresh we touch Security too.

**Field catalog grew.** The 45 CDN fields of 2022 kept growing. Rather than hardcoding a number, discover the current catalog via the API:

```
GET /datastream-config-api/v3/log/{logType}/datasets-fields
```

That returns the `datasetFieldId` and `datasetFieldName` list for the log type and optional product. Hit it with CLI or Postman while planning your fields.

**Native security on the destination.** Three big ones:

- **IP ACLs** (January 2026). DS2 publishes the same IP ranges used by Origin IP ACLs for the CDN. You put them in your Cloud Firewall and only accept the bulk endpoint from Akamai, no more open ES on the public internet. [Changelog][ip-acl].
- **Akamaized hostname endpoints.** Point DS2 to a hostname served by one of your own properties and let Akamai edge sit in front of your Elasticsearch endpoint. Great for TLS termination, WAF, and rate limiting the ingest path.
- **mTLS auth** on supported destinations. Elasticsearch destination supports custom headers for auth; combine with mTLS and the Akamaized hostname and you have a defensible path end to end.

**Push cadence.** Logs can now ship as often as every 30 seconds.

> **Sidebar: do you actually need to run Elasticsearch?** DS2 has shipped a
> native **TrafficPeak** destination. TrafficPeak is Akamai's own observability
> product, and if your use case is "visualize CDN access logs" rather than
> "own my log analytics stack", TrafficPeak gives you dashboards, retention and
> query without a node to patch. The rest of this post assumes you want ELK on
> your own box (for licensing control, custom analysis, or because you already
> have Elastic skills on the team), but TrafficPeak is the shortest path when
> that is not a hard requirement.

[dest]: https://techdocs.akamai.com/datastream2/reference/destinations
[ip-acl]: https://techdocs.akamai.com/datastream2/changelog/jan-7-2026-ip-acl-support

## What is Elastic/Kibana in 2026

Short version: still the right tool for ad-hoc visualization of DS2.

Licensing: Elasticsearch and Kibana are offered under the **SSPL**, the **Elastic License v2**, and (since August 2024) the **AGPLv3**. Pick whichever fits your redistribution needs. For an internal observability stack this is a non-issue.

Versions: Elasticsearch 8 is still the common deployment and 9 is current. The two most useful 2024/2025 changes for DS2 users are:

- **ES|QL** (Elasticsearch Query Language), a pipe-based query language that reads like a mix of SPL and Kusto. We will use it below.
- **Security on by default** since 8.0. If you reuse Hideki's StackScript note that the script explicitly disables TLS on the HTTP layer to keep DS2 destination setup simple. In 2026 we fix that properly with Let's Encrypt or an Akamaized hostname (next section).

## Three ways to deploy

Pick the one that matches how your team ships infra. All three land on the same stack and expose the same Kibana URL and the same Elasticsearch `_bulk` endpoint.

### Path A: Akamai Cloud Marketplace (10 minute click-through)

The Akamai Cloud Marketplace has one-click apps for Elasticsearch and Kibana. Pick a Dedicated 8 GB compute instance in a region close to your origin, let the Marketplace install run, then jump to the "Configure DataStream 2" section below. Shortest path, closest to vendor defaults. The dashboards for DS2 are not preloaded, you import them in the Kibana step.

### Path B: Hideki's StackScript (10 minutes, DS2-ready out of the box)

Hideki Okamoto maintains the StackScript [`1059555`](https://cloud.linode.com/stackscripts/1059555). It installs Elasticsearch and Kibana, creates the ingest pipelines for CMCD and breadcrumbs, applies an ILM policy, loads the `datastream2` index template, and imports two Kibana dashboards: `Akamai` for the CDN business view and `Akamai Common Media Client Data` for video QoS. It's been active since 2022 and he's shipping updates: the last revision as of this writing was August 2026.

In the Cloud UI click **Deploy New Linode**, select the StackScript, fill in the UDFs (SSH user and credentials, Elasticsearch admin password, DS2 ingest user and password). Base image is `linode/ubuntu22.04`. Minimum 8 GB RAM.

Grab the public IPv4 and the **Reverse DNS** hostname from the instance dashboard. DS2 needs both.

### Path C: Terraform wrapper (version controlled, reusable)

My preferred flow for Partners/SEs/customers: version everything in a repo. [`jcavaiuolo/ds2-elk-tf`](https://github.com/jcavaiuolo/ds2-elk-tf) wraps Path B with a Terraform module that provisions:

- Linode compute instance invoking Hideki's StackScript `1059555` (unchanged).
- A Cloud Firewall that drops everything except SSH and Kibana from your admin CIDR, plus Elasticsearch `9200` only from the Akamai Origin IP ACL (same list DS2 uses to push).
- A Block Storage volume attached to the compute instance.
- (Optional, if your region supports it) a VPC and subnet.

Three commands:

```bash
git clone https://github.com/jcavaiuolo/ds2-elk-tf.git
cd ds2-elk-tf/terraform
cp terraform.tfvars.example terraform.tfvars    # edit all the CHANGE-ME values
export LINODE_TOKEN=...
terraform init && terraform apply
```

Terraform outputs `kibana_url`, `elasticsearch_bulk_endpoint`, `reverse_dns_hint`, `public_ipv4`. See the [DEPLOY.md](https://github.com/jcavaiuolo/ds2-elk-tf/blob/main/DEPLOY.md) for the full walkthrough and the regions compatibility note (older regions like `us-east` do not support VPC).

### A quick note on the "community StackScript" pattern

Hideki's idea of publishing the install as a shared StackScript was, and still is, the right move for a 10-minute demo. Any Akamai Cloud user can hit "Deploy" from the UI and get a working ES/Kibana box without reading code. Path C adds Terraform around that same StackScript rather than replacing it, so you keep the one-click reproducibility and gain versioned infra. If down the road your team prefers Ansible, cloud-init, or a Helm chart on LKE, the install logic is a plain Bash script and travels easily.

## First look at Kibana

Browse to `http://[host]:5601/`, log in as `elastic` with the password you set at deploy time. From the sidebar: Analytics -> Dashboard. If you took Path B or C the `Akamai` and `Akamai CMCD` dashboards are already there; if you took Path A, import `kibana/export.ndjson` from Hideki's repo via Stack Management -> Saved Objects.

You will see empty panels. That is expected: DS2 has not started pushing yet.

### Import the Akamai Debug dashboard

This is the plug-and-play addition. The repo ships `kibana/akamai-debug.ndjson`, a debug-focused dashboard built from scratch for SE, Partner and customer use. Import it in one call:

```bash
curl -s -u elastic:<es_admin_password> \
     -H 'kbn-xsrf: true' \
     -X POST "http://<host>:5601/api/saved_objects/_import?overwrite=true" \
     --form file=@kibana/akamai-debug.ndjson
```

Or in the UI: Stack Management -> Saved Objects -> Import -> pick the file.

You get:

- A dedicated data view `akamai-debug` on top of the same `datastream2,datastream2-*` indices (so it coexists with Hideki's view).
- A saved search `Akamai Debug: raw log table` with the fields you need to triage a single request.
- A dashboard `Akamai Debug` with 15 panels and 5 Options List comboboxes at the top for the filters you reach for most while debugging: **Host, Status code, Method, Cache status, Client IP**. The KQL bar handles everything else (path prefix, requestId, etc).
  - Row 1: Requests over time by status class, Cache HIT/MISS over time.
  - Row 2: Top 5xx and Top 4xx by host, method, path.
  - **Row 3: Client IPs with errors.** Table with `cliIP`, `country`, total errors, 4xx count, 5xx count, distinct paths hit. Click a row to pin that IP as a filter across the whole dashboard, or use the Client IP combobox above.
  - Row 4: Top errorCode, Top securityRules, Hit ratio by host.
  - Row 5: Origin RTT p50/p95/p99 filtered to cache MISS (so hits do not dilute the metric).
  - Row 6: Origin retries (empty = healthy), errorCode by host and path.
  - Row 7: DNS cold lookups by host (empty = edge DNS cache warm), Multi-hop breadcrumbs (empty = direct edge-to-origin), Non-cacheable paths going to origin.
  - Row 8: Raw log table saved search for drill-down.

Panels like Origin retries, DNS cold lookups and Multi-hop breadcrumbs are intentionally designed to **be empty when the CDN is healthy**. Their titles tell you so, so an empty panel is a signal, not a bug.

If you want to edit the dashboard, use `scripts/build-debug-dashboard.py` in the repo. It re-creates the saved objects via the Kibana API and re-exports a canonical NDJSON that you can commit back.

## Configure DataStream 2

In the Akamai Control Center, go to COMMON SERVICES -> DataStream and create a stream. The flow is:

**Stream type.** Pick `CDN` for the walkthrough. If you also want security events (WAF, Bot, Account Protector) create a second stream of type `SIEM` later. Keep them separated by index name.

**Properties.** Pick the delivery properties to attach the stream to.

**Fields.** The field picker is paginated by category. For a first install, check **Include all** and refine later. If you want a shorter list, start with: request/response, cache/offload, timing (origin RTT, time to first byte), geo, UA, security rules, and the custom field. Respect PII rules in your region.

**Format.** JSON. Required by the Elasticsearch bulk endpoint.

**Destination.**
- Destination: Elasticsearch.
- Endpoint: `https://[Akamaized hostname]/_bulk` if you proxy via your own property (recommended), or `http://[Reverse DNS]:9200/_bulk` for a quick dev setup.
- Index name: `datastream2-cdn` (or `-sec`, `-ew`, etc, if you create more streams).
- Username/password: the Elasticsearch ingest user from the StackScript UDF.
- Send compressed data: on.
- IP ACL on Elasticsearch firewall: paste the DS2 ACL list from the January 2026 changelog.

**Validate & Save.** You will likely see a dialog titled "Validation failed" with a note about Akamai Origin IP addresses. **Click Skip validation.** The Control Center validator probes from an internal IP that is not in the Origin IP ACL (the list of edge push IPs you allowlisted in the firewall), so the probe fails even when the real DS2 push path will work. The dialog itself tells you so.

**Activate.** Tick "Activate stream upon saving" and optionally "Receive an email once activation is complete". Activation takes roughly 90 minutes.

## Enable DataStream 2 in Property Manager

In the delivery property, add the `DataStream` behavior and the `Log Request Details` behavior to the default rule, then:

- `Stream version`: v2.
- `Stream names`: the stream you created above.
- `Sampling rate`: 100% to start, dial down if volume is a problem.
- Per-header/cookie toggles: enable the ones you need, respect PII.
- `Include Custom Log Field`: on. We will use it in a minute.
- `Log Akamai Edge Server IP Address`: on (required).

Save and activate. Once both DS2 and the property are active, logs begin flowing. The "Akamai" dashboard in Kibana starts drawing within minutes.

## ES|QL: the fastest way to query DS2 in 2026

If you have not met ES|QL yet, open Kibana -> Discover and switch the query bar to **ES|QL**. It is a left-to-right pipeline that is way friendlier than painting Lens visualizations for one-off questions. Six examples I use on DS2:

**Top 5xx by hostname, last hour.**

```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 1 hour AND status >= 500
| STATS errors = COUNT(*) BY reqHost
| SORT errors DESC
| LIMIT 10
```

**Offload by CP code (hit vs miss).**

```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 24 hours
| STATS total = COUNT(*), hits = COUNT(CASE WHEN cacheStatus == "HIT" THEN 1 END) BY cp
| EVAL offload = (hits * 100.0) / total
| SORT offload DESC
```

**WAF rule triggers (last 24h, top offenders).**

```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 24 hours AND securityRules IS NOT NULL
| STATS hits = COUNT(*) BY securityRules
| SORT hits DESC
| LIMIT 20
```

**Bot score distribution.**

```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 1 hour AND botScore IS NOT NULL
| EVAL bucket = CASE
    WHEN botScore < 25 THEN "0-24"
    WHEN botScore < 50 THEN "25-49"
    WHEN botScore < 75 THEN "50-74"
    ELSE "75-100"
  END
| STATS requests = COUNT(*) BY bucket
| SORT bucket ASC
```

**Origin RTT P95 per URL path.**

```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 1 hour
| STATS p95_rtt = PERCENTILE(originRTT, 95), reqs = COUNT(*) BY reqPath
| WHERE reqs > 50
| SORT p95_rtt DESC
| LIMIT 25
```

**CMCD video QoS: rebuffering ratio by content ID.**

```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 1 hour AND cmcd_cid IS NOT NULL
| STATS total = COUNT(*), stalls = COUNT(CASE WHEN cmcd_bs IS TRUE THEN 1 END) BY cmcd_cid
| EVAL stall_pct = (stalls * 100.0) / total
| SORT stall_pct DESC
```

A copy-paste pack with these and a few extras lives in `queries/esql.md` in the repo.

## Role-specific views (future work)

Beyond the shipped `Akamai`, `Akamai CMCD` and `Akamai Debug` dashboards, four role-specific views pay off and are good candidates for the next NDJSON in the repo:

**SRE / Delivery.** Edge requests per second, 2xx/4xx/5xx breakdown, offload per CP code, cache status mix, origin RTT P50/P95, map of edge regions. Alerts: origin P95 regression, offload drop.

**Security.** `securityRules` counts over time, top rule IDs, top source IPs, top ASNs, Bot score heatmap, Account Protector risk score distribution. If you have a `SIEM` stream, cross-filter from the CDN view by request ID.

**Video (CMCD v2).** Buffer starvation `cmcd_bs`, playback rate `cmcd_pr`, measured throughput `cmcd_mtp`, bitrate `cmcd_br`, content ID `cmcd_cid`, session ID `cmcd_sid`, deadline `cmcd_dl`, streaming format `cmcd_sf`. CMCD v2 adds response-side reporting; if you have it enabled in Adaptive Media Delivery, also plot server-side perceived bitrate.

**API view.** For properties fronted by API Protector: top endpoints by error rate, auth failures, suspected API discovery hits (shadow endpoints), rate-limit triggers.

## Advanced

### Custom field + EdgeWorkers

Still the single most useful trick in DS2, and still limited to 1000 bytes.

Set the custom field in your property to carry edge debug info or EdgeWorkers runtime signals. Example: measure the time the edge took to respond to the client.

```
ctt=%{client_turn_time_msec}
```

Then parse it as a Kibana runtime field so you can graph it:

```java
String ctt = dissect('ctt:%{ctt_val}').extract(doc["customField"].value)?.ctt_val;
if (ctt != null) emit(Integer.parseInt(ctt));
```

In EdgeWorkers set per-request state with `request.setVariable()` and read it into the custom field in Property Manager. Combine with `ewExecutionInfo` and `ewUsageInfo` for a full debug trail. Perfect for debugging an EdgeWorker that only misbehaves on 0.1% of requests.

### WSA correlation

Web Security Analytics is still the primary surface for WAF forensics. DS2 is complementary: it carries fields WSA does not surface in the UI, and it lets you join security signals against delivery metrics in a single Kibana panel. Example: show `requestId`, `status`, `securityRules`, `botScore`, `cacheStatus` side by side when you are chasing an "attack vs offload" question.

### Hardening the ingest path

Order of operations, cheapest to most robust:

1. **Cloud Firewall + DS2 IP ACL** (January 2026 feature). Lock port 9200/443 to Akamai IP ranges only. [Changelog][ip-acl].
2. **Enable TLS on Elasticsearch HTTP layer.** Let's Encrypt via `certbot --standalone` on 80, then flip `xpack.security.http.ssl.enabled: true`. Switch the DS2 endpoint from `http://` to `https://`. DS2 does not accept self-signed.
3. **Akamaized hostname.** Point DS2 at a hostname served by one of your own properties. The property can enforce WAF, rate limiting, mTLS, and reuse your existing certs. Origin is the Elasticsearch node.
4. **VPC + private subnet.** Put the ES node in a VPC subnet, expose only the Akamaized ingest hostname.
5. **mTLS to the destination.** Available on supported destinations, worth it for sensitive data.

## Sizing: pick the right Linode for your RPS

Rough rule of thumb based on average 1-1.5 KB uncompressed per DS2 CDN log doc (CMCD and breadcrumbs add ~30% overhead):

| Sustained RPS at edge | Daily raw log volume | Recommended instance | Block Storage | Notes |
|-----------------------|----------------------|----------------------|----------------|-------|
| < 50 | 1 to 2 GB | `g6-dedicated-4` (8 GB RAM, 4 vCPU) | 50 GB | Default of this repo. Fine for a demo. |
| 50 to 200 | 5 to 20 GB | `g6-dedicated-4` | 100 GB | Default of this repo. |
| 200 to 500 | 20 to 50 GB | `g6-dedicated-8` (16 GB RAM, 8 vCPU) | 200 GB | Bump instance type in `terraform.tfvars`. |
| 500 to 2000 | 50 to 200 GB | 3-node cluster, `g7-highmem-2` or similar (32 GB RAM each) | 500 GB each | Move to a multi-node topology. The current module does not do this, use LKE + ECK below. |
| > 2000 | 200+ GB | LKE + ECK on `g7-highmem-4` x N | per-node sizing | Managed Kubernetes with hot/warm/cold node pools and snapshot repo to Object Storage. |

Sampling rate in the DataStream behavior is your fastest escape valve. If you only need a representative view, set it to 10 or 25 instead of 100 and you cut volume linearly.

The [Elastic sizing guide][sizing] remains the best deep reference once you move past the single-node demo.

## Retention: keep storage from exploding

Hideki's StackScript ships an ILM policy named `datastream2-ilm` with a `rollover` action but **no `delete` phase**. Fine for a demo, trouble on a long-running debug stack because indices grow forever until the Block Storage fills up.

For a debug use case, 7 days of hot data covers almost every "what happened at 3am" question. Beyond that, cold storage snapshots are more cost-effective. The quickest fix is to replace the ILM policy with one that includes a `delete` phase. Pick your window:

| Use case | Hot rollover | Delete after |
|----------|--------------|--------------|
| Live debug stack | 1 day or 10 GB | **7 days** (default I recommend) |
| Weekly report feed | 7 days or 50 GB | 30 days |
| Compliance archive | 1 day or 10 GB | never (snapshot repo to Object Storage) |

The 7-day policy, applied via `curl` from your laptop (or SSH into the box and skip the host/auth bits):

```bash
curl -s -u elastic:<es_admin_password> \
     -H 'Content-Type: application/json' \
     -X PUT "http://<host>:9200/_ilm/policy/datastream2-ilm" \
     -d '{
       "policy": {
         "phases": {
           "hot": {
             "min_age": "0ms",
             "actions": {
               "set_priority": { "priority": 100 },
               "rollover": { "max_age": "1d", "max_primary_shard_size": "10gb" }
             }
           },
           "delete": {
             "min_age": "7d",
             "actions": { "delete": {} }
           }
         }
       }
     }'
```

The policy applies to all indices matching the `datastream2-*` pattern (via the index template that Hideki's StackScript installs). Existing indices older than 7 days get deleted on the next ILM run.

If you want hot indices archived to Akamai Cloud Object Storage before deletion, register an S3 snapshot repository and add a `cold` phase with a `searchable_snapshot` action. Object Storage is S3-compatible so the native `repository-s3` plugin works out of the box.

## Production checklist

- Alert on **Datastream Upload Failures** in Control Center.
- Sizing: see the table above. Don't start smaller than 8 GB, Elasticsearch won't boot cleanly below.
- Retention: see the ILM snippet above. Set a `delete` phase before you forget.
- HTTPS on the Elasticsearch HTTP layer. Let's Encrypt via `certbot --standalone` on port 80, then flip `xpack.security.http.ssl.enabled: true`. DS2 does not accept self-signed.
- **Make Elasticsearch nodes redundant** for anything load-bearing. 3-node minimum. Consider **LKE** (Akamai's managed Kubernetes) with the Elastic Cloud on Kubernetes (ECK) operator. Helm/ECK is my current preference over StackScript for production.
- **Block storage** for data volumes on the compute instances.
- Snapshot repo to Object Storage for anything you want to keep past the hot window.
- Watch RAM, JVM GC, and disk pressure in Stack Monitoring.

[sizing]: https://www.elastic.co/blog/benchmarking-and-sizing-your-elasticsearch-cluster-for-logs-and-metrics

## Appendix

- Original post by Hideki Okamoto (September 2022, revised August 2026): [dev.to][orig]
- Hideki's StackScript 1059555: https://cloud.linode.com/stackscripts/1059555
- This refresh's Terraform + Akamai Debug dashboard repo: https://github.com/jcavaiuolo/ds2-elk-tf
- DataStream 2 destinations reference: [techdocs.akamai.com][dest]
- DataStream 2 IP ACL changelog, January 2026: [techdocs.akamai.com][ip-acl]
- Akamai Origin IP ACL (the live CIDR list for the DS2 firewall): https://techdocs.akamai.com/origin-ip-acl/docs/update-your-origin-server
- DataStream 2 data set parameters: https://techdocs.akamai.com/datastream2/docs/data-set-parameters
- DataStream 2 security logs (SIEM via DS2): https://techdocs.akamai.com/datastream2/docs/security-logs
- Akamai TrafficPeak (managed DS2 destination alternative): https://www.akamai.com/products/trafficpeak
- EdgeWorkers Request.setVariable(): https://techdocs.akamai.com/edgeworkers/docs/request-object
- CMCD specification (CTA WAVE): https://cta.tech/

## Credits

Original architecture, StackScript and dashboard shape by [Hideki Okamoto][hokamoto]. This refresh keeps his bones and adds four years of DataStream 2, Elastic, and Akamai Cloud evolution. If you only read one post about getting DS2 into Elastic, start with his.

[hokamoto]: https://dev.to/hokamoto
