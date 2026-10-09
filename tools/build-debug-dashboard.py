#!/usr/bin/env python3
"""
Create the Akamai Debug dashboard in Kibana via the Saved Objects API,
then export it to NDJSON so the file is Kibana's canonical shape.

Usage:
    KIBANA_URL=http://50.116.48.142:5601 \
    KIBANA_USER=elastic KIBANA_PASS=... \
    python3 tools/build-debug-dashboard.py

Writes:
    kibana/akamai-debug.ndjson
"""
import json
import os
import sys
import uuid
import urllib.request
import urllib.error
from pathlib import Path

KIBANA_URL = os.environ.get("KIBANA_URL", "http://localhost:5601").rstrip("/")
USER = os.environ.get("KIBANA_USER", "elastic")
PASS = os.environ.get("KIBANA_PASS", "")
if not PASS:
    sys.exit("Set KIBANA_PASS")

HEADERS = {
    "kbn-xsrf": "true",
    "Content-Type": "application/json",
}

import base64
auth = base64.b64encode(f"{USER}:{PASS}".encode()).decode()
HEADERS["Authorization"] = f"Basic {auth}"

# Fixed IDs so re-runs are idempotent.
IDX = "akamai-debug-dataview"
RAW_SEARCH_ID = "akamai-debug-raw-search"
DASH_ID = "akamai-debug-dashboard"
INDEX_PATTERN_TITLE = "datastream2,datastream2-*"


def req(method, path, body=None, allow_404=False):
    url = f"{KIBANA_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        if allow_404 and e.code == 404:
            return None
        body = e.read().decode()
        raise RuntimeError(f"{method} {path} -> {e.code}: {body}") from None


def delete_if_exists(so_type, so_id):
    print(f"  deleting prior {so_type}/{so_id} if any...")
    req("DELETE", f"/api/saved_objects/{so_type}/{so_id}?force=true", allow_404=True)


def create_dataview():
    """
    Create the Data View (saved object type 'index-pattern').
    Uses the same underlying indices as Hideki's data view (datastream2,datastream2-*)
    but with a fixed, well-known ID so panels can reference it stably.
    """
    print("creating data view...")
    delete_if_exists("index-pattern", IDX)
    body = {
        "attributes": {
            "title": INDEX_PATTERN_TITLE,
            "timeFieldName": "reqTimeSec",
            "name": "akamai-debug",
        }
    }
    res = req("POST", f"/api/saved_objects/index-pattern/{IDX}", body)
    print(f"  created id={res['id']}")
    return res


def create_raw_search():
    """
    Saved search for the raw log table embedded at the bottom of the dashboard.
    Columns are the ones you want when debugging a single request.
    """
    print("creating raw-logs saved search...")
    delete_if_exists("search", RAW_SEARCH_ID)
    body = {
        "attributes": {
            "title": "Akamai Debug: raw log table",
            "description": "Chronological raw log rows with the fields you need to triage one request.",
            "hits": 0,
            "columns": [
                "reqHost",
                "reqMethod",
                "reqPath",
                "statusCode",
                "errorCode",
                "cacheStatus",
                "cliIP",
                "edgeIP",
                "reqId",
                "UA",
                "turnAroundTimeMSec",
                "securityRules",
                "customField",
            ],
            "sort": [["reqTimeSec", "desc"]],
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps({
                    "query": {"query": "", "language": "kuery"},
                    "filter": [],
                    "indexRefName": "kibanaSavedObjectMeta.searchSourceJSON.index",
                }),
            },
        },
        "references": [
            {
                "name": "kibanaSavedObjectMeta.searchSourceJSON.index",
                "type": "index-pattern",
                "id": IDX,
            }
        ],
    }
    res = req("POST", f"/api/saved_objects/search/{RAW_SEARCH_ID}?overwrite=true", body)
    print(f"  created id={res['id']}")
    return res


# Panel helpers --------------------------------------------------------------

