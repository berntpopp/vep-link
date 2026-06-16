"""Fail on Python source files exceeding the per-file line budget.

Rationale: large modules concentrate complexity and slow LLM-assisted
refactors. See AGENTS.md "File Size Discipline" for the policy.

The default budget is 600 lines. Existing oversized files are grandfathered
via `.loc-allowlist` (one path per line, repo-root-relative). Files in the
allowlist must not grow beyond their listed ceiling.

Usage:
    python scripts/check_file_size.py            # check all configured paths
    python scripts/check_file_size.py path/...   # check specific paths
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_LIMIT = 600
DEFAULT_TARGETS = (Path("vep_link"),)
ALLOWLIST_PATH = Path(".loc-allowlist")


def _load_allowlist() -> dict[str, int]:
    """Return {relative_path: ceiling_loc} from `.loc-allowlist`."""
    if not ALLOWLIST_PATH.exists():
        return {}
    entries: dict[str, int] = {}
    for raw in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" in line:
            path, ceiling = line.split(":", 1)
            entries[path.strip()] = int(ceiling.strip())
        else:
            entries[line] = -1
    return entries


def _iter_python_files(targets: list[Path]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        if not target.exists():
            continue
        if target.is_file() and target.suffix == ".py":
            files.append(target)
            continue
        files.extend(sorted(target.rglob("*.py")))
    return files


def _line_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"max lines per file for unallowlisted modules (default: {DEFAULT_LIMIT})",
    )
    args = parser.parse_args(argv)

    targets = args.paths or list(DEFAULT_TARGETS)
    allowlist = _load_allowlist()
    violations: list[str] = []
    grew: list[str] = []

    for path in _iter_python_files(targets):
        rel = path.as_posix()
        loc = _line_count(path)
        if rel in allowlist:
            ceiling = allowlist[rel]
            if ceiling > 0 and loc > ceiling:
                grew.append(
                    f"  {rel}: {loc} lines (grandfathered ceiling {ceiling}). "
                    f"Decompose or lower the entry in .loc-allowlist."
                )
            continue
        if loc > args.limit:
            violations.append(
                f"  {rel}: {loc} lines (limit {args.limit}). "
                f"Split into smaller modules. See AGENTS.md 'File Size Discipline'."
            )

    if not violations and not grew:
        return 0

    if violations:
        sys.stderr.write("\nFiles exceeding the per-file line budget:\n")
        sys.stderr.write("\n".join(violations) + "\n")
    if grew:
        sys.stderr.write("\nGrandfathered files that have grown past their ceiling:\n")
        sys.stderr.write("\n".join(grew) + "\n")
    sys.stderr.write(
        "\nAdd new files to .loc-allowlist with an explicit ceiling only as a "
        "temporary exception with a tracked decomposition plan.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
