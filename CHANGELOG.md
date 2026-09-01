# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.5] - 2026-09-02

### Changed

- Deploy: declare the image's numeric uid:gid in docker/docker-compose.npm.yml so the fleet
  controller can deploy the service; a guard test keeps `user` out of the release Compose files.

## [1.1.4] - 2026-08-31

### Changed

- Consolidate current runtime/tooling updates, use router container workflows at v0.8.3,
  refresh the pinned Python base image, and make the production server restart persistent.
- README validation now identifies the Git remote, so an isolated Git worktree validates
  the repository badges correctly.
- Upgrade Debian packages during the image build and remove bootstrap `setuptools` from the
  production virtual environment to remediate fixable OpenSSL and packaging-tool findings.

## [1.1.3] - 2026-08-10

### Security

- Refresh runtime dependencies, including `cryptography` 50.0.0, and remove
  unused vulnerable packaging tooling from the production image.

### Changed

- Refresh pinned CI actions and use the released genefoundry-router v0.7.6
  reusable container workflows.

## [1.1.2] - 2026-07-30

### Changed

- **CI tests the interpreter that actually ships.** The `quality` job now runs
  on a `["3.12", "3.14"]` matrix instead of 3.12 only. `docker/Dockerfile` ships
  `python:3.14-slim`, so until now the test suite was never exercised on the
  interpreter that reaches production — only the image itself was, via
  `container-ci`/`conformance`. A 3.14-only stdlib or typing regression would
  have shipped uncaught. 3.12 stays in the matrix because it is the declared
  `requires-python` floor; dropping it would make that floor a false claim. The
  coverage gate still runs once (on 3.14, push to `main`). `requires-python`,
  ruff `target-version` and mypy `python_version` are deliberately unchanged at
  3.12.
- **Each leg's interpreter is stated, not inferred.** The job exports
  `UV_PYTHON: ${{ matrix.python-version }}`. This repo has no `.python-version`,
  so `uv` would otherwise fall back to `PATH` discovery — and if a
  `.python-version` were ever added it would outrank `PATH` and silently pin
  both legs to one interpreter while the UI still reported two. The env var also
  covers every `uv run` inside `make ci-local`, not just the explicit `uv sync`.
- **New guard `tests/unit/test_ci_python_matrix.py`.** Pins the matrix against
  `pyproject.toml`'s floor and the Dockerfile's `FROM python:<x.y>-slim`, so a
  base-image bump that is not mirrored into CI fails instead of silently
  re-opening the blind spot.

## [1.1.1] - 2026-07-30

Maintenance release. **Dependabot coverage was added to this repository for the first
time.** Until now there was no `.github/dependabot.yml`, and without a config Dependabot
runs *security* updates only — so vep-link had never received a single version update.
Everything security updates do not touch (the Docker base digest, the GitHub Actions
pins, the CodeQL pin) had been drifting unbounded. This release adds the config and
sweeps the accumulated drift. No functional or wire-contract change.

### Added

- `.github/dependabot.yml` covering all four ecosystems present in the repo: `uv` at `/`,
  `github-actions` at `/`, `docker` at `/docker`, and `docker-compose` at `/docker`.
  Weekly Monday, Europe/Berlin, staggered 04:00/04:15/04:30/04:45, five open PRs per
  ecosystem, `deps`/`ci` commit prefixes — byte-identical to the fleet-standard shape.

### Changed

- **Lint policy is now pinned with `select` instead of `extend-select`.** ruff 0.16 grows
  the implicit default rule set from 59 to 413 rules; under `extend-select` this repo
  would have silently inherited ~350 rules it never opted into. The rule list is
  unchanged and already supersets the pre-0.16 default (E4/E7/E9 + F), so the effective
  policy is identical — `ruff check` still reports zero findings.
- Dependencies swept with `uv lock --upgrade` (35 packages). Notable: ruff 0.15.17 →
  0.16.0, fastapi 0.137.1 → 0.141.1, mypy 2.1.0 → 2.3.0, uvicorn 0.49.0 → 0.52.0,
  fastmcp 3.4.4 → 3.4.5, mcp 1.28.1 → 1.29.0, typer 0.26.7 → 0.27.0, prometheus-client
  0.25.0 → 0.26.0, certifi 2026.5.20 → 2026.7.22. Version floors in `pyproject.toml` are
  deliberately unchanged: this repo's convention is a permissive floor plus a major upper
  cap, and the floor is a compatibility claim rather than a mirror of the lock.
