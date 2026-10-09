# ES|QL companion for DataStream 2 on Elasticsearch

Copy-paste pack. All queries assume CDN-type streams written to indices matching
`datastream2-cdn-*`. Rename to match your index pattern.

## Delivery / SRE

### Top 5xx by hostname, last hour
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 1 hour AND status >= 500
| STATS errors = COUNT(*) BY reqHost
| SORT errors DESC
| LIMIT 10
```

### Offload per CP code (hit vs miss) last 24h
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 24 hours
| STATS total = COUNT(*),
        hits  = COUNT(CASE WHEN cacheStatus == "HIT" THEN 1 END)
      BY cp
| EVAL offload = (hits * 100.0) / total
| SORT offload DESC
```

### Origin RTT P95 per path, hot URLs only
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 1 hour
| STATS p95_rtt = PERCENTILE(originRTT, 95),
        reqs    = COUNT(*)
      BY reqPath
| WHERE reqs > 50
| SORT p95_rtt DESC
| LIMIT 25
```

### Bytes served by cache status
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 24 hours
| STATS gb = SUM(bytes) / 1e9 BY cacheStatus
| SORT gb DESC
```

### Edge regions handling the most traffic
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 1 hour
| STATS reqs = COUNT(*) BY edgeIpContinent, edgeIpCountry
| SORT reqs DESC
| LIMIT 20
```

## Security

### Top WAF/Bot rule triggers
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 24 hours AND securityRules IS NOT NULL
| STATS hits = COUNT(*) BY securityRules
| SORT hits DESC
| LIMIT 20
```

### Bot score distribution
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

### Top ASNs by denied requests
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 24 hours AND responseAction == "DENY"
| STATS denies = COUNT(*) BY asn
| SORT denies DESC
| LIMIT 20
```

### Rate limit triggers per endpoint
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 1 hour AND securityRules LIKE "*rate-limit*"
| STATS triggers = COUNT(*) BY reqPath
| SORT triggers DESC
| LIMIT 25
```

## Video (CMCD v1 and v2)

### Rebuffering ratio per content ID
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 1 hour AND cmcd_cid IS NOT NULL
| STATS total  = COUNT(*),
        stalls = COUNT(CASE WHEN cmcd_bs IS TRUE THEN 1 END)
      BY cmcd_cid
| EVAL stall_pct = (stalls * 100.0) / total
| SORT stall_pct DESC
```

### Average bitrate per session (last 15 min)
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 15 minutes AND cmcd_sid IS NOT NULL
| STATS avg_br_kbps = AVG(cmcd_br) BY cmcd_sid
| SORT avg_br_kbps DESC
| LIMIT 50
```

### Measured throughput distribution
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 1 hour AND cmcd_mtp IS NOT NULL
| STATS p50 = PERCENTILE(cmcd_mtp, 50),
        p95 = PERCENTILE(cmcd_mtp, 95)
      BY cmcd_sf
| SORT p95 DESC
```

## EdgeWorkers runtime

### Executions by status
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 1 hour AND ewExecutionInfo IS NOT NULL
| STATS execs = COUNT(*) BY ewStatus
| SORT execs DESC
```

### Over-limit executions (CPU/memory)
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 24 hours
   AND (ewUsageInfo LIKE "*cpu_time*" OR ewUsageInfo LIKE "*memory*")
| STATS hits = COUNT(*) BY ewVersion, reqPath
| SORT hits DESC
| LIMIT 20
```

## API Protector

### Top endpoints by denied requests
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 24 hours AND responseAction == "DENY"
| STATS denies = COUNT(*) BY reqPath, reqMethod
| SORT denies DESC
| LIMIT 25
```

### Suspected shadow API hits (discovery signal)
```sql
FROM datastream2-cdn-*
| WHERE @timestamp > NOW() - 24 hours AND apiSummary IS NOT NULL
| STATS reqs = COUNT(*) BY apiSummary
| SORT reqs DESC
| LIMIT 25
```

## Notes

- Field names here follow the DS2 CDN data set naming convention. Verify in Kibana's
  Data View that the exact names match (some ingest pipelines rename or flatten).
- CMCD fields assume the DS2 CMCD extraction is on in your Adaptive Media Delivery
  behavior. If you ship CMCD only as query parameters, parse them in an ingest
  pipeline or runtime field first.
- ES|QL requires Elasticsearch 8.11+ for most of these, 8.13+ for CASE WHEN inside
  STATS. On 9.x everything works.
