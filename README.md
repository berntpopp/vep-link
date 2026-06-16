# vep-link

**Unified REST API + MCP server for Ensembl VEP and Variant Recoder across both
human reference assemblies** — GRCh38 (`rest.ensembl.org`) and GRCh37
(`grch37.rest.ensembl.org`).

> **Research use only — not for clinical decision support.**

`vep-link` annotates variants (Variant Effect Predictor), recodes identifiers
(Variant Recoder), resolves any supported input to a canonical genomic
coordinate, and lifts coordinates between builds. It is **MCP-first**: a thin
FastAPI host exposes `/health` and mounts the MCP HTTP app at `/mcp`, matching
the sibling fleet (`gnomad-link`, `litvar-link`, `spliceailookup-link`).

## Architecture

Every input is normalized to a canonical `CHR-POS-REF-ALT` coordinate, then
batched through the VEP `region` POST endpoint. Only VCF/CNV inputs skip the
recoder; rsID / HGVS / SPDI inputs go through Variant Recoder first to obtain a
`vcf_string`, then through VEP.

```
input ─► parse (coordinate | CNV | HGVS | rsID | SPDI)
          ├─ coordinate / CNV ──────────────► VEP region POST
          └─ HGVS / rsID / SPDI ─► Variant Recoder ─► vcf_string ─► VEP region POST
       ─► extract (transcript consequences, gnomAD AF, prioritized transcript)
       ─► shape (minimal | compact | standard | full) ─► response + _meta + provenance
```

Base URL is chosen per `assembly`: GRCh38 → `https://rest.ensembl.org`,
GRCh37 → `https://grch37.rest.ensembl.org`. Batch POSTs are chunked at 200
variants with a short inter-chunk delay and a concurrency cap.

## MCP tools

| Tool | Purpose |
|------|---------|
| `get_capabilities` | Server/tool metadata: assemblies, input formats, VEP-option allowlist, response modes, error codes, citation contract, `capabilities_version` hash. |
| `resolve_variant` | Any input → canonical `CHR-POS-REF-ALT` (+ `gene_symbol`, `most_severe_consequence`). |
| `recode_variant` | All equivalent identifiers (rsID, HGVS g./c./p./t., VCF string, SPDI). Single + batch (cap 200). |
| `annotate_variant` | Full VEP annotation for one variant, shaped to a `response_mode`. |
| `annotate_variants_batch` | Batch VEP annotation (≤200/call, internal chunking + dedup, per-input errors). |
| `liftover_variant` | Lift a coordinate between GRCh37 and GRCh38. |

See [`docs/mcp-tools.md`](docs/mcp-tools.md) for the full per-tool reference.

### response_mode tiers (`annotate_variant` / `annotate_variants_batch`)

| Mode | Returns |
|------|---------|
| `minimal` | `variant_id` + `most_severe_consequence` + `gene_symbol` + `_meta`. |
| `compact` (default) | minimal fields + position + a single prioritized `representative_transcript` + gnomAD `frequencies`. |
| `standard` | identity/position + **all** transcript consequences (each projected to the compact key set) + frequencies. |
| `full` | the entire normalized annotation (all transcripts + colocated variants/frequencies). |

Start `compact` and widen only if needed to control token cost.

## Quickstart

```bash
uv sync --group dev
uv run vep-link serve              # FastAPI host (/health) + MCP at /mcp
uv run vep-link --help
```

The local dev server listens on `http://127.0.0.1:8000` (`/health` and
`/mcp`). `make dev` runs the same with console logging.

## Docker

The Compose stack publishes the internal container port `8000` on host port
**8021** (override with `VEP_LINK_HOST_PORT`):

```bash
docker compose -f docker/docker-compose.yml up
curl http://localhost:8021/health
```

## MCP client configuration

### HTTP (Docker stack, host port 8021)

```bash
claude mcp add --transport http vep-link http://localhost:8021/mcp
```

```json
{
  "mcpServers": {
    "vep-link": {
      "type": "http",
      "url": "http://localhost:8021/mcp"
    }
  }
}
```

For a local non-Docker server, use `http://127.0.0.1:8000/mcp`.

### stdio (local entrypoint)

