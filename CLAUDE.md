# CLAUDE.md

@AGENTS.md

Claude Code entrypoint only:

- Use `AGENTS.md` for shared repository instructions.
- Keep Claude-specific additions here short and tool-specific.
- Prefer `make ci-local` before final handoff. It runs `lint-loc`, which
  enforces the 600-LOC per-file budget.
- When planning an edit that would push a `vep_link/` module past about 500
  lines, propose a cohesive split first rather than growing the file.
- All Ensembl calls in tests are mocked with `respx`; never let a test reach the
  live network (the `tests/conftest.py` guard enforces this).
