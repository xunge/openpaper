#!/usr/bin/env bash
# Install NVIDIA Container Toolkit only. It does NOT install the host driver.
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"
require_root

if [[ "${SKIP_GPU:-0}" == "1" ]]; then
  warn "SKIP_GPU=1; skipping NVIDIA Container Toolkit installation"
  exit 0
fi

if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi >/dev/null 2>&1; then
  die "nvidia-smi failed. Install a supported NVIDIA driver (MinerU image uses CUDA 13) and reboot first."
fi

if [[ "$DRY_RUN" == "1" ]]; then
  log "would install/configure nvidia-container-toolkit (driver already present)"
  exit 0
fi

if command -v nvidia-ctk >/dev/null 2>&1 && docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
  ok "NVIDIA Container Toolkit already configured"
else
  log "installing nvidia-container-toolkit"
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl gnupg
  install -m 0755 -d /usr/share/keyrings
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update
  apt-get install -y nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
fi

log "testing GPU in a container"
docker run --rm --gpus all "${GPU_TEST_IMAGE:-nvidia/cuda:12.4.0-base-ubuntu22.04}" nvidia-smi
ok "NVIDIA Container Toolkit is ready"
