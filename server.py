"""ASGI entrypoint: exposes ``app`` for ``uvicorn server:app`` / gunicorn."""

from __future__ import annotations

from vep_link.server_manager import build_app

app = build_app()
