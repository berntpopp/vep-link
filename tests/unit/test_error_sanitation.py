"""Unit contract for the error-message sanitizer primitive.

``sanitize_message`` strips the fleet fence's forbidden control / zero-width /
bidi / NUL code points from a caller-visible error string and length-caps it, so
a hostile upstream error body cannot smuggle those code points into an MCP error
frame. Prose is preserved verbatim (severing attacker prose is the API client's
job, not the sanitizer's).
"""

from __future__ import annotations

from vep_link.mcp._sanitize import (
    FORBIDDEN_CODEPOINTS,
    MAX_MESSAGE_CHARS,
    sanitize_message,
)


def test_strips_nul_zwj_bom_and_bidi_override() -> None:
    dirty = "Ensembl rejected\x00 the‍ request﻿ here‮."
    clean = sanitize_message(dirty)
    assert "\x00" not in clean  # NUL
    assert "‍" not in clean  # zero-width joiner
    assert "﻿" not in clean  # BOM / zero-width no-break space
    assert "‮" not in clean  # right-to-left override
    # The ordinary prose around the code points survives untouched.
    assert clean == "Ensembl rejected the request here."


def test_preserves_ordinary_prose_and_common_whitespace() -> None:
    # Tabs/newlines are NOT in the forbidden set (0x09/0x0A/0x0D excluded), so a
    # legitimate multi-line message is preserved.
    msg = "Upstream rejected the request (HTTP 400).\n\tRetry with a valid variant."
    assert sanitize_message(msg) == msg


def test_length_capped() -> None:
    capped = sanitize_message("x" * (MAX_MESSAGE_CHARS + 500))
    assert len(capped) == MAX_MESSAGE_CHARS


def test_forbidden_set_covers_the_named_vectors() -> None:
    for cp in (0x0000, 0x200B, 0x200D, 0x2060, 0xFEFF, 0x202E, 0x2066):
        assert cp in FORBIDDEN_CODEPOINTS
    # Tab, newline, carriage return are deliberately allowed.
    for cp in (0x09, 0x0A, 0x0D):
        assert cp not in FORBIDDEN_CODEPOINTS
