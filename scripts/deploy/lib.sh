#!/usr/bin/env bash
# Shared helpers for scripts/deploy. Source this file; do not execute directly.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/docker-compose.dev.yml}"
DEPLOY_ENV="${DEPLOY_ENV:-$ROOT_DIR/deploy.env}"
DRY_RUN="${DRY_RUN:-0}"

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'
  C_INFO=$'\033[36m'
  C_OK=$'\033[32m'
  C_WARN=$'\033[33m'
  C_ERR=$'\033[31m'
else
  C_RESET="" C_INFO="" C_OK="" C_WARN="" C_ERR=""
fi

log()  { printf '%s[deploy]%s %s\n' "$C_INFO" "$C_RESET" "$*"; }
ok()   { printf '%s[ ok ]%s %s\n' "$C_OK" "$C_RESET" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$C_WARN" "$C_RESET" "$*" >&2; }
die()  { printf '%s[error]%s %s\n' "$C_ERR" "$C_RESET" "$*" >&2; exit 1; }

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '+'; printf ' %q' "$@"; printf '\n'
  else
    "$@"
  fi
}

load_env() {
  if [[ -f "$DEPLOY_ENV" ]]; then
    log "loading $DEPLOY_ENV"
    set -a
    # shellcheck disable=SC1090
    . "$DEPLOY_ENV"
    set +a
  else
    warn "deploy env not found: $DEPLOY_ENV (copy deploy.env.example first)"
    return 0
  fi
}

require_env_file() {
  [[ -f "$DEPLOY_ENV" ]] || die "missing $DEPLOY_ENV; cp deploy.env.example deploy.env and edit it"
}

require_var() {
  local name="$1"
  [[ -n "${!name:-}" ]] || die "required variable is empty: $name"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

require_root() {
  [[ "${EUID}" -eq 0 ]] || die "run this script as root (or: sudo $0)"
}

require_docker() {
  require_cmd docker
  docker info >/dev/null 2>&1 || die "docker daemon is not accessible; re-login after adding your user to the docker group"
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is required"
}

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

compose_project() {
  local name
  name="$(docker compose -f "$COMPOSE_FILE" config --format json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("name",""))' 2>/dev/null || true)"
  [[ -n "$name" ]] || name="openpaper-dev"
  printf '%s' "$name"
}

network_name() {
  printf '%s_default' "$(compose_project)"
}

wait_http() {
  local url="$1" timeout="${2:-300}" interval="${3:-5}" waited=0
  if [[ "$DRY_RUN" == "1" ]]; then
    log "would wait for $url"
    return 0
  fi
  log "waiting for $url"
  while (( waited < timeout )); do
    if curl -fsS -o /dev/null --max-time 5 "$url"; then
      ok "ready: $url"
      return 0
    fi
    sleep "$interval"
    waited=$((waited + interval))
  done
  return 1
}

wait_postgres() {
  local timeout="${1:-120}" waited=0
  if [[ "$DRY_RUN" == "1" ]]; then
    log "would wait for postgres"
    return 0
  fi
  log "waiting for postgres"
  while (( waited < timeout )); do
    if compose exec -T postgres pg_isready -U postgres -d annotated-paper >/dev/null 2>&1; then
      ok "postgres ready"
      return 0
    fi
    sleep 3
    waited=$((waited + 3))
  done
  return 1
}

minio_mc() {
  local user="${MINIO_ROOT_USER:-minioadmin}"
  local pass="${MINIO_ROOT_PASSWORD:-minioadmin}"
  compose exec -T -e "MC_HOST_local=http://${user}:${pass}@minio:9000" minio mc "$@"
}

gen_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    python3 -c 'import secrets; print(secrets.token_hex(32))'
  fi
}

normalize_url() {
  local url="$1"
  printf '%s' "${url%/}"
}
