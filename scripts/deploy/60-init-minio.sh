#!/usr/bin/env bash
# Start stateful dependencies, create the MinIO bucket and browser CORS rule.
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"
load_env
require_docker
require_var SERVER_IP

minio_port="${MINIO_PORT:-9000}"
client_domain="$(normalize_url "${CLIENT_DOMAIN:-http://${SERVER_IP}}")"
log "starting postgres, rabbitmq, redis and minio"
if [[ "$DRY_RUN" == "1" ]]; then
  log "would run: docker compose up -d postgres rabbitmq redis minio"
else
  compose up -d postgres rabbitmq redis minio
fi

if [[ "$DRY_RUN" == "1" ]]; then
  ok "dry-run: would create bucket and CORS"
  exit 0
fi

wait_postgres 120 || die "postgres did not become ready"
wait_http "http://127.0.0.1:${minio_port}/minio/health/live" 120 3 || die "MinIO did not become ready"

log "creating bucket openpaper"
minio_mc mb --ignore-existing local/openpaper

cors_file="$(mktemp)"
trap 'rm -f "$cors_file"' EXIT
cat > "$cors_file" <<EOF
{
  "CORSRules": [
    {
      "AllowedOrigins": ["${client_domain}"],
      "AllowedMethods": ["GET", "HEAD"],
      "AllowedHeaders": ["*"],
      "ExposeHeaders": ["ETag", "Content-Length", "Content-Type"],
      "MaxAgeSeconds": 3600
    }
  ]
}
EOF

log "setting MinIO CORS for ${client_domain}"
compose cp "$cors_file" minio:/tmp/openpaper-cors.json
minio_mc cors set local/openpaper /tmp/openpaper-cors.json
minio_mc ls local/openpaper

ok "MinIO bucket and CORS are ready"