`mcp_server.py` runs the same MCP facade over stdio (there is no dedicated
console script; invoke the module directly):

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

Note: the `vep-link serve` CLI command only supports the `unified` and `http`
transports; stdio is served exclusively by `mcp_server.py`.

## Configuration

All settings use the `VEP_LINK_` env prefix (and an optional `.env`; copy
`.env.example` to `.env`).

| Env var | Default | Purpose |
|---------|---------|---------|
| `VEP_LINK_VEP_GRCH38_URL` | `https://rest.ensembl.org` | GRCh38 Ensembl REST host. |
| `VEP_LINK_VEP_GRCH37_URL` | `https://grch37.rest.ensembl.org` | GRCh37 Ensembl REST host. |
| `VEP_LINK_DEFAULT_ASSEMBLY` | `GRCh38` | Default assembly. |
| `VEP_LINK_REQUEST_TIMEOUT` | `60` | Upstream request timeout (s). |
| `VEP_LINK_MAX_CONCURRENCY` | `5` | Concurrent upstream request cap. |
| `VEP_LINK_QUEUE_WAIT_TIMEOUT` | `20` | Max wait (s) for a concurrency slot before backpressure. |
| `VEP_LINK_MAX_RETRIES` | `4` | Retry attempts on retryable failures. |
| `VEP_LINK_BACKOFF_BASE_SECONDS` | `1.0` | Backoff base. |
| `VEP_LINK_BACKOFF_MAX_SECONDS` | `20.0` | Backoff ceiling. |
| `VEP_LINK_CHUNK_SIZE` | `200` | Variants per upstream POST chunk. |
| `VEP_LINK_BATCH_MAX` | `200` | Max variants per batch tool call. |
| `VEP_LINK_INTER_CHUNK_DELAY_MS` | `100` | Politeness delay between chunks. |
| `VEP_LINK_CACHE_SIZE` | `1024` | In-process LRU cache entries. |
| `VEP_LINK_CACHE_TTL_SECONDS` | `86400` | Cache entry TTL. |
| `VEP_LINK_MCP_TRANSPORT` | `unified` | `unified` (host + MCP) or `http` (MCP only). |
| `VEP_LINK_MCP_HOST` | `127.0.0.1` | Bind host. |
| `VEP_LINK_MCP_PORT` | `8000` | Bind port. |
| `VEP_LINK_MCP_PATH` | `/mcp` | MCP mount path. |
| `VEP_LINK_LOG_LEVEL` | `INFO` | Log level. |
| `VEP_LINK_LOG_FORMAT` | `json` | `json` (prod) or `console` (dev). |
| `VEP_LINK_CORS_ORIGINS` | `*` | Comma-separated CORS origins. |
| `VEP_LINK_USER_AGENT` | `vep-link/0.1 (research MCP; +https://github.com/berntpopp/vep-link)` | Upstream User-Agent. |

## CLI

```bash
vep-link serve [--transport unified|http] [--host H] [--port P] [--mcp-path /mcp] [--log-level INFO] [--dev]
vep-link config [--validate]    # show resolved configuration
vep-link health [--url URL]     # probe a running server's /health
vep-link version
```

## Development

```bash
make install       # uv sync --group dev
make dev           # dev server (console logs)
make test          # unit tests
make test-cov      # tests with coverage (80% floor)
make ci-local      # format-check, lint, line-budget, typecheck, tests
```

All Ensembl calls in tests are mocked with `respx`; the no-network guard in
`tests/conftest.py` fails any un-mocked request. Live tests are marked
`integration` and excluded from default CI (`make test-integration`).

## Documentation

- [Usage guide](docs/usage.md) — practical workflows and example tool calls.
- [API reference](docs/api.md) — REST surface, error envelope, `_meta`.
- [MCP tool reference](docs/mcp-tools.md) — per-tool arguments and payloads.

## License & citation

MIT © Bernt Popp. Built on the [Ensembl REST API](https://rest.ensembl.org).

Cite: McLaren W, et al. *The Ensembl Variant Effect Predictor.* Genome Biol.
2016;17:122. PMID:27268795.

**Research use only; not for clinical decision support.**
