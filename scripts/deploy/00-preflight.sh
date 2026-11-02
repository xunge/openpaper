#!/usr/bin/env bash
# Preflight: check OS, Docker, GPU, disk, ports and deploy.env.
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"

load_env

log "checking OS"
case "$(uname -s)" in
  Linux) ok "Linux" ;;
  *) die "this deployment expects Linux; got $(uname -s)" ;;
esac
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  log "distribution: ${PRETTY_NAME:-unknown}"
fi

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    ok "docker accessible: $(docker --version)"
    docker compose version >/dev/null 2>&1 && ok "docker compose: $(docker compose version --short)"
  else
    warn "docker is installed but not accessible for $USER; re-login or add to docker group"
  fi
else
  warn "docker is not installed; run 10-install-docker.sh"
fi

if [[ "${SKIP_GPU:-0}" == "1" ]]; then
  warn "GPU checks skipped (SKIP_GPU=1)"
elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  ok "NVIDIA driver: $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
  nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
else
  warn "nvidia-smi is not available; install/verify the NVIDIA driver before deploying MinerU"
fi

log "checking free disk on /"
available_gb="$(df -Pk / | awk 'NR==2 {print int($4/1024/1024)}')"
min_disk_gb="${MIN_DISK_GB:-150}"
if (( available_gb < min_disk_gb )); then
  warn "only ${available_gb} GB free on / (recommended: ${min_disk_gb} GB, MinerU image is ~25 GB)"
else
  ok "${available_gb} GB free on /"
fi

log "checking ports"
for pair in \
  "CLIENT_PORT:80" \
  "SERVER_PORT:8100" \
  "JOBS_PORT:8101" \
  "MINIO_PORT:9000" \
  "MINIO_CONSOLE_PORT:9001" \
  "POSTGRES_PORT:5433" \
  "RABBITMQ_PORT:5673" \
  "REDIS_PORT:6380" \
  "MINERU_API_PORT:8000" \
  "MINERU_FILE_PARSE_PORT:8003"; do
  name="${pair%%:*}"
  default="${pair##*:}"
  value="${!name:-$default}"
  if command -v ss >/dev/null 2>&1 && ss -ltn "sport = :$value" 2>/dev/null | grep -q LISTEN; then
    warn "port $value ($name) is already in use; use another port in deploy.env or stop the existing service"
  fi
done

if [[ -f "$DEPLOY_ENV" ]]; then
  missing=()
  for name in SERVER_IP CLIENT_DOMAIN API_DOMAIN DEEPSEEK_API_KEY MINIO_ROOT_USER MINIO_ROOT_PASSWORD AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; do
    [[ -n "${!name:-}" ]] || missing+=("$name")
  done
  if (( ${#missing[@]} > 0 )); then
    warn "deploy.env still has empty required values: ${missing[*]}"
  else
    ok "deploy.env required values are present"
  fi
  if [[ "${S3_ENDPOINT_URL:-}" == *"minio:9000"* ]]; then
    die "S3_ENDPOINT_URL must be browser/container reachable, not minio:9000"
  fi
  if [[ "${S3_ENDPOINT_URL:-}" == *"127.0.0.1"* || "${S3_PUBLIC_URL:-}" == *"127.0.0.1"* ]]; then
    warn "S3 URLs contain 127.0.0.1; a remote browser cannot reach that"
  fi
else
  warn "deploy.env not found; env rendering and service startup will not work yet"
fi

ok "preflight finished"
