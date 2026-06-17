"""Unit tests for the shared ``warnings[]`` channel builders.

The builders live in the services layer (pure, no I/O) so both the service and
the MCP tool layer can import them without a layering inversion.
"""

from __future__ import annotations

from vep_link.services.warnings import (
    multiple_alts_warning,
    ref_not_validated_warning,
)


def test_multiple_alts_warning_shape() -> None:
    w = multiple_alts_warning(["1-169549811-C-A", "1-169549811-C-T"])
    assert w == {
        "code": "multiple_alts",
        "message": "Input maps to 2 ALT alleles; all are returned in variants[].",
        "context": {"count": 2, "variants": ["1-169549811-C-A", "1-169549811-C-T"]},
    }


def test_ref_not_validated_warning_shape() -> None:
    w = ref_not_validated_warning(expected="T", carried="C")
    assert w["code"] == "ref_not_validated"
    assert w["context"] == {"expected_ref": "T", "carried_ref": "C"}
    assert "target assembly" in w["message"].lower()
