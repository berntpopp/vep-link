# API Reference

`vep-link` is **MCP-first**. The REST surface is intentionally minimal: a thin
FastAPI host exposes a single health endpoint and mounts the MCP Streamable HTTP
app. All domain functionality (annotation, recoding, resolution, liftover) is
delivered through MCP tools — see [mcp-tools.md](mcp-tools.md).

> **Research use only; not for clinical decision support.**

## REST surface

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness probe. Returns service status. |
| `GET` | `/metrics` | Prometheus scrape target (ops telemetry). |
| `*` | `/mcp` | Mounted MCP Streamable HTTP app (JSON-RPC). |

`/health` and `/metrics` are the only REST endpoints — both operational. All
variant data is served through MCP; there are no data REST endpoints. OpenAPI
docs, ReDoc, and the schema route are disabled on the host (`docs_url`,
`redoc_url`, `openapi_url` are all `None`).

### `GET /health`

```bash
curl http://127.0.0.1:8000/health
```

```json
{"status": "healthy", "service": "vep-link", "version": "0.2.0"}
```

(Under the Docker stack the host port is **8021**:
`curl http://localhost:8021/health`.)

### `GET /metrics`

Prometheus text-exposition telemetry for the MCP tool layer — operational only,
no variant data. Scrape it like any Prometheus target:

```bash
curl http://127.0.0.1:8000/metrics
```

```
# TYPE vep_link_tool_calls_total counter
vep_link_tool_calls_total{outcome="success",tool="annotate_variant"} 12
vep_link_tool_errors_total{code="not_found",tool="resolve_variant"} 1
# TYPE vep_link_tool_latency_ms histogram
vep_link_tool_latency_ms_bucket{le="100",tool="annotate_variant"} 9
vep_link_tool_latency_ms_count{tool="annotate_variant"} 12
# TYPE vep_link_circuit_state gauge
vep_link_circuit_state{assembly="GRCh38",state="closed"} 1
```

Series: `vep_link_tool_calls_total{tool,outcome}` (success rate),
`vep_link_tool_errors_total{tool,code}` (error-code distribution),
`vep_link_tool_latency_ms` (per-tool latency histogram), and
`vep_link_circuit_state{assembly,state}` (live circuit-breaker state, one-hot).

### `/mcp` (MCP Streamable HTTP)

The MCP app is mounted at `settings.MCP_PATH` (default `/mcp`) and speaks
JSON-RPC. List tools or call a tool with a `tools/list` / `tools/call` request:

```bash
curl -sS http://127.0.0.1:8000/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

The host applies CORS (`VEP_LINK_CORS_ORIGINS`, default `*`) and an
`asgi-correlation-id` middleware so each request carries a correlation id. The
MCP HTTP app runs in stateless JSON-response mode.

## Tool response envelopes

Every tool returns a JSON object. On success the payload is the tool's data plus
a `_meta` block (and, for `annotate_variant`, a `provenance` block). On failure
the payload is the structured error envelope below. Tools do **not** raise to the
client — `run_mcp_tool` converts any exception into an error envelope.

### `_meta` envelope fields

Present on every success and error payload:

| Field | Type | Description |
|-------|------|-------------|
| `tool` | `str` | The tool that produced the payload. |
| `request_id` | `str` | Short (12 hex char) per-call id. On `internal_error` this doubles as the correlation id. |
| `timing.elapsed_ms` | `int` | Measured wall-clock cost of the call, in milliseconds (stamped on success and error envelopes). |
| `capabilities_version` | `str` | 12-hex-char content hash of the capabilities document; a warm client compares it to skip re-fetching `get_capabilities`. |
| `unsafe_for_clinical_use` | `bool` | Always `true` — research-use marker. |
| `next_commands` | `list` | Ready-to-call follow-up steps (`{tool, arguments}`). `resolve_variant` suggests `annotate_variant`; `annotate_variant` suggests `recode_variant`, `liftover_variant`, and a widen-to-`all` re-call when the `standard` view is truncated. |
| `transcripts` | `object` | `annotate_variant` only, `standard` tier: `{shown, total}` when the transcript list was filtered/capped (absent otherwise). |
| `assembly` | `str` | Included only when the call has an assembly context (e.g. `GRCh38`). |

### `provenance` block (`annotate_variant`)

| Field | Type | Description |
|-------|------|-------------|
| `data_source` | `str` | `"Ensembl VEP / Variant Recoder REST"`. |
| `assembly` | `str` | Reference build of the result. |
| `endpoint` | `str` | The upstream endpoint URL (e.g. `https://rest.ensembl.org/vep/homo_sapiens/region`). |
| `retrieved` | `str \| null` | ISO-8601 UTC timestamp of when the result was fetched (populated on `annotate_variant`). |
| `recommended_citation` | `str` | The Ensembl VEP citation (PMID:27268795); paste verbatim. |

