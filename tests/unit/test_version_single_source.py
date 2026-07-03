"""Guard: pyproject -> installed metadata -> __version__ -> MCP serverInfo are one value.

The distribution version declared in ``pyproject.toml`` (`[project].version`) is
the single source of truth. It flows to installed metadata at build time,
``vep_link.__version__`` derives from that metadata, and the FastMCP facade
advertises it as ``serverInfo.version`` on ``initialize`` — so a client sees the
package version, not the FastMCP framework version.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

from vep_link import __version__
from vep_link.mcp.facade import create_vep_mcp

DIST = "vep-link"


def _pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def test_pyproject_is_the_single_source() -> None:
    assert version(DIST) == _pyproject_version()


def test_dunder_version_is_metadata_derived() -> None:
    assert __version__ == version(DIST)


def test_mcp_server_info_version_matches_package() -> None:
    mcp = create_vep_mcp(service_factory=lambda: None)
    assert mcp.version == __version__
