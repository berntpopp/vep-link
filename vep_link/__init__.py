"""vep-link — unified REST + MCP server for Ensembl VEP and Variant Recoder.

Annotates, recodes, resolves, and lifts over human genetic variants across both
reference assemblies (GRCh38 via rest.ensembl.org, GRCh37 via
grch37.rest.ensembl.org). Research use only; not for clinical decision support.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vep-link")
except PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0"

__all__ = ["__version__"]