- Docker base digest refreshed **within the same Python line** — `python:3.14-slim`
  `sha256:b877e50b` → `sha256:cea0e604`. Both are Python 3.14.6; the move is Debian 13.5
  → 13.6. Not a Python-line jump, because `container-release.json`'s `image_allowlist`
  entries are interpreter-versioned and crossing a Python minor would relocate them out
  from under the release gate's content inspector. Verified against the actual image that
  `/usr/bin/perl5.40.1` still exists, so the prepared stage's hardlink guard still holds.
- GitHub Actions pins refreshed: `actions/checkout` v7.0.0 → v7.0.1,
  `actions/setup-python` v6.3.0 → v7.0.0, `astral-sh/setup-uv` v8.2.0 → v9.0.0.

### Fixed

- **`container-security.yml` was a whole major behind** on `actions/checkout`, pinned to
  `df4cb1c0` labelled `# v6.0.3`. That SHA is not what `v6.0.3` points at (`9f698171`);
  it is the untagged "Update changelog for v6.0.3" commit. Now `v7.0.1`, matching every
  other checkout in the repo.
- **The CodeQL pin was frozen and untrackable.** `github/codeql-action@ed410739 # v4` is
  a *tag-object* SHA, which Dependabot cannot follow — it would have stayed stuck even
  after the new config started running. Repinned to the dereferenced commit
  `f205ea1c` (= v4.37.4), matching the fleet decision made in genefoundry-router v0.7.3.
- `CITATION.cff` `version:` was stale at 1.0.9 (it had not been regenerated for the 1.1.0
  release); now 1.1.1.

## [1.1.0] - 2026-07-15

GeneFoundry MCP contract-hardening sweep — brings vep-link into compliance with the
fleet Response-Envelope, Tool-Schema Documentation, and Tool-Surface Budget standards
(genefoundry-router #73 / #75 / #76). The behaviour conformance gate
(`tests/conformance/behaviour.py`, vendored byte-identical from router `791363c`) is now
CONFORMANT (0 fail, 0 UNGATED) and wired into `.github/workflows/conformance.yml`.
Research use only.

### Changed

- Re-vendored the behaviour conformance gate from genefoundry-router `56db958`
  (`docs/conformance/behaviour.py` blob `c69801687`) so live MCP contract checks
  treat not-found example probes as inconclusive and keep empty auxiliary objects from hiding counted rows.

- **`error_code` is now closed to the six-value GeneFoundry canon** —
  `invalid_input · not_found · ambiguous_query · upstream_unavailable · rate_limited ·
  internal`. The previous ten-value taxonomy is mapped onto it: `unsupported_input` and
  `build_mismatch` → `invalid_input`; `ambiguous` → `ambiguous_query`; `upstream_timeout`
  → `upstream_unavailable`; `output_validation_failed` → `internal`; `internal_error` →
  `internal`. **Wire note (potentially breaking for a client that branched on the old
  codes):** the finer classification is preserved additively in a new `error_subcode`
  field (e.g. an `upstream_unavailable` timeout carries `error_subcode: "upstream_timeout"`),
  and `get_capabilities` now advertises both `error_codes` (the closed six) and
  `error_subcodes`. Batch per-input error rows use the same canon + subcode.
- **Invalid-argument errors now name the offending parameter.** A bad-arguments call
  previously returned only `"Invalid arguments for <tool>: N error(s)."` with nothing for
  a model to act on; `get_capabilities`, `recode_variant` and `check_upstream_health`
  failed the behaviour gate's "names the offending or the valid parameters" check. The
  handler now extracts the offending field(s) from the validation error, names them in the
  `message`, and carries them as a structured `field` plus the tool's `allowed_values`
  (accepted parameter names).
- **Upstream health advice is endpoint-honest.** Health is tracked per HOST via
  `/info/ping`, not per endpoint, so a working `/vep` could mask a dead `/variant_recoder`
  and advise "retry the other build" as a guaranteed fix. The fallback advice now states
  the ping is a host-level signal that does not confirm the failed endpoint works on the
  other build, and to treat a repeat failure as endpoint-wide. A timeout's recovery notes
  that a healthy VEP call can legitimately take ~40s (so a slow call is not misread as an
  outage).

### Added

- **`error_subcode`** (additive) on error envelopes and batch rows; **`accepting`** on
  each per-host health view — an honest companion to `circuit`, true when an `open`
  breaker's cooldown has elapsed and the next call would pass (closes the audit's
  "breaker reported open while still letting calls through" confusion). Computed without
  mutating breaker state.
- Behaviour conformance gate vendored and wired into CI (`Run behaviour probe`).