### `warnings[]` channel (v0.2)

`resolve_variant`, `annotate_variant`, and `liftover_variant` carry a top-level
`warnings` list — honest, non-fatal signals that ride alongside a successful
result (empty when there is nothing to flag). Distinct from the error envelope,
which owns hard failures. Each entry is `{code, message, context}`:

| `code` | Raised when | `context` |
|--------|-------------|-----------|
| `multiple_alts` | A single input resolved to several ALT alleles (all returned in `variants[]`). | `{count, variants}` |
| `ref_not_validated` | A lifted REF did not match the target-assembly reference base (alleles omitted; coordinate-only `lifted`). | `{expected_ref, carried_ref}` |

The codes are advertised in `get_capabilities` under `warning_codes`.

## Error envelope

Failures return a deterministic structured envelope. An LLM client branches on
`error.code` (stable per exception class) rather than scraping `message`:

```json
{
  "error": {
    "code": "<error_code>",
    "message": "<human message>",
    "recovery": "<how to recover>",
    "fallback_tool": "get_capabilities",
    "next_commands": [{"tool": "...", "arguments": {}}]
  },
  "_meta": {
    "tool": "<tool>",
    "request_id": "<id>",
    "timing": {"elapsed_ms": 21},
    "capabilities_version": "<hash>",
    "unsafe_for_clinical_use": true,
    "next_commands": [],
    "assembly": "<assembly, if any>"
  }
}
```

`fallback_tool` is always `get_capabilities` — the always-readable tool a confused
client can fall back to. `internal_error` envelopes carry a sanitized message: the
original exception text is never surfaced; a fresh correlation id is embedded in
the message and logged server-side so an operator can join the two.

### Error codes

| Code | Trigger | Retryable |
|------|---------|-----------|
| `invalid_input` | Unparseable variant, bad arguments, disallowed `vep_options`, >200 batch variants, same-build liftover. | no |
| `unsupported_input` | Unsupported contig/input for the operation (e.g. HGVS/rsID for liftover). | no |
| `not_found` | Recoder returns no `vcf_string`; no VEP record / no overlap; 0 liftover maps. | no |
| `build_mismatch` | Coordinates inconsistent with the requested assembly. | no |
| `ambiguous` | More than one liftover mapping. | no |
| `rate_limited` | HTTP 429 after retries (`Retry-After` honored) or local concurrency backpressure. | yes |
| `upstream_unavailable` | 5xx / transport error from Ensembl REST. | yes |
| `upstream_timeout` | Upstream request timed out. | yes |
| `output_validation_failed` | Output schema drift. | no |
| `internal_error` | Unexpected fault (sanitized message + correlation id). | no |

These ten codes are surfaced verbatim in the `get_capabilities` payload
(`error_codes`) so a client can branch on them ahead of time.

### Batch per-input errors

`annotate_variants_batch` collects per-input failures inside the success payload's
`errors` array (so one bad variant never fails the batch). Each entry is
`{"input": "...", "error_code": "...", "message": "..."}`, where `error_code` is
`invalid_input` (parse failure) or `not_found` (no genomic coordinate / no VEP
record). Batch-level failures (e.g. >200 variants, rate limiting) still return the
top-level error envelope.

## Versioning

- `server_version` / `/health` `version`: `0.2.0`.
- `mcp_protocol_version`: `2025-06-18`.
- `capabilities_version`: a content hash that changes only when the capabilities
  contract changes; echoed into every `_meta`.
