"""Regression tests for local CI target composition."""

from pathlib import Path

MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


def test_ci_local_runs_dedicated_contract_truth_target() -> None:
    """Keep Contract Truth native to CI without replacing the unit-test target."""
    lines = MAKEFILE.read_text(encoding="utf-8").splitlines()

    phony = next(line for line in lines if line.startswith(".PHONY:"))
    assert "test-contract-truth" in phony.split()

    target_index = lines.index(
        "test-contract-truth: ## Verify documentation against the live MCP registry"
    )
    assert lines[target_index + 1] == (
        "\tuv run pytest tests/conformance/test_contract_truth_v1.py -q"
    )

    ci_local = next(line for line in lines if line.startswith("ci-local:"))
    dependencies = ci_local.partition(":")[2].partition("##")[0].split()
    assert "test-fast" in dependencies
    assert "test-contract-truth" in dependencies
