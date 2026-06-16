"""Exception hierarchy for vep-link.

Each exception maps to a structured MCP ``error.code`` in
``vep_link.mcp.errors`` (see the ``_EXCEPTION_CODE_MAP`` there). Keeping the
mapping centralized lets tools raise domain exceptions and have the facade
translate them into the deterministic error envelope.
"""

from __future__ import annotations


class VepLinkError(Exception):
    """Base class for all vep-link domain errors."""


class ConfigurationError(VepLinkError):
    """Invalid or missing configuration."""


class VariantParseError(VepLinkError):
    """Raw input could not be classified as a supported variant format.

    Maps to ``invalid_input``.
    """


class UnsupportedContigError(VariantParseError):
    """The variant's contig is outside the supported scope (e.g. MT liftover).

    Maps to ``unsupported_input``.
    """


class UpstreamInputError(VepLinkError):
    """Ensembl rejected the request as malformed (4xx). Maps to ``not_found``/``invalid_input``."""


class RateLimitedError(VepLinkError):
    """Upstream returned HTTP 429 (after retries) or local concurrency saturated.

    Maps to ``rate_limited`` (retryable).
    """


class UpstreamTimeoutError(VepLinkError):
    """An upstream request timed out. Maps to ``upstream_timeout`` (retryable)."""


class EnsemblApiError(VepLinkError):
    """A 5xx or transport-level upstream fault. Maps to ``upstream_unavailable`` (retryable)."""


class DataNotFoundError(VepLinkError):
    """The variant resolved cleanly but no data/mapping was found. Maps to ``not_found``."""


class AmbiguousMappingError(VepLinkError):
    """A liftover produced more than one mapping. Maps to ``ambiguous``."""
