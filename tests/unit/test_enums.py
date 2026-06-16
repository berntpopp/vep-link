"""Tests for vep_link.models.enums."""

from __future__ import annotations

from vep_link.models.enums import (
    ConsequenceImpact,
    GenomeBuild,
    InputKind,
    ResponseMode,
    impact_rank,
)


def test_genome_build_values() -> None:
    assert GenomeBuild.GRCH38.value == "GRCh38"
    assert GenomeBuild.GRCH37.value == "GRCh37"
    assert GenomeBuild("GRCh38") is GenomeBuild.GRCH38


def test_response_mode_values() -> None:
    assert [m.value for m in ResponseMode] == ["minimal", "compact", "standard", "full"]


def test_input_kind_values() -> None:
    assert {k.value for k in InputKind} == {"coordinate", "cnv", "hgvs", "rsid"}


def test_impact_rank_ordering() -> None:
    assert (
        impact_rank("HIGH") > impact_rank("MODERATE") > impact_rank("LOW") > impact_rank("MODIFIER")
    )
    assert impact_rank("MODIFIER") == 1
    assert impact_rank("UNKNOWN") == 0


def test_consequence_impact_members() -> None:
    assert ConsequenceImpact.HIGH.value == "HIGH"
