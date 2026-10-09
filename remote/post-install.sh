#!/usr/bin/env bash
#
# Post-install steps on top of Hideki Okamoto's StackScript 1059555.
# deploy.py copies this directory to the instance and runs it as root (sudo)
# once the StackScript has finished. Every step is idempotent: re-running it
# skips what is already done.
#
#   1. Move Elasticsearch data onto the attached Block Storage volume.
#   2. Keep the disk from filling up: retention and force merge in the
#      datastream2-ilm policy, best_compression and 0 replicas in the index
#      template, and a systemd timer that deletes the oldest indices when
#      the disk passes a threshold.
#   3. Optionally front Elasticsearch with nginx + Let's Encrypt on 443.
#   4. Import the Akamai Debug dashboard into Kibana.
#
# Settings come from post-install.env next to this script (written by
# deploy.py, removed on exit):
#   ES_PASSWORD       password of the 'elastic' user
#   VOLUME_DEVICE     /dev/disk/by-id/... of the data volume, empty = skip
#   RETENTION_DAYS    delete indices after N days, 0 = keep forever
#   DISK_MAX_PCT      delete oldest indices above this disk usage, 0 = off
#   IMPORT_DASHBOARD  yes|no
#   TLS_HOSTNAME      hostname for the certificate, empty = no TLS
#   TLS_EMAIL         Let's Encrypt account email, may be empty

set -euo pipefail
exec </dev/null

HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$HERE/post-install.env"
trap 'rm -f "$ENV_FILE"' EXIT
# shellcheck source=/dev/null
source "$ENV_FILE"

ES_URL="http://localhost:9200"
KIBANA_URL="http://localhost:5601"

DATA_DIR="$(awk -F': *' '/^path\.data:/ {print $2}' /etc/elasticsearch/elasticsearch.yml | tr -d '"' | head -1)"
DATA_DIR="${DATA_DIR:-/var/lib/elasticsearch}"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

wait_for() {
  # wait_for <description> <command...>
  local what="$1"; shift
  for _ in $(seq 1 60); do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 5
  done
  die "$what did not become ready in 5 minutes"
}

es_ready() { curl -fsS -u "elastic:$ES_PASSWORD" "$ES_URL/_cluster/health?wait_for_status=yellow&timeout=5s"; }
kibana_ready() { curl -fsS -u "elastic:$ES_PASSWORD" "$KIBANA_URL/api/status"; }

########################################
# 1. Data volume
########################################

mount_volume() {
  if [ -z "${VOLUME_DEVICE:-}" ]; then
    log "No data volume requested, Elasticsearch keeps its data on the instance disk"
    return
  fi

  local data_dir="$DATA_DIR"

  if mountpoint -q "$data_dir"; then
    log "$data_dir is already on its own mount, skipping volume setup"
    return
  fi

  log "Moving Elasticsearch data ($data_dir) to $VOLUME_DEVICE"
  wait_for "Volume $VOLUME_DEVICE" test -b "$VOLUME_DEVICE"

  if ! blkid "$VOLUME_DEVICE" >/dev/null 2>&1; then
    mkfs.ext4 -q -L es-data "$VOLUME_DEVICE"
  fi

  systemctl stop kibana elasticsearch

  local tmp
  tmp="$(mktemp -d)"
  mount "$VOLUME_DEVICE" "$tmp"
  rmdir "$tmp/lost+found" 2>/dev/null || true
  cp -a "$data_dir/." "$tmp/"
  umount "$tmp"
  rmdir "$tmp"

  mv "$data_dir" "$data_dir.pre-volume"
  mkdir "$data_dir"
  if ! grep -q "^$VOLUME_DEVICE " /etc/fstab; then
    echo "$VOLUME_DEVICE $data_dir ext4 defaults,noatime,nofail 0 2" >> /etc/fstab
  fi
  systemctl daemon-reload
  mount "$data_dir"
  chown --reference="$data_dir.pre-volume" "$data_dir"
  chmod --reference="$data_dir.pre-volume" "$data_dir"

  # Never let Elasticsearch start on the bare mountpoint if the volume is missing.
  mkdir -p /etc/systemd/system/elasticsearch.service.d
  printf '[Unit]\nRequiresMountsFor=%s\n' "$data_dir" \
    > /etc/systemd/system/elasticsearch.service.d/data-volume.conf
  systemctl daemon-reload

  systemctl start elasticsearch
  wait_for "Elasticsearch" es_ready
  systemctl start kibana
  rm -rf "$data_dir.pre-volume"
  df -h "$data_dir"
}

