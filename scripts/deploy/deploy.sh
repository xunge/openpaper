#!/usr/bin/env bash
# Entry point for the new-server deployment.
#
#   cp deploy.env.example deploy.env   # edit it first
#   scripts/deploy/deploy.sh all
#
# Stages can also be run individually:
#   scripts/deploy/deploy.sh docker
#   scripts/deploy/deploy.sh nvidia
#   scripts/deploy/deploy.sh mineru
#   scripts/deploy/deploy.sh env
#   scripts/deploy/deploy.sh minio
#   scripts/deploy/deploy.sh openpaper
#   scripts/deploy/deploy.sh admin
#   scripts/deploy/deploy.sh verify
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"

DIR="$SCRIPT_DIR"

usage() {
  cat <<'EOF'
Usage: scripts/deploy/deploy.sh <command>

Commands:
  preflight   Check OS, Docker, GPU, disk, ports and deploy.env
  docker      Install Docker Engine + Compose
  nvidia      Install NVIDIA Container Toolkit (driver must exist)
  mineru      Load MinerU image and start API/file-parse
  env         Render server/.env, jobs/.env, client/.env.local
  minio       Start dependencies, create bucket and CORS
  openpaper   Build/start OpenPaper (excludes TTS/legacy OCR)
  admin       Create the first admin account (interactive)
  verify      Health checks
  all         preflight -> docker -> nvidia -> mineru -> env -> minio -> openpaper -> verify
  help        This help

Environment:
  DEPLOY_ENV=/opt/openpaper/deploy.env   (default: <repo>/deploy.env)
  DRY_RUN=1                              Print commands without executing
  SKIP_GPU=1                             Skip GPU/toolkit checks
  FORCE_ENV=1                            Overwrite existing env files
  FORCE_MINERU_RECREATE=1                Recreate MinerU containers
EOF
}

run_root_script() {
  if [[ "${EUID}" -eq 0 ]]; then
    bash "$1"
  else
    sudo bash "$1"
  fi
}

require_deploy_env() {
  [[ -f "$DEPLOY_ENV" ]] || die "missing $DEPLOY_ENV; cp deploy.env.example deploy.env and edit it"
}

cmd="${1:-help}"
case "$cmd" in
  help|-h|--help) usage ;;
  preflight) bash "$DIR/00-preflight.sh" ;;
  docker) run_root_script "$DIR/10-install-docker.sh" ;;
  nvidia) run_root_script "$DIR/20-install-nvidia.sh" ;;
  mineru)
    bash "$DIR/30-load-mineru.sh"
    bash "$DIR/40-start-mineru.sh"
    ;;
  env) require_deploy_env; bash "$DIR/50-init-env.sh" ;;
  minio) require_deploy_env; bash "$DIR/60-init-minio.sh" ;;
  openpaper) require_deploy_env; bash "$DIR/70-start-openpaper.sh" ;;
  admin) require_deploy_env; bash "$DIR/80-create-admin.sh" ;;
  verify) require_deploy_env; bash "$DIR/90-verify.sh" ;;
  all)
    require_deploy_env
    bash "$DIR/00-preflight.sh"
    if ! command -v docker >/dev/null 2>&1; then
      run_root_script "$DIR/10-install-docker.sh"
    fi
    if ! docker info >/dev/null 2>&1; then
      die "docker is installed but not accessible. Re-login (or run: newgrp docker) and rerun."
    fi
    if [[ "${SKIP_GPU:-0}" != "1" ]]; then
      run_root_script "$DIR/20-install-nvidia.sh"
    fi
    bash "$DIR/30-load-mineru.sh"
    bash "$DIR/40-start-mineru.sh"
    bash "$DIR/50-init-env.sh"
    bash "$DIR/60-init-minio.sh"
    bash "$DIR/70-start-openpaper.sh"
    bash "$DIR/90-verify.sh"
    log "Admin account is not created automatically; run:"
    log "  scripts/deploy/deploy.sh admin"
    ;;
  *) usage; die "unknown command: $cmd" ;;
esac
