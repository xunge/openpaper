FROM python:3.12-slim

# sox is a runtime dependency of the qwen-tts package's audio helpers;
# libsndfile backs soundfile's WAV writing.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 \
        sox \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Domestic mirror by default: direct PyPI is unreachable on this network.
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    HF_ENDPOINT=https://hf-mirror.com \
    HF_HUB_ENABLE_HF_TRANSFER=0 \
    PYTHONUNBUFFERED=1 \
    TOKENIZERS_PARALLELISM=false

# torch from PyPI is the CUDA build on x86_64; the container borrows the host
# driver libs through the compose device mounts, so no CUDA base image is needed.
RUN pip install --index-url "$PIP_INDEX_URL" \
        torch \
        soundfile \
        fastapi \
        "uvicorn[standard]" \
        qwen-tts

# Triton (pulled in by torch) JIT-compiles a small C extension on the first
# synthesis call, so a compiler has to exist at runtime — not just at build
# time. Installed in its own layer after the Python deps so adding it does not
# invalidate the 14 GB dependency layer.
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libc6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY app.py /app/app.py

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
