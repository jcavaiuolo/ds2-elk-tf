# ES|QL companion for DataStream 2 on Elasticsearch

Copy-paste pack for Kibana -> Discover -> ES|QL. Field names and types follow the
index template that Hideki Okamoto's StackScript installs (`logs-akamai.datastream2`):

- Indices: write alias `datastream2`, rolled indices `datastream2-*`.
- Time field: `reqTimeSec` (there is no `@timestamp`).
- `statusCode` is a keyword: convert with `TO_INTEGER(statusCode)` before comparing.
- `cacheStatus` is `"1"` for a cache hit, `"0"` for a miss. `cacheable` follows the same convention.
- CMCD values live under `cmcd.*` (numbers like `cmcd.br`, `cmcd.bl`, `cmcd.mtp`; strings like
  `cmcd.sid`, `cmcd.cid`, `cmcd.sf` are text with a `.keyword` sub-field for grouping).

Requires Elasticsearch 8.16+ (`COUNT(*) WHERE ...` inside `STATS`). The StackScript installs
the latest 8.x.

## Delivery / SRE

### Top 5xx by hostname, last hour
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 1 hour AND TO_INTEGER(statusCode) >= 500
| STATS errors = COUNT(*) BY reqHost
| SORT errors DESC
| LIMIT 10
```

### Offload per CP code, last 24h
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 24 hours
| STATS total = COUNT(*), hits = COUNT(*) WHERE cacheStatus == "1" BY cp
| EVAL offload_pct = ROUND(hits * 100.0 / total, 1)
| SORT total DESC
```

### Turnaround time p95 per path on cache misses (hot URLs only)
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 1 hour AND cacheStatus != "1"
| EVAL ta = TO_INTEGER(turnAroundTimeMSec)
| STATS p95_ms = PERCENTILE(ta, 95), reqs = COUNT(*) BY reqHost, reqPath
| WHERE reqs > 50
| SORT p95_ms DESC
| LIMIT 25
```

### GB served by cache status, last 24h
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 24 hours
| EVAL cache = CASE(cacheStatus == "1", "HIT", cacheStatus == "0", "MISS", "OTHER")
| STATS gb = ROUND(SUM(totalBytes) / 1e9, 2) BY cache
| SORT gb DESC
```

### Requests per client country and edge country, last hour
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 1 hour
| STATS reqs = COUNT(*) BY country, serverCountry
| SORT reqs DESC
| LIMIT 20
```

### TLS versions in use, last 24h
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 24 hours AND tlsVersion IS NOT NULL AND tlsVersion != "-"
| STATS reqs = COUNT(*) BY tlsVersion
| SORT reqs DESC
```

### Slowest DNS lookups by host (cold edge DNS cache)
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 1 hour
| EVAL dns = TO_INTEGER(dnsLookupTimeMSec)
| WHERE dns > 0
| STATS p95_dns_ms = PERCENTILE(dns, 95), lookups = COUNT(*) BY reqHost
| SORT p95_dns_ms DESC
| LIMIT 20
```

## Errors and security

### Top errorCode by host, last 24h
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 24 hours AND errorCode IS NOT NULL AND errorCode != "-"
| STATS errors = COUNT(*) BY errorCode, reqHost
| SORT errors DESC
| LIMIT 25
```

### Top security rule triggers, last 24h
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 24 hours AND securityRules IS NOT NULL AND securityRules != "-"
| STATS hits = COUNT(*) BY securityRules
| SORT hits DESC
| LIMIT 20
```

### Client IPs collecting the most 403s, last hour
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 1 hour AND statusCode == "403"
| STATS denies = COUNT(*), paths = COUNT_DISTINCT(reqPath) BY cliIP, country
| SORT denies DESC
| LIMIT 20
```

### Paths hit with methods other than GET/HEAD
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 24 hours AND reqMethod NOT IN ("GET", "HEAD")
| STATS reqs = COUNT(*) BY reqHost, reqMethod, reqPath
| SORT reqs DESC
| LIMIT 25
```

## Video (CMCD)

These need CMCD in your stream's data set; Hideki's ingest pipeline extracts it into `cmcd.*`.

### Average requested bitrate per session, last 15 minutes
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 15 minutes AND cmcd.br IS NOT NULL
| STATS avg_br_kbps = AVG(cmcd.br), segments = COUNT(*) BY cmcd.sid.keyword
| SORT avg_br_kbps DESC
| LIMIT 50
```

### Share of requests with a low player buffer (< 2 s) per content ID
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 1 hour AND cmcd.bl IS NOT NULL
| STATS total = COUNT(*), low_buffer = COUNT(*) WHERE cmcd.bl < 2000 BY cmcd.cid.keyword
| EVAL low_buffer_pct = ROUND(low_buffer * 100.0 / total, 1)
| SORT low_buffer_pct DESC
| LIMIT 25
```

### Measured throughput p50/p95 per streaming format
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 1 hour AND cmcd.mtp IS NOT NULL
| STATS p50_kbps = PERCENTILE(cmcd.mtp, 50), p95_kbps = PERCENTILE(cmcd.mtp, 95) BY cmcd.sf.keyword
| SORT p95_kbps DESC
```

## EdgeWorkers

### Requests that ran an EdgeWorker, by host and path
```sql
FROM datastream2,datastream2-*
| WHERE reqTimeSec > NOW() - 1 hour AND ewExecutionInfo IS NOT NULL
| STATS execs = COUNT(*) BY reqHost, reqPath
| SORT execs DESC
| LIMIT 25
```

## Notes

- Every query sets its own window with `WHERE reqTimeSec > NOW() - ...`, so results do not
  depend on how Kibana's time picker maps to this index (it has no `@timestamp`).
- Fields that are not in Hideki's template (for example ones added by newer DS2 data sets) are
  mapped dynamically; check the `datastream2` data view before relying on them.