def esql_lens_panel(panel_index, title, viz_type, esql, columns, grid, vis_state):
    """
    Build a by-value Lens panel driven by ES|QL. Returns a dict ready to be
    serialized into the dashboard's panelsJSON array.
    """
    layer_id = f"layer-{panel_index}"
    attrs = {
        "title": title,
        "description": "",
        "visualizationType": viz_type,
        "type": "lens",
        "references": [
            {"name": f"indexpattern-datasource-layer-{layer_id}", "type": "index-pattern", "id": IDX}
        ],
        "state": {
            "visualization": vis_state,
            "query": {"esql": esql},
            "filters": [],
            "datasourceStates": {
                "textBased": {
                    "layers": {
                        layer_id: {
                            "index": IDX,
                            "query": {"esql": esql},
                            "columns": columns,
                            "timeField": "reqTimeSec",
                        }
                    }
                }
            },
            "internalReferences": [
                {"name": f"indexpattern-datasource-layer-{layer_id}", "type": "index-pattern", "id": IDX}
            ],
        },
    }
    return {
        "type": "lens",
        "gridData": {**grid, "i": panel_index},
        "panelIndex": panel_index,
        "embeddableConfig": {"attributes": attrs, "enhancements": {}},
        "title": title,
        "version": "8.19.0",
    }


def xy_line(layer_id, x_col, y_cols, split=None):
    layer = {
        "layerId": layer_id,
        "layerType": "data",
        "seriesType": "line",
        "xAccessor": x_col,
        "accessors": y_cols,
    }
    if split:
        layer["splitAccessor"] = split
    return {
        "preferredSeriesType": "line",
        "layers": [layer],
        "legend": {"isVisible": True, "position": "right"},
        "valueLabels": "hide",
        "fittingFunction": "None",
        "axisTitlesVisibilitySettings": {"x": True, "yLeft": True, "yRight": True},
        "tickLabelsVisibilitySettings": {"x": True, "yLeft": True, "yRight": True},
        "labelsOrientation": {"x": 0, "yLeft": 0, "yRight": 0},
        "gridlinesVisibilitySettings": {"x": True, "yLeft": True, "yRight": True},
    }


def xy_bar_stacked(layer_id, x_col, y_cols, split=None):
    layer = {
        "layerId": layer_id,
        "layerType": "data",
        "seriesType": "bar_stacked",
        "xAccessor": x_col,
        "accessors": y_cols,
    }
    if split:
        layer["splitAccessor"] = split
    return {
        "preferredSeriesType": "bar_stacked",
        "layers": [layer],
        "legend": {"isVisible": True, "position": "right"},
        "valueLabels": "hide",
    }


def datatable(layer_id, cols):
    return {
        "layerId": layer_id,
        "layerType": "data",
        "columns": [{"columnId": c, "isTransposed": False} for c in cols],
    }


def metric(layer_id, metric_col):
    return {
        "layerId": layer_id,
        "layerType": "data",
        "metricAccessor": metric_col,
    }


# -- ES|QL queries -----------------------------------------------------------

Q_REQS_PER_MIN = (
    'FROM datastream2,datastream2-* | '
    'STATS requests = COUNT(*) BY bucket = BUCKET(reqTimeSec, 1 minute) | '
    'SORT bucket ASC'
)

Q_STATUS_CLASS_PER_MIN = (
    'FROM datastream2,datastream2-* | '
    'EVAL sc = TO_INTEGER(statusCode) | '
    'EVAL class = CASE('
    '  sc < 200, "1xx",'
    '  sc < 300, "2xx",'
    '  sc < 400, "3xx",'
    '  sc < 500, "4xx",'
    '  "5xx") | '
    'STATS c = COUNT(*) BY bucket = BUCKET(reqTimeSec, 1 minute), class | '
    'SORT bucket ASC'
)

Q_TOP_5XX_PATHS = (
    'FROM datastream2,datastream2-* | '
    'WHERE TO_INTEGER(statusCode) >= 500 | '
    'STATS errors = COUNT(*) BY reqHost, reqMethod, reqPath, statusCode | '
    'SORT errors DESC | '
    'LIMIT 20'
)

Q_TOP_4XX_PATHS = (
    'FROM datastream2,datastream2-* | '
    'EVAL sc = TO_INTEGER(statusCode) | '
    'WHERE sc >= 400 AND sc < 500 | '
    'STATS errors = COUNT(*) BY reqHost, reqMethod, reqPath, statusCode | '
    'SORT errors DESC | '
    'LIMIT 20'
)

