"""Input validation models for the vep-link MCP tools.

These Pydantic v2 models validate and normalize tool arguments before they reach
the service layer. Assembly and response-mode are constrained with ``Literal``
tokens that mirror the :mod:`vep_link.models.enums` members. Batch endpoints cap
their input list at :data:`BATCH_MAX_DEFAULT`; the tool layer enforces the real,
settings-driven cap. The default is duplicated here as a module constant to keep
the models import-cycle free.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Conservative batch cap used for declarative validation. The tool layer applies
# the authoritative cap from ``Settings.BATCH_MAX``; this constant exists only so
# the models stay free of a runtime import on settings.
BATCH_MAX_DEFAULT = 200

Assembly = Literal["GRCh38", "GRCh37"]
ResponseModeStr = Literal["minimal", "compact", "standard", "full"]


def _require_non_empty(value: str) -> str:
    """Strip ``value`` and reject it if nothing remains."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("variant must be a non-empty string")
    return stripped


def _clean_variant_list(variants: list[str]) -> list[str]:
    """Strip each item and reject blank entries."""
    cleaned: list[str] = []
    for item in variants:
        stripped = item.strip()
        if not stripped:
            raise ValueError("variant entries must be non-empty strings")
        cleaned.append(stripped)
    return cleaned


class ResolveRequest(BaseModel):
    """Arguments for resolving a single variant to its canonical coordinate."""

    model_config = ConfigDict(extra="forbid")

    variant: str = Field(..., description="A single variant (rsID, HGVS, coordinate, or CNV).")
    assembly: Assembly = "GRCh38"

    @field_validator("variant")
    @classmethod
    def _strip_variant(cls, v: str) -> str:
        return _require_non_empty(v)


class RecodeRequest(BaseModel):
    """Arguments for recoding one or more variants across notations."""

    model_config = ConfigDict(extra="forbid")

    variants: list[str] = Field(
        ...,
        min_length=1,
        max_length=BATCH_MAX_DEFAULT,
        description="1..200 variants to recode.",
    )
    assembly: Assembly = "GRCh38"
    fields: str | None = Field(
        None, description="Comma-separated subset of recoder output fields to return."
    )

    @field_validator("variants", mode="before")
    @classmethod
    def _coerce_to_list(cls, v: object) -> object:
        """Accept a single string and wrap it in a one-element list."""
        if isinstance(v, str):
            return [v]
        return v

    @field_validator("variants")
    @classmethod
    def _clean_variants(cls, v: list[str]) -> list[str]:
        return _clean_variant_list(v)


class AnnotateRequest(BaseModel):
    """Arguments for annotating a single variant with VEP."""

    model_config = ConfigDict(extra="forbid")

    variant: str = Field(..., description="A single variant to annotate.")
    assembly: Assembly = "GRCh38"
    response_mode: ResponseModeStr = "compact"
    vep_options: dict[str, str] | None = Field(
        None,
        description="Optional VEP flag overrides (validated against the allowlist downstream).",
    )

    @field_validator("variant")
    @classmethod
    def _strip_variant(cls, v: str) -> str:
        return _require_non_empty(v)


class BatchAnnotateRequest(BaseModel):
    """Arguments for annotating a batch of variants with VEP."""

    model_config = ConfigDict(extra="forbid")

    variants: list[str] = Field(
        ...,
        min_length=1,
        max_length=BATCH_MAX_DEFAULT,
        description="1..200 variants to annotate.",
    )
    assembly: Assembly = "GRCh38"
    response_mode: ResponseModeStr = "compact"
    vep_options: dict[str, str] | None = Field(
        None,
        description="Optional VEP flag overrides (validated against the allowlist downstream).",
    )

    @field_validator("variants")
    @classmethod
    def _clean_variants(cls, v: list[str]) -> list[str]:
        if len(v) > BATCH_MAX_DEFAULT:
            raise ValueError(
                f"too many variants: {len(v)} (max {BATCH_MAX_DEFAULT} per batch request)"
            )
        return _clean_variant_list(v)


class LiftoverRequest(BaseModel):
    """Arguments for lifting a variant between two assemblies."""

    model_config = ConfigDict(extra="forbid")

    variant: str = Field(..., description="A single variant to lift over.")
    from_assembly: Assembly = Field(..., description="Source assembly.")
    to_assembly: Assembly = Field(..., description="Target assembly.")

    @field_validator("variant")
    @classmethod
    def _strip_variant(cls, v: str) -> str:
        return _require_non_empty(v)

    @model_validator(mode="after")
    def _assemblies_differ(self) -> LiftoverRequest:
        if self.from_assembly == self.to_assembly:
            raise ValueError("from_assembly and to_assembly must differ")
        return self
