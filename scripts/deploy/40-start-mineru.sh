#!/usr/bin/env bash
# Start MinerU API + file-parse (and optional webui) on the host.
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"
load_env
require_docker

image="${MINERU_IMAGE:-mineru:4}"
gpu_id="${MINERU_GPU_ID:-0}"
api_port="${MINERU_API_PORT:-8000}"
fileparse_port="${MINERU_FILE_PARSE_PORT:-8003}"
tier="${MINERU_TIER:-standard}"
base_dir="${MINERU_DIR:-/opt/mineru}"
api_name="mineru-api"
fileparse_name="mineru-fileparse"
webui_name="mineru-webui"
force="${FORCE_MINERU_RECREATE:-0}"

docker image inspect "$image" >/dev/null 2>&1 || die "MinerU image '$image' not found; run 30-load-mineru.sh"

ensure_dir() {
  local dir="$1"
  if [[ -d "$dir" && -w "$dir" ]]; then
    return 0
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    log "would create $dir"
  elif [[ "${EUID}" -eq 0 ]]; then
    mkdir -p "$dir"
  else
    sudo mkdir -p "$dir"
    sudo chown "$(id -u):$(id -g)" "$dir"
  fi
}

ensure_dir "$base_dir"
ensure_dir "$base_dir/input"
ensure_dir "$base_dir/output"
ensure_dir "$base_dir/state/fileparse"

docker network inspect mineru >/dev/null 2>&1 || {
  log "creating docker network mineru"
  run docker network create mineru
}

container_exists() { docker container inspect "$1" >/dev/null 2>&1; }
container_running() { [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" == "true" ]]; }

start_existing_or_create() {
  local name="$1"
  shift
  if container_exists "$name"; then
    if [[ "$force" == "1" ]]; then
      log "recreating $name"
      run docker rm -f "$name"
    elif container_running "$name"; then
      ok "$name already running"
      return 0
    else
      log "starting existing $name"
      run docker start "$name"
      return 0
    fi
  fi
  log "creating $name"
  run docker run -d \
    --name "$name" \
    --restart unless-stopped \
    "$@"
}

start_existing_or_create "$api_name" \
  --gpus "device=${gpu_id}" \
  --shm-size=16g \
  --network mineru \
  -p "${api_port}:8000" \
  -v "$base_dir/input:/workspace/input:ro" \
  -v "$base_dir/output:/workspace/output" \
  "$image" \
  api-server --host 0.0.0.0 --port 8000 --tier "$tier"

if ! wait_http "http://127.0.0.1:${api_port}/v1/health" 600 10; then
  docker logs --tail 100 "$api_name" >&2 || true
  die "mineru-api did not become healthy"
fi

start_existing_or_create "$fileparse_name" \
  --network mineru \
  -p "${fileparse_port}:8003" \
  -v "$base_dir/state/fileparse:/state" \
  "$image" \
  file-parse-server \
    --host 0.0.0.0 \
    --port 8003 \
    --api-url http://mineru-api:8000 \
    --state-dir /state

if ! wait_http "http://127.0.0.1:${fileparse_port}/docs" 180 5; then
  docker logs --tail 100 "$fileparse_name" >&2 || true
  die "mineru-fileparse did not become ready"
fi

if [[ "${MINERU_WEBUI:-0}" == "1" ]]; then
  start_existing_or_create "$webui_name" \
    --network mineru \
    -p "7860:7860" \
    "$image" \
    webui --server-name 0.0.0.0 --server-port 7860 --api-url http://mineru-api:8000
fi

ok "MinerU ready: API :$api_port, file-parse :$fileparse_port"
log "OpenPaper must use host.docker.internal:${fileparse_port} and :${api_port}"