Q_TOP_ERRORCODE = (
    'FROM datastream2,datastream2-* | '
    'WHERE errorCode IS NOT NULL AND errorCode != "-" | '
    'STATS c = COUNT(*) BY errorCode | '
    'SORT c DESC | '
    'LIMIT 15'
)

Q_CACHE_OVER_TIME = (
    'FROM datastream2,datastream2-* | '
    'EVAL cs = CASE(cacheStatus == "1", "HIT", cacheStatus == "0", "MISS", "OTHER") | '
    'STATS c = COUNT(*) BY bucket = BUCKET(reqTimeSec, 1 minute), cs | '
    'SORT bucket ASC'
)

Q_HIT_RATIO_BY_HOST = (
    'FROM datastream2,datastream2-* | '
    'WHERE reqHost IS NOT NULL AND reqHost != "-" | '
    'STATS total = COUNT(*), hits = COUNT(*) WHERE cacheStatus == "1" BY reqHost | '
    'EVAL hit_pct = (hits * 100.0) / total | '
    'KEEP reqHost, total, hits, hit_pct | '
    'SORT total DESC | '
    'LIMIT 20'
)

Q_TOP_SECRULES = (
    'FROM datastream2,datastream2-* | '
    'WHERE securityRules IS NOT NULL AND securityRules != "-" | '
    'STATS triggers = COUNT(*) BY securityRules | '
    'SORT triggers DESC | '
    'LIMIT 15'
)

Q_ORIGIN_RTT_MISS = (
    'FROM datastream2,datastream2-* | '
    'WHERE cacheStatus != "1" | '
    'EVAL ta = TO_INTEGER(turnAroundTimeMSec) | '
    'STATS p50 = PERCENTILE(ta, 50), p95 = PERCENTILE(ta, 95), p99 = PERCENTILE(ta, 99) '
    'BY bucket = BUCKET(reqTimeSec, 1 minute) | '
    'SORT bucket ASC'
)

Q_ORIGIN_RETRIES = (
    'FROM datastream2,datastream2-* | '
    'WHERE TO_INTEGER(edgeAttempts) > 1 | '
    'STATS retries = COUNT(*), avg_ta = AVG(TO_INTEGER(turnAroundTimeMSec)) '
    'BY reqHost, reqMethod, reqPath, errorCode | '
    'SORT retries DESC | '
    'LIMIT 25'
)

Q_ERRORCODE_BY_PATH = (
    'FROM datastream2,datastream2-* | '
    'WHERE errorCode IS NOT NULL AND errorCode != "-" | '
    'STATS errors = COUNT(*) BY errorCode, reqHost, reqPath | '
    'SORT errors DESC | '
    'LIMIT 25'
)

Q_DNS_SLOW_BY_HOST = (
    'FROM datastream2,datastream2-* | '
    'EVAL dns = TO_INTEGER(dnsLookupTimeMSec) | '
    'WHERE reqHost IS NOT NULL AND reqHost != "-" AND dns > 0 | '
    'STATS p95_dns_ms = PERCENTILE(dns, 95), lookups = COUNT(*) '
    'BY reqHost | '
    'SORT p95_dns_ms DESC | '
    'LIMIT 20'
)

Q_BREADCRUMBS_TOP = (
    'FROM datastream2,datastream2-* | '
    'WHERE breadcrumbs IS NOT NULL AND breadcrumbs != "-" | '
    'STATS c = COUNT(*) BY breadcrumbs | '
    'SORT c DESC | '
    'LIMIT 10'
)

Q_UNCACHEABLE_PATHS = (
    'FROM datastream2,datastream2-* | '
    'WHERE cacheable == "0" AND cacheStatus != "1" AND reqHost IS NOT NULL AND reqHost != "-" | '
    'STATS c = COUNT(*) BY reqHost, reqPath, rspContentType | '
    'SORT c DESC | '
    'LIMIT 20'
)

Q_CLIENTS_WITH_ERRORS = (
    'FROM datastream2,datastream2-* | '
    'EVAL sc = TO_INTEGER(statusCode) | '
    'WHERE sc >= 400 AND cliIP IS NOT NULL | '
    'STATS errors = COUNT(*), '
    '      status_4xx = COUNT(*) WHERE sc < 500, '
    '      status_5xx = COUNT(*) WHERE sc >= 500, '
    '      distinct_paths = COUNT_DISTINCT(reqPath) '
    'BY cliIP, country | '
    'SORT errors DESC | '
    'LIMIT 25'
)


