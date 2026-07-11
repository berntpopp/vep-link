"""Caller-visible-message sanitizer for the MCP error surface.

vep-link is a *classify* backend: it exposes no ``untrusted_text`` fence (its
success payloads carry only structured, server-derived VEP data, guarded by
``tests/unit/test_no_untrusted_text_surface.py``). The one residual external-text
surface is the **error path**: an upstream Ensembl 4xx/5xx body, or the
``str(exc)`` of an upstream fault, can reach a caller-visible ``message`` /
``error`` / diagnostics field in BOTH ``structured_content`` and the
``TextContent`` JSON mirror.

Two complementary defenses close it:

* **Sever** (at the source): attacker-influenceable upstream response *bodies*
  are never interpolated into an exception message (see
  ``vep_link.api.base_client._safe_upstream_input_message``) -- a fixed,
  status-keyed, body-free message is raised instead. ``sanitize_message`` alone
  is NOT enough for a body, because it strips code points but not injection *prose*.
* **Sanitize** (defensive backstop): every caller-visible message string is run
  through :func:`sanitize_message`, which drops the same control / zero-width /
  bidi / NUL code points the fleet untrusted-text fence removes, so a hostile
  upstream can never smuggle those code points into an error frame. It is the
  backstop for *server-authored* strings (our own guidance text, a caller
  identifier echoed in an arg error), where the prose is trusted.

``FORBIDDEN_CODEPOINTS`` is byte-identical to the fleet fence set (Response-
Envelope Standard v1.1 §Sanitation).
"""

from __future__ import annotations

# The ratified fleet control/zero-width/bidi/NUL code-point set. Identical to the
# 15 module-fenced backends' ``untrusted_content.FORBIDDEN_CODEPOINTS`` so the
# error surface strips exactly what the primary untrusted-text fence does.
FORBIDDEN_CODEPOINTS = frozenset(
    {
        *range(0x0000, 0x0009),
        *range(0x000B, 0x000D),
        *range(0x000E, 0x0020),
        *range(0x007F, 0x00A0),
        0x200B,
        0x200C,
        0x200D,
        0x2060,
        0xFEFF,
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
    }
)

# Fleet norm: caller-visible error messages are guidance, not payload, so a short
# cap keeps them cheap and forecloses a large hostile body being echoed wholesale.
MAX_MESSAGE_CHARS = 280


def sanitize_message(text: str) -> str:
    """Strip the fence's forbidden control/zero-width/bidi/NUL code points + length-cap.

    Applied to EVERY caller-visible message/error string so a hostile upstream
    (or a caller-influenced 4xx/5xx body) can never smuggle control, zero-width,
    bidirectional, or NUL code points into an error frame. Caller-visible messages
    are server-authored guidance data; upstream response bodies are additionally
    kept out of them at the source (severed in the API client).
    """
    clean = "".join(char for char in text if ord(char) not in FORBIDDEN_CODEPOINTS)
    return clean[:MAX_MESSAGE_CHARS]
