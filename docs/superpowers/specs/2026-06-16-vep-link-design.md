# vep-link — Design Specification

**Date:** 2026-06-16
**Author:** Bernt Popp
**Status:** Approved
**License:** MIT

## 1. Purpose & Scope

`vep-link` is a unified **REST API + MCP server** that wraps the **Ensembl VEP**
(Variant Effect Predictor) and **Variant Recoder** REST endpoints across **both
human reference assemblies**:

- **GRCh38** via `https://rest.ensembl.org`
- **GRCh37** via `https://grch37.rest.ensembl.org`

It annotates variants, recodes identifiers between every representation, resolves
any supported input to a canonical genomic coordinate, and lifts coordinates
between builds. It is **MCP-first**; the FastAPI host exposes only `/health` plus
the mounted MCP HTTP app at `/mcp`, matching the existing fleet
(`gnomad-link`, `litvar-link`, `spliceailookup-link`).

**Research use only — not for clinical decision support.**

### In scope (v1)
- VEP annotation (single + batch) with sensible default options and an
  allowlisted passthrough for extra VEP flags.
- Variant Recoder (single + batch) returning all equivalent identifiers.
- Canonical resolution of any input → `CHR-POS-REF-ALT`.
- Liftover between GRCh37 and GRCh38 (coordinate inputs).
- `get_capabilities`, structured error envelopes, `_meta`/provenance, schema
  resources, citation contract, research-use disclaimer.

### Out of scope (deferred to v2)
- The config-driven **scoring engine** from `variant-linker`
  (`nephro_variant_score` et al.). Porting it safely requires an
  AST-restricted/sandboxed expression evaluator (the JS original uses
  `new Function`, which must not be ported verbatim) plus curated formula
  configs. It is a separate, self-contained effort.

## 2. Architectural Backbone (proven by `variant-linker`)

The single most important design decision, validated by the upstream
`berntpopp/variant-linker` implementation:

> **Normalize every input to `CHR-POS-REF-ALT`, then batch through VEP
> _region POST_.** Only VCF/CNV inputs skip the recoder; rsID / HGVS (g./c./p.) /
> SPDI inputs go through **Variant Recoder first** to obtain the `vcf_string`,
> then through VEP.

Endpoint map (Ensembl REST, both hosts):

| Purpose | Method | Path | Body / key params |
|---|---|---|---|
| Variant Recoder (single) | GET | `/variant_recoder/human/{id}` | `?vcf_string=1` |
| Variant Recoder (batch) | POST | `/variant_recoder/homo_sapiens` | `{"ids": [...]}`, `?vcf_string=1` |
| VEP (batch, main path) | POST | `/vep/homo_sapiens/region` | `{"variants": ["CHR POS . REF ALT . . ."]}` |
| VEP (HGVS, single, legacy) | GET | `/vep/human/hgvs/{hgvs}` | — |
| VEP (id, single) | GET | `/vep/human/id/{id}` | — |
| Assembly map (liftover) | GET | `/map/human/GRCh37/{region}/GRCh38` | region `chr:start..end` |

Chunking: **200 variants per POST**, ~100 ms inter-chunk politeness delay,
bounded by a concurrency semaphore.