def cols(spec):
    """
    Convenience: ES|QL returned columns schema that Lens wants.
    spec: list of (name, type).  type in {"date","number","string"}
    """
    types_meta = {
        "date": {"type": "date", "esType": "date"},
        "number": {"type": "number", "esType": "long"},
        "string": {"type": "string", "esType": "keyword"},
    }
    return [{"columnId": n, "fieldName": n, "meta": types_meta[t]} for n, t in spec]


def build_dashboard_panels():
    panels = []
    # Row 1: hero numbers would ideally be metrics, skipping for v1 brevity.
    # Row 2: trending
    panels.append(esql_lens_panel(
        "p_status_trend",
        "Requests over time by status class",
        "lnsXY",
        Q_STATUS_CLASS_PER_MIN,
        cols([("bucket", "date"), ("c", "number"), ("class", "string")]),
        {"x": 0, "y": 0, "w": 24, "h": 10},
        xy_bar_stacked("layer-p_status_trend", "bucket", ["c"], split="class"),
    ))
    panels.append(esql_lens_panel(
        "p_cache_trend",
        "Cache HIT vs MISS over time",
        "lnsXY",
        Q_CACHE_OVER_TIME,
        cols([("bucket", "date"), ("c", "number"), ("cs", "string")]),
        {"x": 24, "y": 0, "w": 24, "h": 10},
        xy_bar_stacked("layer-p_cache_trend", "bucket", ["c"], split="cs"),
    ))

    # Row 3: 4xx & 5xx deep dive
    panels.append(esql_lens_panel(
        "p_top_5xx",
        "Top 5xx (host, method, path, status)",
        "lnsDatatable",
        Q_TOP_5XX_PATHS,
        cols([("reqHost", "string"), ("reqMethod", "string"), ("reqPath", "string"), ("statusCode", "string"), ("errors", "number")]),
        {"x": 0, "y": 10, "w": 24, "h": 14},
        datatable("layer-p_top_5xx", ["reqHost", "reqMethod", "reqPath", "statusCode", "errors"]),
    ))
    panels.append(esql_lens_panel(
        "p_top_4xx",
        "Top 4xx (host, method, path, status)",
        "lnsDatatable",
        Q_TOP_4XX_PATHS,
        cols([("reqHost", "string"), ("reqMethod", "string"), ("reqPath", "string"), ("statusCode", "string"), ("errors", "number")]),
        {"x": 24, "y": 10, "w": 24, "h": 14},
        datatable("layer-p_top_4xx", ["reqHost", "reqMethod", "reqPath", "statusCode", "errors"]),
    ))

    # Row 4: client IPs that received errors (click a row to filter the dashboard)
    panels.append(esql_lens_panel(
        "p_clients_errors",
        "Client IPs with errors (click a row to filter the dashboard)",
        "lnsDatatable",
        Q_CLIENTS_WITH_ERRORS,
        cols([("cliIP", "string"), ("country", "string"),
              ("errors", "number"), ("status_4xx", "number"),
              ("status_5xx", "number"), ("distinct_paths", "number")]),
        {"x": 0, "y": 24, "w": 48, "h": 12},
        datatable("layer-p_clients_errors",
                  ["cliIP", "country", "errors", "status_4xx", "status_5xx", "distinct_paths"]),
    ))

    # Row 5: errorCode + securityRules + hit ratio
    panels.append(esql_lens_panel(
        "p_errorcode",
        "Top errorCode (ERR_* from DS2)",
        "lnsDatatable",
        Q_TOP_ERRORCODE,
        cols([("errorCode", "string"), ("c", "number")]),
        {"x": 0, "y": 36, "w": 16, "h": 12},
        datatable("layer-p_errorcode", ["errorCode", "c"]),
    ))
    panels.append(esql_lens_panel(
        "p_secrules",
        "Top securityRules triggers",
        "lnsDatatable",
        Q_TOP_SECRULES,
        cols([("securityRules", "string"), ("triggers", "number")]),
        {"x": 16, "y": 36, "w": 16, "h": 12},
        datatable("layer-p_secrules", ["securityRules", "triggers"]),
    ))
    panels.append(esql_lens_panel(
        "p_hit_by_host",
        "Cache hit ratio by host",
        "lnsDatatable",
        Q_HIT_RATIO_BY_HOST,
        cols([("reqHost", "string"), ("total", "number"), ("hits", "number"), ("hit_pct", "number")]),
        {"x": 32, "y": 36, "w": 16, "h": 12},
        datatable("layer-p_hit_by_host", ["reqHost", "total", "hits", "hit_pct"]),
    ))

    # Row 6: origin RTT (filtered to MISS so the picture is honest)
    panels.append(esql_lens_panel(
        "p_origin_rtt",
        "Origin RTT p50/p95/p99 (ms), only cache MISS",
        "lnsXY",
        Q_ORIGIN_RTT_MISS,
        cols([("bucket", "date"), ("p50", "number"), ("p95", "number"), ("p99", "number")]),
        {"x": 0, "y": 48, "w": 48, "h": 12},
        xy_line("layer-p_origin_rtt", "bucket", ["p50", "p95", "p99"]),
    ))

    # Row 7: origin reliability
    panels.append(esql_lens_panel(
        "p_origin_retries",
        "Origin retries (empty = no retries, healthy)",
        "lnsDatatable",
        Q_ORIGIN_RETRIES,
        cols([("reqHost", "string"), ("reqMethod", "string"), ("reqPath", "string"),
              ("errorCode", "string"), ("retries", "number"), ("avg_ta", "number")]),
        {"x": 0, "y": 60, "w": 24, "h": 14},
        datatable("layer-p_origin_retries",
                  ["reqHost", "reqMethod", "reqPath", "errorCode", "retries", "avg_ta"]),
    ))
    panels.append(esql_lens_panel(
        "p_errorcode_by_path",
        "errorCode by host and path",
        "lnsDatatable",
        Q_ERRORCODE_BY_PATH,
        cols([("errorCode", "string"), ("reqHost", "string"), ("reqPath", "string"), ("errors", "number")]),
        {"x": 24, "y": 60, "w": 24, "h": 14},
        datatable("layer-p_errorcode_by_path",
                  ["errorCode", "reqHost", "reqPath", "errors"]),
    ))

    # Row 8: origin diagnostics
    panels.append(esql_lens_panel(
        "p_dns_slow",
        "DNS cold lookups p95 (ms) by host (empty = edge DNS cache warm)",
        "lnsDatatable",
        Q_DNS_SLOW_BY_HOST,
        cols([("reqHost", "string"), ("p95_dns_ms", "number"), ("lookups", "number")]),
        {"x": 0, "y": 74, "w": 16, "h": 12},
        datatable("layer-p_dns_slow", ["reqHost", "p95_dns_ms", "lookups"]),
    ))
    panels.append(esql_lens_panel(
        "p_breadcrumbs",
        "Multi-hop breadcrumbs (empty = direct edge-to-origin)",
        "lnsDatatable",
        Q_BREADCRUMBS_TOP,
        cols([("breadcrumbs", "string"), ("c", "number")]),
        {"x": 16, "y": 74, "w": 16, "h": 12},
        datatable("layer-p_breadcrumbs", ["breadcrumbs", "c"]),
    ))
    panels.append(esql_lens_panel(
        "p_uncacheable",
        "Non-cacheable paths going to origin",
        "lnsDatatable",
        Q_UNCACHEABLE_PATHS,
        cols([("reqHost", "string"), ("reqPath", "string"), ("rspContentType", "string"), ("c", "number")]),
        {"x": 32, "y": 74, "w": 16, "h": 12},
        datatable("layer-p_uncacheable", ["reqHost", "reqPath", "rspContentType", "c"]),
    ))

    # Bottom: raw table (reference to saved search)
    panels.append({
        "type": "search",
        "gridData": {"x": 0, "y": 86, "w": 48, "h": 18, "i": "p_raw"},
        "panelIndex": "p_raw",
        "panelRefName": "panel_p_raw",
        "title": "Raw logs (sort by time, drill into one request)",
        "version": "8.19.0",
        "embeddableConfig": {},
    })

    return panels


