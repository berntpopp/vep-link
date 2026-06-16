# vep-link — Upstream Health Awareness Design

**Date:** 2026-06-16
**Status:** Approved (active probe + passive breaker; Core scope)

## Problem

Ensembl REST has periodic outages (observed: `rest.ensembl.org`/GRCh38 returning
500s + hangs while GRCh37 stayed up). vep-link should detect per-assembly
degradation and **warn the LLM consumer early** so it can fail fast, back off, or
route to the healthy assembly — rather than discovering the outage one slow tool
failure at a time.

## Research conclusions (grounding)

- FastMCP 3.4.2 with our `stateless_http=True` mount **cannot push out-of-band
  notifications**; MCP logging is deprecated in the draft spec and is rarely fed
  to the model. So "early warning" is delivered through channels the model reads:
  `_meta` (always-on) + the tool-result error envelope (reactive) + an explicit
  health tool/resource — not server push.
- `_meta` is the conformant always-on channel; use a **non-reserved** key
  (`upstream`), never under `mcp/` or `modelcontextprotocol/`.
- A **per-host circuit breaker** is the missing infra that turns "each call slowly
  times out" into a fast, advertised degraded state.

## Design (Core)

### 1. `UpstreamHealth` monitor — `vep_link/api/health.py`
Per-assembly host state (`GRCh38`, `GRCh37`) with a circuit breaker:
- States `closed → open → half_open`. `record_success` → closed; `record_failure`
  accumulates and trips to `open` at `CIRCUIT_FAILURE_THRESHOLD`; `allow()` flips
  `open → half_open` after `CIRCUIT_COOLDOWN_SECONDS`. `record_failure` while
  already `open` is a no-op (does not extend the cooldown — so a fail-fast cannot
  starve recovery).
- Active probe `refresh()` pings `/info/ping` per host with a short dedicated
  client (`MAX_RETRIES=0`, `HEALTH_PROBE_TIMEOUT`), feeding the breaker. TTL via
  the background poll interval; **never probed per tool call**.
- `status_for(assembly)` → `ok` (closed) | `recovering` (half_open) | `down`
  (open). `snapshot()` → both hosts `{status, circuit, reachable, checked_at,
  latency_ms, last_error}`. `meta_hint()` → compact always-on `_meta.upstream`.
- Monotonic `clock` injected for deterministic breaker tests.

### 2. Background poller — `server_manager.py` lifespan
Create the monitor on `app.state.upstream_health`; `asyncio.create_task` loops
`refresh()` every `HEALTH_PROBE_INTERVAL_SECONDS`; cancelled on shutdown. Pass a
`health_factory=lambda: app.state.upstream_health` into the facade, mirroring
`service_factory`. stdio mode has no poller → passive-only (acceptable).

### 3. Surfacing (channels)
- **`_meta.upstream`** on every success + error result (injected centrally in
  `run_mcp_tool`): compact `{GRCh38, GRCh37, checked_at}` plus `advice` when not ok.
- **Enriched error envelope** for `upstream_unavailable`/`upstream_timeout`/
  `rate_limited`: add `retryable: true`, `retry_after_s`, and a fallback hint
  naming the healthy assembly.
- **Fail fast**: upstream tools check `health.allow(assembly)`; if the circuit is
  open they raise immediately (clean `upstream_unavailable` + fallback) instead of
  attempting and timing out.
- **`check_upstream_health` tool** (live `refresh()` + snapshot) and a readable
  **`vep://health` resource** (recompute-on-read).
- **`get_capabilities`** tool adds a live `upstream` summary *outside* the hashed
  capabilities doc (so `CAPABILITIES_VERSION` stays stable).

### 4. Outcome recording
`run_mcp_tool` records `record_success`/`record_failure` on the call's assembly
for upstream faults only (not parse/validation errors), and injects `_meta.upstream`.

## Config additions (`VEP_LINK_` prefix)
`HEALTH_PROBE_ENABLED=true`, `HEALTH_PROBE_INTERVAL_SECONDS=60`,
`HEALTH_PROBE_TIMEOUT=8`, `CIRCUIT_FAILURE_THRESHOLD=3`,
`CIRCUIT_COOLDOWN_SECONDS=30`.

## Testing
respx-mock `/info/ping` per host; deterministic breaker transitions via injected
clock; tool tests assert `_meta.upstream` present, fail-fast on open circuit, and
enriched error envelope. Zero real network. Coverage floor stays 80%.

## Non-goals
No server-initiated push (unsupported in 3.4.2 stateless HTTP); no auto-routing
between builds (different coordinates); logging notifications not relied upon.
