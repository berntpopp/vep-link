# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
