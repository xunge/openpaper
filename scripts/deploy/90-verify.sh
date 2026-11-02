#!/usr/bin/env bash
# Non-destructive health checks for the whole stack.
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"
load_env
require_docker

failures=0
check_http() {
  local label="$1" url="$2"
  if curl -fsS -o /dev/null --max-time 10 "$url"; then
    ok "$label: $url"
  else
    warn "$label failed: $url"
    failures=$((failures + 1))
  fi
}

server_port="${SERVER_PORT:-8100}"
jobs_port="${JOBS_PORT:-8101}"
client_port="${CLIENT_PORT:-80}"
minio_port="${MINIO_PORT:-9000}"
mineru_api_port="${MINERU_API_PORT:-8000}"
mineru_fileparse_port="${MINERU_FILE_PARSE_PORT:-8003}"

check_http "OpenPaper API" "http://127.0.0.1:${server_port}/docs"
check_http "Jobs API" "http://127.0.0.1:${jobs_port}/health"
check_http "Next.js client" "http://127.0.0.1:${client_port}/"
check_http "MinIO" "http://127.0.0.1:${minio_port}/minio/health/live"
check_http "MinerU API" "http://127.0.0.1:${mineru_api_port}/v1/health"
check_http "MinerU file-parse" "http://127.0.0.1:${mineru_fileparse_port}/docs"

log "containers"
docker ps --filter name=mineru- --format 'table {{.Names}}\t{{.Status}}' || true
compose ps || true

log "MinIO bucket"
if minio_mc ls local/openpaper >/dev/null 2>&1; then
  ok "MinIO bucket openpaper exists"
else
  warn "MinIO bucket check failed (run 60-init-minio.sh)"
  failures=$((failures + 1))
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader || true
fi

echo
if (( failures > 0 )); then
  die "$failures check(s) failed"
fi
ok "all checks passed"
log "open ${CLIENT_DOMAIN:-http://127.0.0.1:${client_port}}/login"
