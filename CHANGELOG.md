# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