def build_controls():
    """
    Build the Options List (combobox) filter controls that appear above the
    dashboard. Each is multi-select, pulls distinct values from the data view,
    sorted by frequency.
    """
    def options_list(cid, field, title, width="medium", order=0):
        return {
            cid: {
                "type": "optionsListControl",
                "order": order,
                "grow": True,
                "width": width,
                "explicitInput": {
                    "id": cid,
                    "fieldName": field,
                    "title": title,
                    "dataViewId": IDX,
                    "searchTechnique": "prefix",
                    "selectedOptions": [],
                    "singleSelect": False,
                    "exclude": False,
                    "existsSelected": False,
                    "sort": {"by": "_count", "direction": "desc"},
                    "enhancements": {},
                },
            }
        }

    panels = {}
    panels.update(options_list("ctrl_host", "reqHost", "Host", width="large", order=0))
    panels.update(options_list("ctrl_status", "statusCode", "Status code", width="small", order=1))
    panels.update(options_list("ctrl_method", "reqMethod", "Method", width="small", order=2))
    panels.update(options_list("ctrl_cache", "cacheStatus", "Cache status", width="small", order=3))
    panels.update(options_list("ctrl_clip", "cliIP", "Client IP", width="medium", order=4))
    return panels


def create_dashboard():
    print("creating dashboard...")
    delete_if_exists("dashboard", DASH_ID)
    panels = build_dashboard_panels()
    controls = build_controls()

    body = {
        "attributes": {
            "title": "Akamai Debug",
            "description": "Debug-oriented view over DataStream 2 CDN logs. Filter by host/status/method/cache from the top bar, drill into a single request from the raw table at the bottom. Companion to Hideki Okamoto's 'Akamai' dashboard.",
            "panelsJSON": json.dumps(panels),
            "optionsJSON": json.dumps({
                "useMargins": True,
                "syncColors": False,
                "syncCursor": True,
                "syncTooltips": False,
                "hidePanelTitles": False,
            }),
            "timeRestore": True,
            "timeFrom": "now-1h",
            "timeTo": "now",
            "refreshInterval": {"pause": False, "value": 30000},
            "controlGroupInput": {
                "controlStyle": "oneLine",
                "chainingSystem": "HIERARCHICAL",
                "showApplySelections": False,
                "ignoreParentSettingsJSON": json.dumps({
                    "ignoreFilters": False,
                    "ignoreQuery": False,
                    "ignoreTimerange": False,
                    "ignoreValidations": False,
                }),
                "panelsJSON": json.dumps(controls),
            },
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps({
                    "query": {"query": "", "language": "kuery"},
                    "filter": [],
                }),
            },
            "version": 3,
        },
        "references": [
            {"name": "panel_p_raw", "type": "search", "id": RAW_SEARCH_ID},
            {"name": "controlGroup_ctrl_host:optionsListDataView", "type": "index-pattern", "id": IDX},
            {"name": "controlGroup_ctrl_status:optionsListDataView", "type": "index-pattern", "id": IDX},
            {"name": "controlGroup_ctrl_method:optionsListDataView", "type": "index-pattern", "id": IDX},
            {"name": "controlGroup_ctrl_cache:optionsListDataView", "type": "index-pattern", "id": IDX},
            {"name": "controlGroup_ctrl_clip:optionsListDataView", "type": "index-pattern", "id": IDX},
        ],
    }
    res = req("POST", f"/api/saved_objects/dashboard/{DASH_ID}?overwrite=true", body)
    print(f"  created id={res['id']}")
    return res


def export_ndjson():
    print("exporting canonical NDJSON...")
    body = {
        "objects": [
            {"type": "dashboard", "id": DASH_ID},
        ],
        "includeReferencesDeep": True,
    }
    url = f"{KIBANA_URL}/api/saved_objects/_export"
    data = json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(r, timeout=60) as resp:
        content = resp.read().decode()
    out_path = Path(__file__).resolve().parent.parent / "kibana" / "akamai-debug.ndjson"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content)
    lines = content.strip().split("\n")
    print(f"  wrote {out_path} ({len(lines)} saved objects)")


def main():
    print(f"Target Kibana: {KIBANA_URL}")
    create_dataview()
    create_raw_search()
    create_dashboard()
    export_ndjson()
    print("done.")


if __name__ == "__main__":
    main()