VEP default query profile (variant-linker's proven set + a little more):
`CADD=1, hgvs=1, mane=1, numbers=1, canonical=1, domains=1` (`merged=1` when the
caller requests merged Ensembl+RefSeq). Caller may add flags via a **validated,
allowlisted** `vep_options` param. Instance-dependent plugins (SpliceAI, dbNSFP)
are **not** available on public Ensembl REST; they are documented as opt-in and
surfaced in a note rather than silently dropped.

## 3. Stack & Fleet Conventions (mirror exactly)

- **Build:** uv + hatchling, `requires-python = ">=3.12"`, MIT, author "Bernt Popp".
- **Runtime deps:** fastapi, uvicorn[standard], gunicorn, pydantic v2,
  pydantic-settings, httpx, async-lru, mcp[cli], fastmcp (>=3.2 for task support,
  though v1 uses foreground tools), structlog, asgi-correlation-id,
  prometheus-client, orjson, rich, typer.
- **Dev deps:** pytest, pytest-asyncio, pytest-cov, pytest-mock, pytest-xdist,
  respx, ruff, mypy, pre-commit.
- **Tooling:** ruff line-length 100, target py312, extend-select
  `E,W,F,I,N,UP,B,C4,S,T20,SIM,RUF`; mypy non-strict (dynamic VEP JSON) with
  `ignore_missing_imports` overrides; pytest `asyncio_mode=auto`; coverage
  `fail_under = 80`, branch=true.
- **Discipline:** ≤600 LOC per file (pre-commit guard); modules split before
  they grow past ~500 lines.
- **MCP-first:** FastAPI host = `/health` + mounted MCP at `/mcp`. Unified
  transport via `UnifiedServerManager`.
- **Host port:** **8021** (8010/8012/8020/8479/8603/8765 already taken across the
  fleet); internal container port 8000.

## 4. Package Layout

```
vep-link/
  vep_link/
    __init__.py            # __version__ = "0.1.0"
    config.py              # Settings (pydantic-settings); GenomeBuild Literal; vep_url/recoder_url/map_url(build)
    cli.py                 # typer app: serve | config | health | version  (direct commands)
    server_manager.py      # UnifiedServerManager: FastAPI host (/health) + mounted MCP, CORS, correlation-id, lifespan
    exceptions.py          # VariantParseError, UnsupportedContigError, UpstreamInputError, RateLimitedError, EnsemblApiError, ConfigurationError
    logging_config.py      # structlog processor chain (json|console), static service/version fields
    variant.py             # VariantInput{kind,value}; parse_variant_input; clean_hgvs; to_vep_line; detect_format (VCF|CNV|HGVS catch-all)
    api/
      __init__.py
      base_client.py       # lazy singleton httpx.AsyncClient; asyncio.Semaphore; jittered backoff; 429/Retry-After; retryable {429,500,502,503,504}
      ensembl_client.py    # build-aware: vep_region_post, vep_hgvs_get, vep_id_get, recoder_get, recoder_post, assembly_map; 200-chunking + delay
    services/
      __init__.py
      vep_service.py       # orchestration: resolve / annotate / annotate_batch / recode / liftover; async-lru caching; build dispatch
      extraction.py        # flatten transcript_consequences; most_severe fallback; transcript priority pick>mane>canonical>first; gnomAD from colocated_variants
    models/
      __init__.py          # central re-export
      enums.py             # GenomeBuild, ResponseMode, ConsequenceImpact, InputKind
      requests.py          # pydantic request models
      responses.py         # pydantic response models (variant annotation, recoding, liftover)
    mcp/
      __init__.py
      facade.py            # create_vep_mcp(service_factory) -> FastMCP
      errors.py            # McpErrorContext; run_mcp_tool; error envelope; error code enum; install_validation_error_handler
      annotations.py       # READ_ONLY_OPEN_WORLD
      resources.py         # capabilities payload; RESEARCH_USE_NOTICE; provenance; citations; vep:// resources
      shaping.py           # response_mode tiers: minimal | compact | standard | full
      schema.py            # output schema relax/validate helpers
      tools/
        __init__.py        # register_vep_tools(mcp, service_factory)
        capabilities.py    # get_capabilities
        resolve.py         # resolve_variant
        recode.py          # recode_variant
        annotate.py        # annotate_variant, annotate_variants_batch
        liftover.py        # liftover_variant
  server.py                # HTTP entrypoint (module exposing `app`)
  mcp_server.py            # stdio entrypoint
  tests/
    conftest.py            # no-network guard; respx fixtures; StubService; FastMCP facade fixture
    fixtures/              # canned Ensembl JSON (recoder GET/POST, VEP region, assembly map) — shapes from variant-linker
    unit/                  # one module per source module
    integration/           # @pytest.mark.integration live tests (excluded from default CI)
  docker/{Dockerfile, docker-compose.yml}
  Makefile
  pyproject.toml
  README.md
  docs/
  CLAUDE.md
  AGENTS.md
  CHANGELOG.md
  .pre-commit-config.yaml
  .env.example
  .gitignore
  scripts/check_file_size.py   # 600-LOC budget guard
```

## 5. Tool Surface (6 MCP tools)

Every tool returns a payload carrying `_meta` (`request_id`,
`capabilities_version`, `timing.elapsed_ms`, `unsafe_for_clinical_use: true`,
`next_commands`, `see_also`) plus provenance, and uses the structured error
envelope on failure. Every tool carries `annotations=READ_ONLY_OPEN_WORLD`.

| Tool | Key args | Behavior |
|---|---|---|
| `get_capabilities` | — | Server/tool metadata, assemblies, input formats, VEP-option allowlist, response modes, error codes, citation contract, `capabilities_version` hash. |
| `resolve_variant` | `variant`, `assembly="GRCh38"` | Any input → canonical `CHR-POS-REF-ALT` (+ gene_symbol, most_severe_consequence). Recoder for rsID/HGVS; direct for VCF/CNV. |
| `recode_variant` | `variant \| variants[]`, `assembly="GRCh38"`, `fields=None` | Variant Recoder GET/POST → all equivalents (rsID, HGVS g./c./p./t., VCF string, SPDI). Single + batch (cap 200). |
| `annotate_variant` | `variant`, `assembly="GRCh38"`, `response_mode="compact"`, `vep_options=None` | parse → recode-if-needed → VEP region POST → extract → shape. |
| `annotate_variants_batch` | `variants[]`, `assembly="GRCh38"`, `response_mode="compact"`, `vep_options=None` | Cap **200/call**; internal 200-chunking + delay + semaphore; dedup by canonical key; **per-input** results AND per-input error objects (one bad variant never fails the batch). |
| `liftover_variant` | `variant`, `from_assembly`, `to_assembly` | `/map/human/GRCh37/{region}/GRCh38` and reverse. Coordinate/VCF only; 0 maps → `not_found`; >1 → `ambiguous`; HGVS/rsID → `unsupported_input`. |

### response_mode tiers
- `minimal` — variant_id + most_severe_consequence + gene_symbol + `_meta`.
- `compact` (default) — representative (prioritized) transcript + key fields.
- `standard` — all transcript consequences, key fields each.
- `full` — raw-ish VEP payload (all fields) + colocated variants/frequencies.

## 6. Data Flow

```
input
  └─ variant.parse_variant_input → VariantInput{kind ∈ coordinate|vcf|cnv|hgvs|rsid, value}
       ├─ vcf / cnv ───────────────► VEP region POST
       └─ hgvs / rsid ─► Variant Recoder ─► first valid vcf_string ─► VEP region POST
  └─ extraction.flatten (per-transcript; most_severe fallback; priority pick>mane>canonical>first;
        gnomAD AF from colocated_variants[].frequencies[].{gnomade,gnomadg})
  └─ shaping(response_mode) ─► envelope (_meta + provenance + disclaimer)
```

Base URL is chosen per `assembly`. Canonical key = `CHR-POS-REF-ALT`. VEP input
line format = `"CHR POS . REF ALT . . ."`; CNV line = `"chr start end type allele_number"`.

## 7. Error Handling

Structured envelope:

```json
{
  "error": {
    "code": "<error_code>",
    "message": "<human message>",
    "recovery": "<how to recover>",
    "fallback_tool": "get_capabilities",
    "next_commands": [{"tool": "...", "arguments": {}}]
  },
  "_meta": { "...": "..." }
}
```

Error codes:

| Code | Trigger | Retryable |
|---|---|---|
| `invalid_input` | Unparseable variant / bad arguments | no |
| `unsupported_input` | Unsupported contig (e.g. MT for liftover), HGVS for liftover | no |
| `not_found` | Recoder returns no vcf_string; VEP/no overlap; 0 liftover maps | no |
| `build_mismatch` | Coordinates inconsistent with requested assembly | no |
| `ambiguous` | >1 liftover mapping | no |
| `rate_limited` | HTTP 429 after retries (Retry-After honored) | yes |
| `upstream_unavailable` | 5xx / transport error | yes |
| `upstream_timeout` | Timeout | yes |
| `output_validation_failed` | Output schema drift | no |
| `internal_error` | Unexpected fault (sanitized, correlation id) | no |

## 8. HTTP Client & Resilience

- Lazy singleton `httpx.AsyncClient` (built once under a lock, closed on shutdown),
  `Accept: application/json`, `Content-Type: application/json` on POST, configurable
  `User-Agent`, `follow_redirects=True`.
- `asyncio.Semaphore` concurrency cap with bounded queue wait (fast backpressure →
  `rate_limited`).
- Jittered exponential backoff: base 1.0 s, factor 2, full jitter, max retries 4,
  retryable status `{429, 500, 502, 503, 504}` + transport errors.
- **`Retry-After` honored** on 429 (integer seconds or HTTP-date), using
  `max(computed_backoff, retry_after) + jitter`.
- 200-variant chunk cap + ~100 ms inter-chunk delay for batch POST.
- `async-lru` caching on resolve/annotate/recode keyed by
  `(normalized_input, assembly, options_hash)`; configurable maxsize + TTL.

## 9. Testing (TDD, zero real network)

- **respx mocks ALL** Ensembl endpoints on **both** hosts. A `conftest`
  autouse fixture installs a no-network guard so any un-mocked request fails the
  test (zero real network).
- Fixtures captured from `variant-linker`'s real response shapes (recoder GET
  per-allele, recoder POST array, VEP region array with `transcript_consequences`
  / `colocated_variants`, assembly map).