### Removed

- **`outputSchema` is no longer published on any tool** (`output_schema=None`) and the
  server is constructed with `FastMCP(dereference_schemas=False)` — Tool-Surface Budget
  Standard v1. `structuredContent` is unaffected (every tool returns a dict envelope).
  Total advertised tool surface drops to ~2,585 tokens (0% `outputSchema`).

### Fixed

- `liftover_variant` was un-probeable by the behaviour gate (UNGATED): its required
  `from_assembly` / `to_assembly` enum parameters carried no `examples`. Examples added
  (Tool-Schema Documentation Standard S2), so the tool is now fully gated.
- **`recode_variant`'s `fields` was a silently-empty filter.** `fields` is a
  comma-separated projection over the closed set `vcf_string, hgvsg, hgvsc, hgvsp, spdi`,
  but an unknown token (e.g. `fields="bogus"`) previously returned `success: true` with
  identity-only rows. An unknown token is now rejected as `invalid_input` naming `fields`
  and the allowed set (schema is a subset of the runtime).
- **An out-of-allowlist `vep_options` key is rejected naming the key.** The runtime
  honours only `VEP_OPTION_ALLOWLIST`; an unknown key (e.g. `{"NoSuchFlag": "1"}`) now
  returns `invalid_input` naming `vep_options` and the offending key on both
  `annotate_variant` and `annotate_variants_batch` (previously the schema was wider than
  the runtime).
- **A genuine internal fault of an existing tool no longer mislabels as `not_found`.**
  `get_capabilities` now runs inside the `run_mcp_tool` error boundary, and the protocol
  backstop routes a known tool's masked fault to `internal` (with a correlation id) while
  reserving `not_found` for genuinely-unknown tool names — so a health-wiring failure on
  an existing tool can no longer tell the model "the requested tool is not available."
- **Batch and single-call paths now share ONE exception classifier**
  (`canonical_error_code`). The duplicated batch table had omitted `DisallowedURLError` /
  `ResponseTooLargeError`, so those lost `error_subcode="output_validation_failed"` in
  batch; every error type now maps identically in both paths.

## [1.0.9] - 2026-07-14

### Changed

- **The NPM deployment pulls the released image instead of building from source.**
  `docker/docker-compose.npm.yml` carried `build:`, so a deploy rebuilt the image on the
  server even though CI had already published an attested, digest-addressable image to
  GHCR — the published image was never consumed. The overlay now requires
  `VEP_LINK_IMAGE` pinned to a digest and fails closed when it is unset. Nothing else
  changed: `container_name`, the Compose project name, the healthcheck, networks, tmpfs
  and `expose` are all preserved, so the deployed topology is untouched. Research use
  only.

## [1.0.8] - 2026-07-13

### Fixed

- Re-pin the reusable container CI and container release workflows to the
  corrected GeneFoundry container release standard revision
  (`58d011d9c72efe90337244342fdec703f2b5b4b9`). The previously pinned revision
  carried latent release-pipeline defects that were fixed centrally, including
  GHCR authentication before the version alias push. Research use only.

## [1.0.7] - 2026-07-13

### Added

- Adopt the GeneFoundry container release standard with SHA-pinned reusable
  container CI/release callers, release metadata, digest-only production Compose,
  and complete OCI image labels. Research use only.

## [1.0.6] - 2026-07-12

### Security

- Adopted the canonical outbound HTTP Policy v1 for configured GRCh37 and
  GRCh38 Ensembl origins. Every redirect hop is validated, decoded response
  bodies have bounded reads, and redirect-limit failures map to fixed,
  identifier-free policy errors. The production client is bound to the shared
  conformance suite. Research use only.

## [1.0.5] - 2026-07-11

### Security

- **FastMCP-core not-found reflection guard.**
  FastMCP core reflected the caller's OWN requested tool name / resource URI /
  prompt name (with any control/zero-width/bidi/NUL code points and injection
  prose) back to the caller and to logs *before* backend middleware ran. A
  layered guard (`vep_link/mcp/notfound_guard.py`) closes it with fixed,
  input-free constants: Layer 1 preflights an unknown tool name to a name-free
  `not_found` envelope; Layer 2 collapses any `on_read_resource` failure to a
  fixed URI-free error; Layer 3 is a protocol-handler backstop over the raw
  CallTool/ReadResource/GetPrompt handlers (covers the unknown-tool *return*
  path and the unknown-prompt echo — vep registers no prompts, so every
  `prompts/get` is unknown); Layer 5 scrubs the FastMCP/MCP-SDK validation logs
  (`Handler called`, `Tool cache miss for`, session `Failed to validate request`)
  at DEBUG+ on their source loggers and handlers. No success/error schema
  change; caller self-reflection surface (low–medium severity). Research use only.

