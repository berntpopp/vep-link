"""Build-hardening guard: the Docker builder must bootstrap uv reproducibly (F-19).

Primitive P-A: a floating ``pip install --upgrade pip uv`` pulls an unpinned
installer at build time (supply-chain drift). Replace it with a digest-pinned
COPY from the official uv image so the toolchain is byte-reproducible, and it
must land before ``uv sync`` so uv exists on the PATH.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # tests/unit/ -> repo root
DOCKERFILE = ROOT / "docker" / "Dockerfile"

UV_COPY = (
    "COPY --from=ghcr.io/astral-sh/uv:0.8.7@sha256:"
    "1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab "
    "/uv /usr/local/bin/uv"
)


def test_dockerfile_pins_uv_and_has_no_floating_pip_upgrade() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "pip install --upgrade" not in text, "floating pip/uv upgrade must be removed"
    assert UV_COPY in text, "builder must COPY the digest-pinned uv binary"


def test_uv_copy_precedes_uv_sync() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "uv sync" in text, "builder should still run uv sync"
    assert text.index(UV_COPY) < text.index("uv sync"), "uv must be COPYed before uv sync"


def test_runtime_image_removes_unused_pip_installations() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "/usr/local/lib/python*/site-packages/pip" in text
    assert "/opt/venv/lib/python*/site-packages/pip" in text
    assert "/usr/local/bin/pip3.14" in text
    assert "/opt/venv/bin/pip3.14" in text