- `StubService` for MCP tool-layer tests (records calls, simulates each error).
- Unit coverage: variant classification + HGVS cleaning + VEP-line formatting;
  base_client backoff / Retry-After / semaphore; ensembl_client chunking + URL
  selection; extraction flattening / priority / gnomAD path; every tool success +
  every error code; shaping tiers; capabilities payload + version hash; CLI
  commands; logging config.
- Integration tests `@pytest.mark.integration` (excluded from default CI; hit live
  Ensembl manually).
- Coverage gate **80%**, branch coverage on.

## 10. Config (`Settings`)

| Setting | Default |
|---|---|
| `VEP_GRCH38_URL` | `https://rest.ensembl.org` |
| `VEP_GRCH37_URL` | `https://grch37.rest.ensembl.org` |
| `DEFAULT_ASSEMBLY` | `GRCh38` |
| `REQUEST_TIMEOUT` | 60 |
| `MAX_CONCURRENCY` | 5 |
| `QUEUE_WAIT_TIMEOUT` | 20 |
| `MAX_RETRIES` | 4 |
| `BACKOFF_BASE_SECONDS` | 1.0 |
| `BACKOFF_MAX_SECONDS` | 20.0 |
| `CHUNK_SIZE` | 200 |
| `BATCH_MAX` | 200 |
| `INTER_CHUNK_DELAY_MS` | 100 |
| `CACHE_SIZE` | 1024 |
| `CACHE_TTL_SECONDS` | 86400 |
| `USER_AGENT` | `vep-link/<version> (+https://github.com/berntpopp/vep-link)` |
| `HOST` / `PORT` | `127.0.0.1` / `8000` |
| `MCP_PATH` | `/mcp` |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` |

Env prefix `VEP_LINK_` (fleet convention), `.env` supported.

## 11. Citation / Provenance Contract

Every annotation/recoding result carries provenance:

```json
"provenance": {
  "data_source": "Ensembl VEP / Variant Recoder REST",
  "ensembl_release": "<from upstream when available>",
  "assembly": "GRCh38",
  "endpoint": "https://rest.ensembl.org/vep/homo_sapiens/region",
  "retrieved": "<iso8601>",
  "recommended_citation": "McLaren W, et al. The Ensembl Variant Effect Predictor. Genome Biol. 2016;17:122. PMID:27268795."
}
```

`RESEARCH_USE_NOTICE = "Research use only; not for clinical decision support."`
appears in `_meta.unsafe_for_clinical_use`, the capabilities payload, the
`vep://research-use` resource, the CLI, and the README.

MCP resources: `vep://capabilities`, `vep://usage`, `vep://reference`,
`vep://citations`, `vep://research-use`.

## 12. Deliverables

Scaffolded package; REST + MCP servers; high-coverage test suite (respx, zero
network); `docker/` Dockerfile + compose; Makefile; README + `docs/`; CLAUDE.md +
AGENTS.md + CHANGELOG.md; ruff + mypy clean; pre-commit green.
