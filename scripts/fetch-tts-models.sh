#!/usr/bin/env bash
#
# Fetch the Qwen3-TTS checkpoints used by the `tts` and `tts-vllm` services.
#
# The weights are ~4.8 GB and deliberately not in git: they are a build artifact
# of a public model, not source. This script is the reproducible way to get them
# onto a fresh machine. Override the destination with TTS_MODELS_DIR.
set -euo pipefail

DEST="${TTS_MODELS_DIR:-/home/jiang/models/qwen3-tts}"
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.12-slim}"

mkdir -p "$DEST"

if [ -f "$DEST/Qwen3-TTS-12Hz-1.7B-CustomVoice/model.safetensors" ]; then
    echo "weights already present in $DEST — nothing to do"
    du -sh "$DEST"
    exit 0
fi

echo "downloading Qwen3-TTS into $DEST (ModelScope mirror)"
docker run --rm -v "$DEST:/models" "$PYTHON_IMAGE" sh -c '
    set -e
    pip install -q --index-url https://pypi.tuna.tsinghua.edu.cn/simple modelscope
    modelscope download --model Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice \
        --local_dir /models/Qwen3-TTS-12Hz-1.7B-CustomVoice
    modelscope download --model Qwen/Qwen3-TTS-Tokenizer-12Hz \
        --local_dir /models/Qwen3-TTS-Tokenizer-12Hz
'

du -sh "$DEST"
echo "done. services read this path via TTS_MODELS_DIR (default: $DEST)"
