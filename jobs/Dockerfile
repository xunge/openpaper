FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Same reasoning as the server image: the index is configurable, with a
# domestic default because direct PyPI is unreachable on some deployments.
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

# Install uv for faster package management
RUN pip install --index-url "$PIP_INDEX_URL" uv

# Source and dependency files
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY scripts/ ./scripts/
RUN chmod +x scripts/*.sh

# The non-root user is created *before* dependencies are installed, and owns the
# app dir from the start. Upstream installs as root and then runs a recursive
# chown, which on overlayfs copies up every hardlinked package file — gigabytes
# of I/O that reads as a hung build on a loaded host. This ordering needs no
# chown over the venv at all.
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app

USER appuser

# Dependencies come from uv.lock via an explicit export into /app/.venv.
# Upstream's `uv sync --frozen` re-resolves the project itself over the network,
# which stalls indefinitely on a network where only a package mirror is
# reachable. Exporting the lockfile needs no resolution, and installing into
# /app/.venv keeps `source .venv/bin/activate` working in the start scripts.
RUN uv export --frozen --no-dev --no-emit-project -o requirements.txt \
    && uv venv /app/.venv \
    && uv pip install --python /app/.venv/bin/python \
        --default-index "$PIP_INDEX_URL" -r requirements.txt

# Health check for ECS
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

# Expose port for FastAPI (matching your script's port 8001)
EXPOSE 8001

# Default command - will be overridden in ECS task definitions
CMD ["python", "--version"]
