# Usage Guide

Practical workflows for `vep-link` — the unified REST + MCP server wrapping
Ensembl VEP and Variant Recoder across GRCh38 and GRCh37.

> **Research use only; not for clinical decision support.**

## Quick start

```bash
uv sync --group dev
make dev                          # FastAPI /health host + MCP at /mcp (console logs)
```

The local dev server exposes:

- MCP Streamable HTTP: `http://127.0.0.1:8000/mcp`
- Health: `http://127.0.0.1:8000/health`
- Metrics (Prometheus): `http://127.0.0.1:8000/metrics`

Manual equivalent:

```bash
uv run vep-link serve --transport unified --host 127.0.0.1 --port 8000
```

### Docker (host port 8021)

```bash
docker compose -f docker/docker-compose.yml up
curl http://localhost:8021/health
```

The container listens on internal port `8000`, published on host port **8021**
(override with `VEP_LINK_HOST_PORT`). When running under Docker, use
`http://localhost:8021/mcp` everywhere a local server would use `:8000`.

## Connecting an MCP client

### Claude Code (HTTP)

```bash
# local dev server
claude mcp add --transport http vep-link http://127.0.0.1:8000/mcp
# Docker stack
claude mcp add --transport http vep-link http://localhost:8021/mcp
```

### stdio

```json
{
  "mcpServers": {
    "vep-link": {
      "command": "uv",
      "args": ["run", "python", "mcp_server.py"],
      "cwd": "/path/to/vep-link"
    }
  }
}
```

## Calling tools over HTTP

Tool calls are JSON-RPC `tools/call` requests to the `/mcp` endpoint. The
examples below target the local dev server; swap the port for `8021` under
Docker.

### List available tools

```bash
curl -sS http://127.0.0.1:8000/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### Discovery

```bash
curl -sS http://127.0.0.1:8000/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_capabilities","arguments":{}}}'
```

## Workflow 1 — resolve, then annotate

Use this when the caller's variant is an rsID, HGVS, SPDI, or loosely formatted.
`resolve_variant` returns the canonical coordinate cheaply (sub-kilobyte) and its
`_meta.next_commands` already contains the matching `annotate_variant` call.

```bash
# 1. resolve rs6025 to a canonical coordinate
curl -sS http://127.0.0.1:8000/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"resolve_variant","arguments":{"variant":"rs6025","assembly":"GRCh38"}}}'