########################################
# 2. Disk protection
########################################

es_put() { curl -fsS -u "elastic:$ES_PASSWORD" -H 'Content-Type: application/json' -X PUT "$ES_URL$1" -d "$2"; echo; }

ilm_policy() {
  # Hideki's policy rolls over at 30d / 50gb and never deletes. With a
  # retention window, roll over daily so deletes happen close to it.
  local rollover='{"max_age":"30d","max_primary_shard_size":"50gb"}'
  local delete_phase=""
  if [ "${RETENTION_DAYS:-0}" -gt 0 ]; then
    rollover='{"max_age":"1d","max_primary_shard_size":"10gb"}'
    delete_phase=',"delete":{"min_age":"'"$RETENTION_DAYS"'d","actions":{"delete":{}}}'
    log "ILM: roll over daily (or at 10 GB), force merge, delete ${RETENTION_DAYS}d after rollover"
  else
    log "ILM: Hideki's rollover (30 days or 50 GB) plus force merge, indices are never deleted by age"
  fi
  # warm: once an index stops receiving writes, merge it to one segment and
  # recompress it with best_compression (frees space from deleted docs too).
  es_put /_ilm/policy/datastream2-ilm '{"policy":{"phases":{
    "hot":{"min_age":"0ms","actions":{"set_priority":{"priority":100},"rollover":'"$rollover"'}},
    "warm":{"min_age":"0ms","actions":{"set_priority":{"priority":50},"forcemerge":{"max_num_segments":1,"index_codec":"best_compression"}}}'"$delete_phase"'
  }}}'
}

