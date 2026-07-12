# Multi-stage Dockerfile for vep-link.

# --- Builder Stage ---
FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV VIRTUAL_ENV="/opt/venv" \
    PATH="/opt/venv/bin:$PATH"

RUN mkdir -p /home/app/web

WORKDIR /app
COPY uv.lock pyproject.toml README.md ./

# Digest-pinned uv from the official image (reproducible; no floating installer).
COPY --from=ghcr.io/astral-sh/uv:0.8.7@sha256:1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab /uv /usr/local/bin/uv

RUN uv sync --frozen --no-dev --active --no-install-project

# --- Production Stage ---
FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    VEP_LINK_MCP_TRANSPORT=unified \
    VEP_LINK_MCP_HOST=0.0.0.0 \
    VEP_LINK_MCP_PORT=8000 \
    VEP_LINK_MCP_PATH=/mcp \
    TMPDIR=/tmp/vep-link

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

RUN groupadd --system app && \
    useradd --system --gid app --home /home/app --create-home app && \
    mkdir -p /tmp/vep-link /var/cache/vep-link && \
    chown -R app:app /tmp/vep-link /var/cache/vep-link /home/app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /home/app/web

COPY --chown=app:app ./vep_link ./vep_link
COPY --chown=app:app ./pyproject.toml ./README.md ./

RUN /opt/venv/bin/pip install -e . --no-deps

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f -H 'Host: localhost' http://localhost:8000/health || exit 1

CMD ["vep-link", "serve", "--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
