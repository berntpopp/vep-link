# AGENTS.md

Shared repository instructions for agentic coding tools working in vep-link.

## Project

vep-link is an MCP server wrapping Ensembl VEP and Variant Recoder; FastAPI is a
thin host providing `/health` only and mounting the MCP HTTP app at `/mcp`. The
MCP facade covers variant annotation, identifier recoding, canonical resolution,
and cross-build liftover across GRCh38 (`rest.ensembl.org`) and GRCh37
(`grch37.rest.ensembl.org`).

Primary areas:

- `vep_link/` - Python package, MCP facade, Ensembl client, services, models,
  transports, and server management
- `vep_link/mcp/` - hand-authored MCP facade (tools, resources, errors, shaping)
- `vep_link/api/` - resilient async HTTP base client + Ensembl VEP/Recoder client
- `vep_link/services/` - orchestration (resolve/annotate/recode/liftover) +
  annotation extraction
- `tests/` - unit and integration tests; `tests/fixtures/` holds canned Ensembl
  payloads
- `docs/` - usage, API, and MCP tool docs
- `docker/` - Dockerfile and Compose
- `docs/superpowers/specs/` and `docs/superpowers/plans/` - design + execution
  artifacts for agentic workers

## Source Of Truth

- Use this file for shared repo-wide agent guidance.
- Keep `CLAUDE.md` lean and Claude-specific; it references this file.
- Prefer `Makefile` targets over ad hoc commands.
- Use `uv.lock` as the dependency lock source of truth.
- For multi-step work, follow the spec in `docs/superpowers/specs/` and the plan
  in `docs/superpowers/plans/`.

## Working Rules

- Do not revert or overwrite changes you did not make unless explicitly asked.
- Keep edits scoped to the task and avoid unrelated refactors.
- Prefer existing code patterns over new abstractions.
- Put tests under `tests/`; do not create alternate test roots.
- Use ASCII unless a file already requires non-ASCII content.
- Treat Ensembl as an external research data service.
- Keep MCP tools research-use scoped and never imply clinical decision support.
- **Zero real network in tests.** All Ensembl calls are mocked with `respx`;
  the `tests/conftest.py` no-network guard fails any un-mocked request. Live
  tests are marked `integration` and excluded from default CI.
- MCP tool names, schemas, resources, and response modes are owned by
  `vep_link/mcp/`. REST is intentionally minimal (`/health` only).

## Commands

Required before claiming completion:

- `make ci-local`

Useful focused commands: `make install`, `make format`, `make lint`,
`make lint-loc`, `make typecheck`, `make test`, `make test-fast`,
`make test-cov`, `make test-integration`, `make precommit`, `make dev`,
`make docker-build`, `make docker-up`.

## Coding Standards

- Use `uv` for dependency management; never `pip install` directly.
- Use modern Python typing: `list[str]`, `dict[str, int]`, `str | None`.
- Format and lint with Ruff; type check with mypy targeting Python 3.12.
- Keep MCP tool and service behavior covered by unit tests.
- Every input is normalized to canonical `CHR-POS-REF-ALT` before VEP. Do not
  reintroduce per-type VEP GET endpoints on the main annotation path; resolve via
  Variant Recoder then batch through VEP region POST.
- Honor Ensembl rate limits: 429/Retry-After backoff, 200-variant POST chunks.
- Preserve MCP tool names and response schemas unless the task explicitly calls
  for a breaking change.

## File Size Discipline

Hard cap: **600 lines per Python module** in `vep_link/`. Enforced by
`make lint-loc` (wired into `ci-local` and pre-commit). Tests are exempt.

- New files MUST stay under 600 lines.
- Existing oversized files are grandfathered in `.loc-allowlist`; they may shrink
  but not grow.
- Prefer cohesive splits: one module per responsibility.

## Testing Notes

- `make test` runs deterministic unit tests from `tests/unit/`.
- `make test-integration` runs live Ensembl tests (may rate-limit).
- `make test-cov` enforces the 80% coverage floor.
- Treat failing checks as real issues unless you have clear evidence otherwise.
- Do not broaden Ruff or mypy ignores to hide new issues.