# 2. full VEP annotation (compact tier by default)
curl -sS http://127.0.0.1:8000/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"annotate_variant","arguments":{"variant":"rs6025","assembly":"GRCh38","response_mode":"compact"}}}'
```

Coordinate and CNV inputs skip the recoder and can be annotated directly:

```json
{"name": "annotate_variant", "arguments": {"variant": "1-169549811-T-C", "assembly": "GRCh38"}}
```

### Choosing a response_mode

Start `compact` and widen only if needed:

- `minimal` — identity only (`variant_id`, `most_severe_consequence`, `gene_symbol`).
- `compact` (default) — adds position + variant-level `position_scores` (CADD/GERP,
  emitted once) + one prioritized `representative_transcript` (with the
  substitution-specific scores `revel`, `am_pathogenicity`/`am_class`; null fields
  dropped) + gnomAD frequencies.
- `standard` — transcript consequences (each null-stripped). By default
  (`transcripts="auto"`) uninformative MODIFIER neighbour transcripts are dropped
  and the list is capped to the most severe, with `_meta.transcripts` reporting
  `{shown, total}`; pass `transcripts="all"` for every isoform.
- `full` — entire normalized annotation, including `cadd_raw`, the `*_score`
  predictor values, and colocated variants.

> CADD and GERP (`conservation`) are genomic-position scores — equal across a
> variant's transcripts — so they are hoisted once to `position_scores` rather
> than repeated per transcript. REVEL/AlphaMissense are substitution-specific and
> stay per transcript.

See [mcp-tools.md](mcp-tools.md) for an example payload of each tier.

### Extra VEP flags

`annotate_variant` and `annotate_variants_batch` accept `vep_options` (an
allowlisted flag map). Disallowed keys return `invalid_input`. The default profile
already enables the headline predictors (`CADD`, `REVEL`, `AlphaMissense`,
`Conservation`) plus `hgvs`/`mane`/`numbers`/`canonical`/`domains`, so you only
need `vep_options` to add others (e.g. `EVE`, `dbscSNV`, `MaxEntScan`, `refseq`)
or to turn a default off (e.g. `{"REVEL": "0"}`).

```json
{"name": "annotate_variant", "arguments": {"variant": "1-169549811-T-C", "vep_options": {"EVE": "1", "dbscSNV": "1"}}}
```

`SpliceAI`, `dbNSFP`, and `LoF` are allowlisted but **not run by the public
Ensembl REST API**. Requesting one returns the annotation plus an explanatory
`note` field rather than silently dropping the flag (it only populates against a
VEP instance configured with the plugin). The scores typically pulled *from*
dbNSFP — REVEL, CADD, SIFT, PolyPhen, AlphaMissense — are available via the
dedicated toggles above.

## Workflow 2 — batch annotate (≤200, internal chunking)

Annotate many variants in one call. The batch is capped at **200** variants;
larger requests must be split client-side (>200 → `invalid_input`). Internally
the service chunks at 200 per upstream POST, applies a politeness delay, dedupes
identical canonical variants, and collects per-input errors so one bad variant
never fails the batch.

```bash
curl -sS http://127.0.0.1:8000/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"annotate_variants_batch","arguments":{"variants":["1-169549811-T-C","rs1799963","not-a-variant"],"assembly":"GRCh38","response_mode":"minimal"}}}'
```

The result carries `results` (each tagged with its original `input`), `errors`
(per-input `{input, error_code, message}`), and a `summary`
(`{requested, annotated, failed}`).

## Workflow 3 — recode identifiers

Translate a variant (or a batch, cap 200) between identifier systems without a
full VEP annotation: rsID ↔ HGVS (g./c./p./t.) ↔ VCF string ↔ SPDI.

```bash
curl -sS http://127.0.0.1:8000/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"recode_variant","arguments":{"variants":["rs6025","NM_000059.3:c.274G>A"],"assembly":"GRCh38"}}}'
```

Trim the payload with the optional `fields` filter:

```json
{"name": "recode_variant", "arguments": {"variants": ["rs6025"], "fields": "hgvsg,spdi,vcf_string"}}
```

## Workflow 4 — liftover GRCh37 ↔ GRCh38

Map a genomic coordinate between the two human assemblies. Only
`CHR-POS-REF-ALT` coordinates are liftable; HGVS/rsID are `unsupported_input`
(resolve them first). The two assemblies must differ.

```bash
# GRCh37 -> GRCh38
curl -sS http://127.0.0.1:8000/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"liftover_variant","arguments":{"variant":"1-169519049-T-C","from_assembly":"GRCh37","to_assembly":"GRCh38"}}}'
```

The reverse direction simply swaps `from_assembly`/`to_assembly`. Zero mappings →
`not_found`; more than one mapping → `ambiguous`.

## Dual-assembly notes

- The `assembly` argument selects the Ensembl REST host: GRCh38 →
  `rest.ensembl.org`, GRCh37 → `grch37.rest.ensembl.org`. The default is GRCh38.
- Coordinates are assembly-specific: the same `CHR-POS-REF-ALT` means different
  loci on GRCh37 vs GRCh38. Use `liftover_variant` to convert, not a plain
  re-annotation.
- rsIDs and HGVS recode to assembly-specific coordinates, so `resolve_variant`
  and `recode_variant` may return different `vcf_string` values per `assembly`.

## Rate limits, backoff, and resilience

`vep-link` treats Ensembl as an external research data service and honors its
limits:

- A concurrency semaphore caps in-flight upstream requests
  (`VEP_LINK_MAX_CONCURRENCY`, default 5); waiting past
  `VEP_LINK_QUEUE_WAIT_TIMEOUT` (default 20 s) yields fast backpressure surfaced
  as `rate_limited`.
- Retryable failures (HTTP 429/500/502/503/504 and transport errors) are retried
  with jittered exponential backoff (`VEP_LINK_BACKOFF_BASE_SECONDS` 1.0 →
  `VEP_LINK_BACKOFF_MAX_SECONDS` 20.0, up to `VEP_LINK_MAX_RETRIES` 4 attempts).
- On HTTP 429 the upstream `Retry-After` header is honored.
- Batch POSTs are chunked at `VEP_LINK_CHUNK_SIZE` (200) variants with a
  `VEP_LINK_INTER_CHUNK_DELAY_MS` (100 ms) politeness delay.

If retries are exhausted, the tool returns a structured error envelope:
`rate_limited` (429 after retries), `upstream_unavailable` (5xx / transport
error), or `upstream_timeout`. These are retryable — back off and retry, or
reduce parallelism. Treat live Ensembl rate limits as upstream state, not a local
failure.

## Caching

`resolve`, `annotate`, and `recode` results are deterministic per
`(input, assembly, options)` and are cached in-process with an
`async-lru` LRU (`VEP_LINK_CACHE_SIZE` entries, default 1024;
`VEP_LINK_CACHE_TTL_SECONDS` TTL, default 86400 s / 24 h). Identical calls within
the TTL skip the upstream client entirely. The cache is per-process and resets on
restart; there is no MCP tool to inspect or clear it.

## Health check

```bash
curl http://127.0.0.1:8000/health
# {"status":"healthy","service":"vep-link","version":"0.1.0"}

vep-link health --url http://127.0.0.1:8000
```

## Configuration

All settings use the `VEP_LINK_` env prefix (and an optional `.env`; copy
`.env.example` to `.env`). See the [README](../README.md#configuration) for the
full env-var table, or inspect the resolved configuration:

```bash
vep-link config --validate
```

## Production notes

- Prefer Streamable HTTP MCP behind HTTPS; protect public deployments with an
  authenticated reverse proxy.
- Keep MCP tools research-use scoped; never imply clinical decision support.
- Treat live Ensembl rate limits as upstream state, not local test failures.
