#!/usr/bin/env bash
# Load or pull the MinerU image. Models are baked into this image.
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"
load_env
require_docker

image="${MINERU_IMAGE:-mineru:4}"
tar="${MINERU_IMAGE_TAR:-}"
registry="${MINERU_IMAGE_REGISTRY:-}"

if docker image inspect "$image" >/dev/null 2>&1 && [[ "${FORCE_MINERU_PULL:-0}" != "1" ]]; then
  ok "MinerU image already present: $image"
  exit 0
fi

if [[ -n "$tar" && -f "$tar" ]]; then
  log "loading MinerU image from $tar (this may take several minutes)"
  if [[ "$DRY_RUN" == "1" ]]; then
    log "would load $tar"
  elif [[ "$tar" == *.gz ]]; then
    gunzip -c "$tar" | docker load
  else
    docker load -i "$tar"
  fi
elif [[ -n "$registry" ]]; then
  log "pulling $registry"
  if [[ "$DRY_RUN" == "1" ]]; then
    log "would pull $registry and tag $image"
  else
    docker pull "$registry"
    docker tag "$registry" "$image"
  fi
else
  die "no MinerU image found. Set MINERU_IMAGE_TAR to a saved image, or MINERU_IMAGE_REGISTRY to a registry image, or build it with README section 6.1."
fi

if [[ "$DRY_RUN" == "1" ]]; then
  ok "dry-run: MinerU image would be ready"
  exit 0
fi
docker image inspect "$image" >/dev/null
ok "MinerU image ready: $image"
