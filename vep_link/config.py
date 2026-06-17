"""Configuration settings for the vep-link server.

Settings are read from the environment with the ``VEP_LINK_`` prefix (and an
optional ``.env`` file). Build-aware URL helpers select the correct Ensembl REST
host for each assembly: GRCh38 -> ``rest.ensembl.org``,
GRCh37 -> ``grch37.rest.ensembl.org``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models.enums import GenomeBuild

# Ensembl REST endpoint paths (host-independent; combined with a build base URL).
RECODER_GET_PATH = "/variant_recoder/human"
RECODER_POST_PATH = "/variant_recoder/homo_sapiens"
VEP_REGION_PATH = "/vep/homo_sapiens/region"
VEP_HGVS_PATH = "/vep/human/hgvs"
VEP_ID_PATH = "/vep/human/id"
ASSEMBLY_MAP_PATH = "/map/human"
SEQUENCE_REGION_PATH = "/sequence/region/human"

# VEP query profile applied by default (variant-linker's proven set + the
# precomputed pathogenicity/conservation predictors that the public Ensembl REST
# *does* serve as dedicated toggles). Values of "1" enable the flag per Ensembl
# REST convention. CADD/REVEL/AlphaMissense are the headline missense
# pathogenicity scores; Conservation is the GERP score. They only populate for
# applicable (e.g. missense/coding) variants, so they cost nothing on others.
DEFAULT_VEP_OPTIONS: dict[str, str] = {
    "CADD": "1",
    "REVEL": "1",
    "AlphaMissense": "1",
    "Conservation": "1",
    "hgvs": "1",
    "mane": "1",
    "numbers": "1",
    "canonical": "1",
    "domains": "1",
}

# Caller-supplied VEP flags are validated against this allowlist. Two classes:
#   1. Toggles the public Ensembl REST genuinely serves -- transcript/identifier
#      annotation plus the precomputed predictor scores (CADD, REVEL,
#      AlphaMissense, Conservation, EVE, dbscSNV, MaxEntScan, GeneSplicer,
#      Blosum62). These return data directly.
#   2. Instance-dependent plugins not run by the public REST (SpliceAI, dbNSFP,
#      LoF). They stay allowlisted so they can be requested against a configured
#      VEP instance, but are surfaced in a note rather than silently dropped (see
#      ``vep_link.mcp.tools._common._INSTANCE_DEPENDENT_PLUGINS``).
VEP_OPTION_ALLOWLIST: frozenset[str] = frozenset(
    {
        # transcript / identifier annotation
        "hgvs",
        "hgvsg",
        "mane",
        "mane_select",
        "numbers",
        "canonical",
        "domains",
        "merged",
        "refseq",
        "protein",
        "uniprot",
        "ccds",
        "tsl",
        "appris",
        "biotype",
        "symbol",
        "xref_refseq",
        "transcript_version",
        "variant_class",
        "var_synonyms",
        "mirna",
        "gene_phenotype",
        "regulatory",
        "shift_3prime",
        "pick",
        "pick_allele",
        "per_gene",
        "flag_pick",
        "minimal",
        "vcf_string",
        # precomputed predictor scores served by the public REST
        "CADD",
        "REVEL",
        "AlphaMissense",
        "Conservation",
        "Blosum62",
        "EVE",
        "dbscSNV",
        "MaxEntScan",
        "GeneSplicer",
        "Phenotypes",
        # instance-dependent plugins (not run by the public REST)
        "SpliceAI",
        "dbNSFP",
        "LoF",
    }
)


def _build_value(build: GenomeBuild | str) -> str:
    """Normalize a build to its canonical assembly string."""
    return build.value if isinstance(build, GenomeBuild) else str(build)


@dataclass
class ServerConfig:
    """Transport-level server configuration."""

    transport: Literal["unified", "http"] = "unified"
    host: str = "127.0.0.1"
    port: int = 8000
    mcp_path: str = "/mcp"
    enable_docs: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> ServerConfig:
        return cls(
            transport=settings.MCP_TRANSPORT,
            host=settings.MCP_HOST,
            port=settings.MCP_PORT,
            mcp_path=settings.MCP_PATH,
            log_level=settings.LOG_LEVEL,
        )


class Settings(BaseSettings):
    """Application settings (env prefix ``VEP_LINK_``)."""

    # Build-specific Ensembl REST hosts.
    VEP_GRCH38_URL: str = "https://rest.ensembl.org"
    VEP_GRCH37_URL: str = "https://grch37.rest.ensembl.org"
    DEFAULT_ASSEMBLY: Literal["GRCh38", "GRCh37"] = "GRCh38"

    # Request handling / resilience.
    #
    # Defaults are tuned so a *synchronous* MCP tool call fails fast and cleanly
    # when Ensembl is unhealthy (e.g. an upstream 500/hang) instead of stacking
    # up retries past the client's tool-call timeout. ``CONNECT_TIMEOUT`` fails
    # fast on connection-level stalls; ``REQUEST_TIMEOUT`` bounds a single
    # attempt's read; ``OVERALL_DEADLINE_SECONDS`` is a hard wall-clock cap on
    # one logical request *across all retries* (each attempt's timeout is capped
    # to the remaining budget), so total time can never exceed it.
    REQUEST_TIMEOUT: int = 30
    CONNECT_TIMEOUT: float = 10.0
    OVERALL_DEADLINE_SECONDS: float = 45.0
    MAX_CONCURRENCY: int = 5
    QUEUE_WAIT_TIMEOUT: int = 20
    MAX_RETRIES: int = 2
    BACKOFF_BASE_SECONDS: float = 1.0
    BACKOFF_MAX_SECONDS: float = 20.0

    # Batch POST chunking (Ensembl region/recoder POST limits).
    CHUNK_SIZE: int = 200
    BATCH_MAX: int = 200
    INTER_CHUNK_DELAY_MS: int = 100

    # In-process cache. VEP/Recoder results are deterministic per
    # (input, assembly, options) so a long TTL is safe.
    CACHE_SIZE: int = 1024
    CACHE_TTL_SECONDS: int = 86400

    # Liftover. When true, the lifted REF base is validated against the target
    # assembly reference (via /sequence/region); a mismatch downgrades the result
    # to coordinate-only + a ``ref_not_validated`` warning instead of emitting a
    # possibly-wrong CHR-POS-REF-ALT.
    LIFTOVER_VALIDATE_REF: bool = True

    # Upstream health monitoring. A per-assembly circuit breaker (fed by real
    # call outcomes + a cheap background /info/ping probe) lets the server warn
    # the LLM consumer early and fail fast when an Ensembl host is degraded.
    HEALTH_PROBE_ENABLED: bool = True
    HEALTH_PROBE_INTERVAL_SECONDS: float = 60.0
    HEALTH_PROBE_TIMEOUT: float = 8.0
    CIRCUIT_FAILURE_THRESHOLD: int = 3
    CIRCUIT_COOLDOWN_SECONDS: float = 30.0

    # Transport.
    MCP_TRANSPORT: Literal["unified", "http"] = "unified"
    MCP_HOST: str = "127.0.0.1"
    MCP_PORT: int = 8000
    MCP_PATH: str = "/mcp"

    # Logging.
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "json"

    # Server.
    CORS_ORIGINS: str = "*"
    USER_AGENT: str = "vep-link/0.1 (research MCP; +https://github.com/berntpopp/vep-link)"

    model_config = SettingsConfigDict(
        env_prefix="VEP_LINK_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("MCP_PATH")
    @classmethod
    def _validate_mcp_path(cls, v: str) -> str:
        return v if v.startswith("/") else f"/{v}"

    def base_url(self, build: GenomeBuild | str) -> str:
        """Return the Ensembl REST base URL for an assembly."""
        return self.VEP_GRCH37_URL if _build_value(build) == "GRCh37" else self.VEP_GRCH38_URL

    # The Ensembl host is identical for VEP, Recoder, and assembly map; the
    # distinct helpers exist for call-site readability.
    def vep_url(self, build: GenomeBuild | str) -> str:
        return self.base_url(build)

    def recoder_url(self, build: GenomeBuild | str) -> str:
        return self.base_url(build)

    def map_url(self, build: GenomeBuild | str) -> str:
        return self.base_url(build)

    @property
    def cors_origins_list(self) -> list[str]:
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()
