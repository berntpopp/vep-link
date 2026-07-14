# Configuration

Every setting uses the `VEP_LINK_` env prefix and may be supplied via the
environment or an optional `.env` file (copy [`.env.example`](../.env.example) to
`.env`). Inspect what the server actually resolved with:

```bash
uv run vep-link config --validate
```

## Environment variables

| Env var | Default | Purpose |
|---------|---------|---------|
| `VEP_LINK_VEP_GRCH38_URL` | `https://rest.ensembl.org` | GRCh38 Ensembl REST host. |
| `VEP_LINK_VEP_GRCH37_URL` | `https://grch37.rest.ensembl.org` | GRCh37 Ensembl REST host. |
| `VEP_LINK_DEFAULT_ASSEMBLY` | `GRCh38` | Default assembly. |
| `VEP_LINK_REQUEST_TIMEOUT` | `30` | Per-attempt upstream read timeout (s). |
| `VEP_LINK_CONNECT_TIMEOUT` | `10.0` | Connection (TCP/TLS) timeout (s) — fast-fail on a stalled handshake. |
| `VEP_LINK_OVERALL_DEADLINE_SECONDS` | `45.0` | Hard wall-clock cap on one request across all retries. |
| `VEP_LINK_MAX_CONCURRENCY` | `5` | Concurrent upstream request cap. |
| `VEP_LINK_QUEUE_WAIT_TIMEOUT` | `20` | Max wait (s) for a concurrency slot before backpressure. |
| `VEP_LINK_MAX_RETRIES` | `2` | Retry attempts on retryable failures. |
| `VEP_LINK_BACKOFF_BASE_SECONDS` | `1.0` | Backoff base. |
| `VEP_LINK_BACKOFF_MAX_SECONDS` | `20.0` | Backoff ceiling. |
| `VEP_LINK_CHUNK_SIZE` | `200` | Variants per upstream POST chunk. |
| `VEP_LINK_BATCH_MAX` | `200` | Max variants per batch tool call. |
| `VEP_LINK_INTER_CHUNK_DELAY_MS` | `100` | Politeness delay between chunks. |
| `VEP_LINK_CACHE_SIZE` | `1024` | In-process LRU cache entries. |
| `VEP_LINK_CACHE_TTL_SECONDS` | `86400` | Cache entry TTL. |
| `VEP_LINK_HEALTH_PROBE_ENABLED` | `true` | Run the background upstream-health probe. |
| `VEP_LINK_HEALTH_PROBE_INTERVAL_SECONDS` | `60` | Seconds between `/info/ping` probes per host. |
| `VEP_LINK_HEALTH_PROBE_TIMEOUT` | `8` | Per-probe timeout (s). |
| `VEP_LINK_CIRCUIT_FAILURE_THRESHOLD` | `3` | Consecutive failures before a host's circuit opens. |
| `VEP_LINK_CIRCUIT_COOLDOWN_SECONDS` | `30` | Open-circuit cooldown before a recovery probe. |
| `VEP_LINK_MCP_TRANSPORT` | `unified` | `unified` (host + MCP) or `http` (MCP only). |
| `VEP_LINK_MCP_HOST` | `127.0.0.1` | Bind host. |
| `VEP_LINK_MCP_PORT` | `8000` | Bind port. |
| `VEP_LINK_MCP_PATH` | `/mcp` | MCP mount path. |
| `VEP_LINK_MCP_ALLOWED_HOSTS` | `["localhost","127.0.0.1","::1"]` | Exact HTTP Host allowlist as JSON; wildcards are rejected. |
| `VEP_LINK_MCP_ALLOWED_ORIGINS` | `[]` | Exact browser Origin allowlist as JSON; absent Origin remains allowed. |
| `VEP_LINK_LOG_LEVEL` | `INFO` | Log level. |
| `VEP_LINK_LOG_FORMAT` | `json` | `json` (prod) or `console` (dev). |
| `VEP_LINK_CORS_ORIGINS` | `*` | Comma-separated CORS origins. |
| `VEP_LINK_USER_AGENT` | `vep-link/0.1 (research MCP; +https://github.com/berntpopp/vep-link)` | Upstream User-Agent. |

The VEP annotation profile itself is **not** env-tunable: every `annotate` call
enables the Ensembl REST toggles `CADD`, `REVEL`, `AlphaMissense`, `Conservation`
(GERP), `hgvs`, `mane`, `numbers`, `canonical` and `domains` by default. Callers
override them per call through the annotate tools' allowlist-checked
`vep_options` argument (see [architecture](architecture.md#default-on-predictors)).

## Host, Origin, and CORS

Host and Origin validation is strict on the health, metrics, and MCP routes.

- Add reverse-proxy hostnames as **exact** entries in
  `VEP_LINK_MCP_ALLOWED_HOSTS`. Wildcards are rejected.
- Browser deployments must configure the same exact origins in **both**
  `VEP_LINK_MCP_ALLOWED_ORIGINS` and `VEP_LINK_CORS_ORIGINS`: transport
  validation and browser CORS are independent controls, and setting only one of
  them will fail the other.

## CLI

```bash
vep-link serve [--transport unified|http] [--host H] [--port P] [--mcp-path /mcp] [--log-level INFO] [--dev]
vep-link config [--validate]    # show resolved configuration
vep-link health [--url URL]     # probe a running server's /health
vep-link version
```

`vep-link serve` supports **only** the `unified` and `http` transports. stdio is
served exclusively by `mcp_server.py`; there is no dedicated stdio console
script — see [deployment](deployment.md#stdio-local-entrypoint).
