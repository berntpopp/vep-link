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

    Maps to canonical ``invalid_input`` (subcode ``unsupported_input``).
    """


class UpstreamInputError(VepLinkError):
    """Ensembl rejected the request as malformed (4xx). Maps to ``not_found``/``invalid_input``."""


class RateLimitedError(VepLinkError):
    """Upstream returned HTTP 429 (after retries) or local concurrency saturated.

    Maps to ``rate_limited`` (retryable).
    """


class UpstreamTimeoutError(VepLinkError):
    """An upstream request timed out.

    Maps to canonical ``upstream_unavailable`` (subcode ``upstream_timeout``);
    retryable.
    """


class EnsemblApiError(VepLinkError):
    """A 5xx or transport-level upstream fault. Maps to ``upstream_unavailable`` (retryable)."""


class DataNotFoundError(VepLinkError):
    """The variant resolved cleanly but no data/mapping was found. Maps to ``not_found``."""


class AmbiguousMappingError(VepLinkError):
    """A liftover produced more than one mapping. Maps to ``ambiguous_query``."""


class DisallowedURLError(VepLinkError):
    """An outbound request/redirect hop violated the URL policy. NON-RETRYABLE.

    Raised by the outbound URL guard (see ``vep_link.api.url_guard``) when a hop
    -- including an auto-followed redirect -- uses a non-https scheme, carries
    userinfo, or targets a host outside the exact allowlist. It is a
    ``VepLinkError`` (never an ``httpx`` fault), so the base-client retry loop,
    which retries only ``httpx`` faults, never retries it. The message is FIXED
    and reflects no upstream-controlled value. Maps to ``output_validation_failed``.
    """


class ResponseTooLargeError(VepLinkError):
    """An upstream response exceeded the decoded-byte cap. NON-RETRYABLE.

    The capped read fails closed (raises) rather than truncating a partial body,
    so a corrupt/partial JSON is never parsed. Not an ``httpx`` fault, so it is
    never retried by the base-client retry loop. Maps to canonical ``internal``
    (subcode ``output_validation_failed``).
    """
