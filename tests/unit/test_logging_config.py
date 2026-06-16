"""Tests for vep_link.logging_config."""

from __future__ import annotations

from vep_link.logging_config import configure_logging


def test_configure_logging_json_returns_logger() -> None:
    log = configure_logging("INFO", "json")
    # Should not raise when emitting.
    log.info("hello", key="value")


def test_configure_logging_console() -> None:
    log = configure_logging("DEBUG", "console")
    log.debug("debug-event")


def test_configure_logging_defaults() -> None:
    # No args -> falls back to settings (INFO/json by default).
    log = configure_logging()
    log.info("default-event")
