#!/usr/bin/env bash
# Create the first local admin account. Password is prompted, not echoed.
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"
load_env
require_docker

email="${ADMIN_EMAIL:-}"
name="${ADMIN_NAME:-Admin}"

if [[ -z "$email" && ! -t 0 ]]; then
  die "set ADMIN_EMAIL or run this script interactively"
fi
if [[ -z "$email" ]]; then
  read -r -p "Admin email: " email
fi
[[ -n "$email" ]] || die "email is required"

args=(--email "$email" --name "$name" --admin)
if [[ -n "${ADMIN_PASSWORD:-}" ]]; then
  warn "ADMIN_PASSWORD is set; it may be visible in your shell history/process list"
  args+=(--password "$ADMIN_PASSWORD")
fi

compose exec server python -m app.scripts.set_password "${args[@]}"
ok "admin account ready: $email"
