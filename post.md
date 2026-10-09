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

## Two ways to deploy

Pick the fastest one you can defend to Security.

### Path A: Akamai Cloud Marketplace (fast)

The Akamai Cloud Marketplace has one-click apps for Elasticsearch and Kibana. Choose a Dedicated 8 GB (minimum) compute instance in a region close to your origin, let the Marketplace install run, then jump straight to the "Configure DataStream 2" section below.

Pros: shortest path, upgrades are easier because it stays close to vendor defaults.
Trade-off: dashboards and index templates for DS2 are not preloaded, you add them in the Kibana import step below.

### Path B: Forked StackScript (opinionated)

Fork of Hideki's StackScript, with the following bumps:

- Ubuntu 24.04 LTS base image.
- Elasticsearch and Kibana 8.x pinned (test against 9.x before adopting).
- Keeps the pattern of installing both on a single node for the demo. Split for anything production.
- Updates the preloaded **index template**, **data view**, **visualizations** and **dashboards** to cover the new fields: Bot Manager, Account Protector hints via Security events, EdgeWorkers runtime, CMCD v2, API Protector detections.
- Leaves security on, generates a self-signed CA for internal cluster TLS, and documents how to swap in a Let's Encrypt cert for the HTTP layer.

The script is still a two-field UDF: SSH user credentials and admin passwords for Elasticsearch/Kibana and the DS2 ingest user. Deploy on a `g6-dedicated-8` or larger (see the sizing note at the end).

While the node provisions, note down two things from the Linode dashboard: the public IPv4 and the **Reverse DNS** hostname. You will need both to configure DS2.

[Repo and definition files on GitHub][repo]. The import step below works for either path.

[repo]: https://github.com/jcavaiuolo/elasticsearch-kibana-for-akamai-datastream2

### Note on the "community StackScript" pattern

Hideki's original idea of publishing the install as a shared StackScript was,
and still is, the right move for a 10-minute demo. It lets any Akamai Cloud
user hit "Deploy" from the UI and get a working ES/Kibana box without reading
code. I keep that spirit in the fork and keep the UDF-driven form.

That said, you do not have to be married to the StackScript if your team
already lives in version-controlled infra. The install logic is a plain Bash
script, so you can:

- **Export it as `user_data`** on `linode_instance` (the Terraform module below
  has a `metadata { user_data = base64encode(file("install.sh")) }` option),
  and version `install.sh` alongside your Terraform. You lose the UDF form in
  the Cloud UI; you gain CI/CD-friendly diffs.
- **Call it from Ansible, Salt, or your config-management tool of choice**
  after a bare instance comes up. Same script, different driver.
- **Fork the StackScript** into your own community account if you want to
  share your variant with your org while keeping the "one-click deploy"
  button in the Cloud UI.

Hideki's StackScript, my fork, cloud-init, and Ansible all end up running the
same ~200 lines. Pick the one that matches your operating model.

## First look at Kibana

Browse to `http://[host]:5601/`, log in as `elastic` with the password you set at deploy time. From the sidebar: Analytics -> Dashboard. If you took Path B the "Akamai" dashboard is already there; if you took Path A, import `kibana/export.ndjson` from the repo via Stack Management -> Saved Objects.

You will see empty panels. That is expected: DS2 has not started pushing yet.

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

**Validate & Save.** You should see "Destination details are valid" within a few seconds.

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

## Dashboards worth building

The dashboard that comes with Path B covers CDN basics. On top of that, four role-specific views pay off:

**SRE / Delivery.** Edge requests per second, 2xx/4xx/5xx breakdown, offload per CP code, cache status mix, origin RTT P50/P95, map of edge regions. Alerts: origin P95 regression, offload drop.

**Security.** `securityRules` counts over time, top rule IDs, top source IPs, top ASNs, Bot score heatmap, Account Protector risk score distribution. If you have a `SIEM` stream, cross-filter from the CDN view by request ID.

**Video (CMCD v2).** Buffer starvation `cmcd_bs`, playback rate `cmcd_pr`, measured throughput `cmcd_mtp`, bitrate `cmcd_br`, content ID `cmcd_cid`, session ID `cmcd_sid`, deadline `cmcd_dl`, streaming format `cmcd_sf`. CMCD v2 adds response-side reporting; if you have it enabled in Adaptive Media Delivery, also plot server-side perceived bitrate.

**API view.** For properties fronted by API Protector: top endpoints by error rate, auth failures, suspected API discovery hits (shadow endpoints), rate-limit triggers.

Export each as NDJSON in `kibana/dashboards/` so the next person can import them.

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

## Production checklist

Not changed much from the original list, but updated to 2026 tooling:

- Alert on **Datastream Upload Failures** in Control Center.
- Right-size the node. For low-traffic properties, 8 GB is fine. For 1k+ req/s start at 32 GB and plan for a 3-node cluster. [Elastic sizing guide][sizing] remains the best starting point.
- HTTPS on the HTTP layer (above).
- **Make Elasticsearch nodes redundant.** 3-node minimum for anything load-bearing. Consider **LKE** (Akamai's managed Kubernetes) with the Elastic Cloud on Kubernetes (ECK) operator. Helm/ECK is my current preference over StackScript for production.
- **Block storage** for data volumes on the compute instances.
- **ILM**. Rollover daily, hot -> warm after 7d, warm -> cold after 30d, then snapshot repository to Akamai Cloud Object Storage. Object Storage is S3-compatible so the native `s3` snapshot plugin works.
- Lifecycle policy must align with your retention obligations.
- Watch RAM, GC, and disk pressure in Stack Monitoring.

[sizing]: https://www.elastic.co/blog/benchmarking-and-sizing-your-elasticsearch-cluster-for-logs-and-metrics

## Appendix

- Original post by Hideki Okamoto (September 2022, revised November 2023): [dev.to][orig]
- DataStream 2 destinations reference: [techdocs.akamai.com][dest]
- DataStream 2 IP ACL changelog, January 2026: [techdocs.akamai.com][ip-acl]
- DataStream 2 data set parameters: https://techdocs.akamai.com/datastream2/docs/data-set-parameters
- DataStream 2 security logs (SIEM via DS2): https://techdocs.akamai.com/datastream2/docs/security-logs
- Akamai TrafficPeak (managed DS2 destination alternative): https://www.akamai.com/products/trafficpeak
- Elasticsearch, Kibana for Akamai DataStream 2 (fork): https://github.com/jcavaiuolo/elasticsearch-kibana-for-akamai-datastream2
- EdgeWorkers Request.setVariable(): https://techdocs.akamai.com/edgeworkers/docs/request-object
- CMCD specification (CTA WAVE): https://cta.tech/

## Credits

Original architecture, StackScript and dashboard shape by [Hideki Okamoto][hokamoto]. This refresh keeps his bones and adds four years of DataStream 2, Elastic, and Akamai Cloud evolution. If you only read one post about getting DS2 into Elastic, start with his.

[hokamoto]: https://dev.to/hokamoto