- **Error-message sanitation (defense in depth). PATCH bump to `1.0.4`.**
  Caller-visible error messages are sanitized of control/zero-width/bidi/NUL
  code points; the upstream Ensembl VEP error body is no longer echoed; batch-row
  and health `last_error` no longer expose exception text; the debug log no
  longer renders raw exception detail. Research use only.

### Fixed

- **Security remediation (2026-07-07).** Two inbound-boundary hardening fixes,
  each with a test guard. PATCH bump to `1.0.3`.
  - Loopback-bind the base `docker-compose.yml` published host port
    (`127.0.0.1:${VEP_LINK_HOST_PORT}:8000`) so copying the dev/local compose
    to a server never publishes the unauthenticated backend on the public IP
    (Docker otherwise binds `0.0.0.0` and bypasses the host firewall);
    production still fronts the container via the router/reverse proxy.
  - Drop the exception detail from the `mcp_internal_error` operator log: only
    the exception CLASS name + `correlation_id` are logged now (no
    `repr(exc)`/`str(exc)`, no `exc_info` traceback), because the message text
    can embed a patient variant string (PII).
- Harden Ensembl REST URL test assertions to exact/settings-based checks
  (clears two CodeQL `py/incomplete-url-substring-sanitization` alerts).
- **Single-sourced the package version and advertised it in MCP `serverInfo`.**
  `pyproject.toml [project].version` is now the single source of truth (was
  `dynamic`, read from `vep_link/__init__.py`); `vep_link.__version__` derives
  from the installed distribution metadata (`importlib.metadata.version`) rather
  than a hardcoded literal that had drifted from the built metadata. The FastMCP
  facade now passes `version=__version__` to `FastMCP(...)`, so an `initialize`
  handshake reports the package version in `serverInfo.version` instead of the
  FastMCP framework version (`3.4.2`). `/health` was already correct. PATCH bump
  to `1.0.1`.

### Changed (BREAKING — GeneFoundry Response-Envelope Standard v1)

- **Migrated `run_mcp_tool` to the ratified flat-banner envelope.** Every
  successful tool response now carries a top-level `success: true` (payload
  keys and `_meta` are otherwise unchanged — no tool renames, no payload-key
  renames). Every failure now returns a FLAT `{"success": false, "error_code",
  "message", "retryable", "recovery_action", ...}` frame instead of the
  previous nested `{"error": {"code", "message", ...}}` block; `next_commands`
  moved from `error.next_commands` to `_meta.next_commands`. Failures
  additionally set the MCP-native `CallToolResult.isError = true` wire flag
  (verified against the installed `fastmcp==3.4.2`: a tool body returning
  `ToolResult(structured_content=<frame>, is_error=True)` is passed through
  unchanged by `Tool.convert_result`), so a client sees both the protocol-level
  error signal and the actionable in-band frame. `unsafe_for_clinical_use:
  true` remains on every `_meta` block, success or error. `docs/api.md`'s
  Error envelope section and the MCP tool-layer tests are updated to the new
  shape; no tool names or successful payload shapes changed.

### Security

- **Adopted the GeneFoundry Container & Deployment Hardening Standard v1.** Added
  a `docker/docker-compose.prod.yml` hardening overlay (read-only rootfs +
  explicit writable tmpfs, `cap_drop: ALL`, `no-new-privileges`, `init`, and
  memory/CPU/PID limits; expose-only with `ports: !reset []`), digest-pinned the
  `python:3.14-slim` base image, added a `container-security` CI workflow (Trivy
  scan failing on fixable HIGH/CRITICAL + CycloneDX SBOM artifact), and fixed the
  CORS middleware to never pair wildcard origins with `allow_credentials=True`.

### Added

- **Tool-Naming Standard v1 CI guard + namespace docs.** Added
  `tests/unit/test_tool_names.py`, which lints the **live** FastMCP tool roster
  against the GeneFoundry Tool-Naming & Normalization Standard v1: every name is
  unprefixed snake_case `≤50` chars, does not self-prefix the gateway namespace
  token, starts with an approved verb, and the live roster equals the
  `get_capabilities` roster. As an action/compute server, vep-link's domain
  action verbs (`annotate`, `recode`, `liftover`, plus the readiness verb
  `check`) are carried as **documented exceptions** pending the fleet-wide
  Standard v1.1 verb-canon decision (no tools are renamed). README now documents
  the canonical gateway namespace token **`vep`** (`annotate_variant` →
  `vep_annotate_variant` at the gateway) and the action-verb exception note.
