#!/usr/bin/env bash
# Build and start the OpenPaper application services only.
# Deliberately excludes tts/tts-vllm/ocr/ocr-xiaomi so MinerU owns the GPU.
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"
load_env
require_docker

for file in "$ROOT_DIR/server/.env" "$ROOT_DIR/jobs/.env" "$ROOT_DIR/client/.env.local"; do
  [[ -f "$file" ]] || die "missing $file; run 50-init-env.sh first"
done

build_flag="--build"
[[ "${SKIP_BUILD:-0}" == "1" ]] && build_flag=""

log "building and starting OpenPaper"
if [[ "$DRY_RUN" == "1" ]]; then
  log "would run: docker compose up -d ${build_flag} postgres rabbitmq redis minio migrate server jobs-api jobs-worker jobs-beat client"
else
  compose up -d $build_flag \
    postgres rabbitmq redis minio migrate \
    server jobs-api jobs-worker jobs-beat client
fi

if [[ "$DRY_RUN" == "1" ]]; then
  ok "dry-run: would wait for services"
  exit 0
fi

wait_postgres 120 || die "postgres did not become ready"

server_port="${SERVER_PORT:-8100}"
jobs_port="${JOBS_PORT:-8101}"
client_port="${CLIENT_PORT:-80}"

if ! wait_http "http://127.0.0.1:${server_port}/docs" 300 5; then
  compose logs --tail=150 server >&2 || true
  die "OpenPaper server did not become ready"
fi
if ! wait_http "http://127.0.0.1:${jobs_port}/health" 180 5; then
  compose logs --tail=150 jobs-api >&2 || true
  die "jobs-api did not become ready"
fi
if ! wait_http "http://127.0.0.1:${client_port}/" 300 5; then
  compose logs --tail=150 client >&2 || true
  die "Next.js client did not become ready"
fi

ok "OpenPaper is running"
log "client:  ${CLIENT_DOMAIN:-http://127.0.0.1:${client_port}}"
log "api:     http://${SERVER_IP}:${server_port}/docs"
log "jobs:    http://${SERVER_IP}:${jobs_port}/health"