index_settings() {
  log "Index template: best_compression, 0 replicas (single node)"
  ES_PASSWORD="$ES_PASSWORD" python3 - <<'PY'
import base64, json, os, urllib.request

auth = "Basic " + base64.b64encode(f"elastic:{os.environ['ES_PASSWORD']}".encode()).decode()

def call(method, path, body=None):
    req = urllib.request.Request("http://localhost:9200" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": auth, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b"{}")

name = "logs-akamai.datastream2"
template = call("GET", f"/_index_template/{name}")["index_templates"][0]["index_template"]
# Only send back what PUT accepts (GET can carry read-only metadata).
allowed = {"index_patterns", "template", "composed_of", "priority", "version", "_meta",
           "data_stream", "allow_auto_create", "ignore_missing_component_templates", "deprecated"}
template = {k: v for k, v in template.items() if k in allowed}
index = template.setdefault("template", {}).setdefault("settings", {}).setdefault("index", {})
index["codec"] = "best_compression"
index["number_of_replicas"] = "0"
call("PUT", f"/_index_template/{name}", template)
print(f"updated {name}")
PY
  # Existing indices: replicas can change live (codec applies at force merge).
  es_put "/datastream2-*/_settings" '{"index":{"number_of_replicas":0}}'
}

install_disk_guard() {
  if [ "${DISK_MAX_PCT:-0}" -le 0 ]; then
    log "Disk guard: disabled"
    systemctl disable --now ds2-elk-disk-guard.timer 2>/dev/null || true
    return
  fi
  log "Disk guard: delete the oldest datastream2 indices when $DATA_DIR passes ${DISK_MAX_PCT}%"
  install -m 0755 "$HERE/disk-guard.py" /usr/local/sbin/ds2-elk-disk-guard
  install -d -m 0700 /etc/ds2-elk
  ( umask 077
    printf 'ES_PASSWORD=%s\nMAX_PCT=%s\nDATA_DIR=%s\n' "$ES_PASSWORD" "$DISK_MAX_PCT" "$DATA_DIR" \
      > /etc/ds2-elk/disk-guard.env )

  cat > /etc/systemd/system/ds2-elk-disk-guard.service <<EOF
[Unit]
Description=Delete the oldest DataStream 2 indices when the disk is nearly full
After=elasticsearch.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ds2-elk-disk-guard
EOF
  cat > /etc/systemd/system/ds2-elk-disk-guard.timer <<EOF
[Unit]
Description=Run ds2-elk-disk-guard every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now ds2-elk-disk-guard.timer
  /usr/local/sbin/ds2-elk-disk-guard || true
}

protect_disk() {
  wait_for "Elasticsearch" es_ready
  ilm_policy
  index_settings
  install_disk_guard
}

########################################
# 3. HTTPS endpoint (nginx + Let's Encrypt)
########################################

setup_tls() {
  if [ -z "${TLS_HOSTNAME:-}" ]; then
    log "TLS: disabled, DataStream 2 posts over HTTP to port 9200"
    return
  fi

  log "TLS: nginx + Let's Encrypt for $TLS_HOSTNAME"
  export DEBIAN_FRONTEND=noninteractive
  if ! command -v nginx >/dev/null || ! command -v certbot >/dev/null; then
    apt-get update -q
    apt-get install -y -q nginx certbot
  fi

  # The StackScript enables UFW with 22, 5601 and 9200 only.
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null

  local site=/etc/nginx/sites-available/ds2-elk
  local cert_dir="/etc/letsencrypt/live/$TLS_HOSTNAME"
  mkdir -p /var/www/letsencrypt
  rm -f /etc/nginx/sites-enabled/default

  write_nginx_http() {
    cat > "$site" <<EOF
# Managed by ds2-elk-tf remote/post-install.sh
server {
    listen 80;
    listen [::]:80;
    server_name $TLS_HOSTNAME;

    location /.well-known/acme-challenge/ { root /var/www/letsencrypt; }
    location / { return 404; }
}
EOF
  }

  write_nginx_http
  ln -sf "$site" /etc/nginx/sites-enabled/ds2-elk
  nginx -t
  systemctl enable --now nginx
  systemctl reload nginx

  local email_args=(--register-unsafely-without-email)
  if [ -n "${TLS_EMAIL:-}" ]; then email_args=(-m "$TLS_EMAIL"); fi
  certbot certonly --webroot -w /var/www/letsencrypt -d "$TLS_HOSTNAME" \
    --non-interactive --agree-tos --keep-until-expiring "${email_args[@]}"

  write_nginx_http
  cat >> "$site" <<EOF

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $TLS_HOSTNAME;

    ssl_certificate     $cert_dir/fullchain.pem;
    ssl_certificate_key $cert_dir/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    client_max_body_size 100m;

    location / {
        proxy_pass http://127.0.0.1:9200;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;
    }
}
EOF
  nginx -t
  systemctl reload nginx

  mkdir -p /etc/letsencrypt/renewal-hooks/deploy
  printf '#!/bin/sh\nsystemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
  chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
}

########################################
# 4. Akamai Debug dashboard
########################################

import_dashboard() {
  if [ "${IMPORT_DASHBOARD:-yes}" != "yes" ]; then
    log "Akamai Debug dashboard: skipped"
    return
  fi
  log "Importing the Akamai Debug dashboard into Kibana"
  wait_for "Kibana" kibana_ready
  local out
  out="$(curl -sS -u "elastic:$ES_PASSWORD" -H 'kbn-xsrf: true' \
    -X POST "$KIBANA_URL/api/saved_objects/_import?overwrite=true" \
    --form file=@"$HERE/akamai-debug.ndjson")"
  case "$out" in
    *'"success":true'*) echo "Imported." ;;
    *) die "Dashboard import failed: $out" ;;
  esac
}

mount_volume
protect_disk
setup_tls
import_dashboard

log "Post-install finished"