- **Pathogenicity & conservation scores (REVEL, AlphaMissense, CADD, GERP).** The
  public Ensembl REST serves these precomputed predictors as dedicated VEP
  toggles; vep-link now enables `REVEL`, `AlphaMissense`, and `Conservation`
  alongside the existing `CADD` **by default**, extracts them onto each
  transcript (`revel`, `cadd_raw`, `conservation`, and the flattened
  `am_pathogenicity` / `am_class`), and surfaces `revel` / `am_pathogenicity` /
  `am_class` / `conservation` in the default `compact` `representative_transcript`
  (and every `standard` transcript) so an interpreter sees them with no extra
  round trip. The `vep_options` allowlist gained the public scoring/annotation
  toggles (`REVEL`, `AlphaMissense`, `EVE`, `dbscSNV`, `MaxEntScan`, `GeneSplicer`,
  `Blosum62`, `mane_select`, `hgvsg`, `var_synonyms`, `transcript_version`,
  `mirna`, `gene_phenotype`, `shift_3prime`, `Phenotypes`). `dbNSFP`, `SpliceAI`,
  and `LoF` remain allowlisted but instance-dependent (not run by the public
  REST); the scores usually pulled *from* dbNSFP are available via the dedicated
  toggles instead.
- **Upstream health awareness.** A per-assembly circuit breaker (`UpstreamHealth`)
  tracks the two Ensembl REST hosts, fed both passively (real tool-call outcomes)
  and actively (a cheap `/info/ping` background probe every
  `HEALTH_PROBE_INTERVAL_SECONDS`). The MCP layer now warns the consumer early:
  - `_meta.upstream` on every tool response — compact per-assembly status plus an
    `advice` line naming the healthy host when one is degraded.
  - Upstream error envelopes carry `retryable: true`, `retry_after_s`, and a
    fallback-to-healthy-assembly hint.
  - **Fail fast**: when a host's circuit is open, tools return a clean
    `upstream_unavailable` immediately instead of attempting and timing out.
  - New `check_upstream_health` tool (live probe + snapshot), a readable
    `vep://health` resource, and a live `upstream` summary in `get_capabilities`.
  - New settings: `HEALTH_PROBE_ENABLED`, `HEALTH_PROBE_INTERVAL_SECONDS`,
    `HEALTH_PROBE_TIMEOUT`, `CIRCUIT_FAILURE_THRESHOLD`, `CIRCUIT_COOLDOWN_SECONDS`.

### Fixed

- Fail fast and cleanly when Ensembl REST is unhealthy. Previously a hung or
  500-storming upstream (e.g. a `rest.ensembl.org` GRCh38 outage) made a tool
  call stack `MAX_RETRIES` full-length timeouts (minutes) and the MCP client
  would time out with no useful error. The HTTP client now uses a short connect
  timeout, a hard wall-clock `OVERALL_DEADLINE_SECONDS` (default 45 s) across all
  retries, and tuned defaults (`REQUEST_TIMEOUT` 60→30, `MAX_RETRIES` 4→2), so an
  outage surfaces a clean `upstream_unavailable`/`upstream_timeout` in seconds.
- Fix a spurious `rate_limited` ("concurrency saturated") error on a retry that
  followed a slow first attempt: the concurrency-slot wait now uses a fresh
  per-attempt budget instead of a stale absolute deadline.

## [0.1.0] - 2026-06-16

### Added

- Initial release of vep-link: a unified REST + MCP server wrapping Ensembl VEP
  and Variant Recoder across GRCh38 (`rest.ensembl.org`) and GRCh37
  (`grch37.rest.ensembl.org`).
- Six MCP tools: `get_capabilities`, `resolve_variant`, `recode_variant`,
  `annotate_variant`, `annotate_variants_batch`, `liftover_variant`.
- Canonical normalization pipeline (input → Variant Recoder → `CHR-POS-REF-ALT`
  → VEP region POST) with 200-variant POST chunking and Retry-After backoff.
- Response-mode tiers (`minimal`, `compact`, `standard`, `full`), structured
  error envelopes, `_meta`/provenance, schema resources, and the research-use
  disclaimer.
- FastAPI `/health` host with mounted MCP HTTP app, typer + rich CLI, structured
  logging, Docker image + Compose, and an 80%-floor respx-mocked test suite.
