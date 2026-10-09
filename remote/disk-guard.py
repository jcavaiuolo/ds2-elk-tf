#!/usr/bin/env python3
"""
Disk guard for the DataStream 2 Elasticsearch node.

Installed by post-install.sh as /usr/local/sbin/ds2-elk-disk-guard and run by
the ds2-elk-disk-guard.timer every 15 minutes. When the Elasticsearch data
directory is above MAX_PCT used, it deletes the oldest datastream2-* indices
(never the current write index) until usage drops below the threshold.

ILM retention removes data by age; this removes it by space, so a traffic
spike cannot push the disk into Elasticsearch's flood-stage watermark (95%),
where indices turn read-only and DataStream 2 uploads start failing.

Settings: /etc/ds2-elk/disk-guard.env (ES_PASSWORD, MAX_PCT, DATA_DIR).
Logs go to the journal: journalctl -u ds2-elk-disk-guard
"""
import base64
import json
import os
import sys
import time
import urllib.request

CONFIG = "/etc/ds2-elk/disk-guard.env"
ES_URL = "http://localhost:9200"
ALIAS = "datastream2"


def load_config():
    config = {}
    with open(CONFIG) as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                config[key] = value.strip().strip("'\"")
    return config


def es(method, path, password):
    request = urllib.request.Request(f"{ES_URL}{path}", method=method)
    token = base64.b64encode(f"elastic:{password}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(request, timeout=60) as resp:
        return json.loads(resp.read() or b"{}")


def used_pct(path):
    stats = os.statvfs(path)
    used = (stats.f_blocks - stats.f_bfree) * stats.f_frsize
    avail = stats.f_bavail * stats.f_frsize
    return 100.0 * used / (used + avail)


def main():
    config = load_config()
    password = config["ES_PASSWORD"]
    max_pct = float(config.get("MAX_PCT", "75"))
    data_dir = config.get("DATA_DIR", "/var/lib/elasticsearch")

    pct = used_pct(data_dir)
    if pct < max_pct:
        print(f"{data_dir} at {pct:.1f}% (threshold {max_pct:g}%), nothing to do")
        return 0

    aliases = es("GET", f"/_alias/{ALIAS}", password)
    write_indices = {name for name, data in aliases.items()
                     if data["aliases"][ALIAS].get("is_write_index", len(aliases) == 1)}

    indices = es("GET", f"/_cat/indices/{ALIAS}-*?format=json&h=index,creation.date,store.size&s=creation.date",
                 password)
    candidates = [row for row in indices if row["index"] not in write_indices]

    for row in candidates:
        print(f"{data_dir} at {pct:.1f}% >= {max_pct:g}%: deleting oldest index {row['index']} ({row['store.size']})")
        es("DELETE", f"/{row['index']}", password)
        # Give Elasticsearch a moment to release the files before re-measuring.
        for _ in range(12):
            time.sleep(5)
            pct = used_pct(data_dir)
            if pct < max_pct:
                break
        if pct < max_pct:
            print(f"{data_dir} back to {pct:.1f}%")
            return 0

    print(f"WARNING: {data_dir} still at {pct:.1f}% and only the write index "
          f"({', '.join(sorted(write_indices)) or 'none'}) is left. Lower the DataStream 2 "
          "sampling rate, trim the data set, or grow the disk.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
