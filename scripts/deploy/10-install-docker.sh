#!/usr/bin/env bash
# Install Docker Engine + Compose plugin on Ubuntu/Debian.
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"
require_root

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  ok "docker already installed: $(docker --version)"
  exit 0
fi

command -v apt-get >/dev/null 2>&1 || die "this installer supports apt-based distributions only"

if [[ "$DRY_RUN" == "1" ]]; then
  log "would install Docker from get.docker.com"
  exit 0
fi

log "installing Docker from get.docker.com"
if [[ "$DRY_RUN" == "1" ]]; then
  log "would run: curl -fsSL https://get.docker.com | sh"
else
  curl -fsSL https://get.docker.com | sh
fi

systemctl enable --now docker

DEPLOY_USER="${DEPLOY_USER:-${SUDO_USER:-}}"
if [[ -n "$DEPLOY_USER" && "$DEPLOY_USER" != "root" ]]; then
  usermod -aG docker "$DEPLOY_USER"
  ok "added $DEPLOY_USER to the docker group; that user must re-login (or use newgrp docker)"
fi

docker --version
docker compose version
ok "Docker installed"
