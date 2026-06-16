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

# VEP query profile applied by default (variant-linker's proven set + a little
# more). Values of "1" enable the flag per Ensembl REST convention.
DEFAULT_VEP_OPTIONS: dict[str, str] = {
    "CADD": "1",
    "hgvs": "1",
    "mane": "1",
    "numbers": "1",
    "canonical": "1",
    "domains": "1",
}

# Caller-supplied VEP flags are validated against this allowlist. Plugins that
# are not available on the public Ensembl REST (e.g. SpliceAI, dbNSFP) are listed
# so they can be requested against an instance that supports them, but are
# surfaced in a note rather than silently dropped when unsupported.
VEP_OPTION_ALLOWLIST: frozenset[str] = frozenset(
    {
        "CADD",
        "hgvs",
        "mane",
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
        "variant_class",
        "regulatory",
        "pick",
        "pick_allele",
        "per_gene",
        "flag_pick",
        "minimal",
        "vcf_string",
        "SpliceAI",
        "dbNSFP",
        "Conservation",
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
