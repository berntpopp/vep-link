# vep-link

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![CI](https://github.com/berntpopp/vep-link/actions/workflows/ci.yml/badge.svg)](https://github.com/berntpopp/vep-link/actions/workflows/ci.yml)
[![Conformance](https://github.com/berntpopp/vep-link/actions/workflows/conformance.yml/badge.svg)](https://github.com/berntpopp/vep-link/actions/workflows/conformance.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An MCP (Model Context Protocol) server over the [Ensembl REST API](https://rest.ensembl.org/),
exposing the Variant Effect Predictor and Variant Recoder across both human reference
assemblies — GRCh38 (`rest.ensembl.org`) and GRCh37 (`grch37.rest.ensembl.org`). It annotates
variants, recodes identifiers, resolves any supported input to a canonical genomic coordinate,
and lifts coordinates between builds.

> [!IMPORTANT]
> Research use only. Not clinical decision support. Do not use for diagnosis,
> treatment, triage, or patient management.

## Why

Ensembl already serves VEP over a public REST API, but it is shaped for scripts, not for
models. Its variant endpoints are split by input type, so a caller holding an rsID, an HGVS
string and a VCF line must know three different routes; a raw VEP response returns every
transcript and every colocated variant, which is far more context than an answer needs; and
the two reference assemblies live on two separate hosts with independent uptime.

`vep-link` collapses that into one surface. Every input — coordinate, CNV, HGVS, rsID, SPDI —
is normalized to a canonical `CHR-POS-REF-ALT` and batched through the single VEP `region`
POST endpoint, then shaped into one of four `response_mode` tiers so a model pays only for the
detail it asked for. Batches are chunked and rate-limited to Ensembl's etiquette, and a
per-assembly circuit breaker fails fast with a "retry on the healthy assembly" hint instead of
hanging when one build's host is degraded.

## Quick start

Hosted — no install required:

```bash
claude mcp add --transport http vep-link https://vep-link.genefoundry.org/mcp
```

Local (Python 3.12+, [uv](https://github.com/astral-sh/uv)). There is no data build step:
`vep-link` is a live-API client and serves as soon as it boots.

```bash
uv sync --group dev
make dev                                   # FastAPI /health host + MCP at /mcp on :8000
curl http://127.0.0.1:8000/health
claude mcp add --transport http vep-link http://127.0.0.1:8000/mcp
```

The Docker stack publishes the container's port `8000` on host port **8021**
(`docker compose -f docker/docker-compose.yml up`); see [deployment](docs/deployment.md).

## Tools

| Tool | Purpose |
|------|---------|
| `get_capabilities` | Server metadata: assemblies, input formats, VEP-option allowlist, response modes, error codes, citation contract, `capabilities_version` hash. |
| `resolve_variant` | Any input → canonical `CHR-POS-REF-ALT` (+ `gene_symbol`, `most_severe_consequence`). |
| `recode_variant` | All equivalent identifiers (rsID, HGVS g./c./p./t., VCF string, SPDI). Single or batch (cap 200). |
| `annotate_variant` | Full VEP annotation for one variant, shaped to a `response_mode`. |
| `annotate_variants_batch` | Batch VEP annotation (≤200/call, internal chunking + dedup, per-input errors). |
| `liftover_variant` | Lift a coordinate between GRCh37 and GRCh38. |
| `check_upstream_health` | Live Ensembl REST readiness per assembly (circuit-breaker snapshot). |

Leaf names are intentionally unprefixed per
[Tool-Naming Standard v1](https://github.com/berntpopp/genefoundry-router/blob/main/docs/TOOL-NAMING-STANDARD-v1.md) —
namespacing is the gateway's job. Behind
[genefoundry-router](https://github.com/berntpopp/genefoundry-router) this server mounts under
the token `vep`, so the tools above surface as `vep_<tool>` (e.g. `vep_annotate_variant`).

## Data & provenance

All annotations come live from the **Ensembl REST API** — GRCh38 via
`https://rest.ensembl.org`, GRCh37 via `https://grch37.rest.ensembl.org`. There is no local
mirror, no bundle and no ingest: freshness is whatever Ensembl currently serves. Each
annotation carries a `provenance` block stamping the data source, assembly, endpoint and
retrieval time. The public endpoint needs no API key, and its precomputed CADD, REVEL,
AlphaMissense and GERP predictors are enabled by
default (`dbNSFP`, `SpliceAI` and `LoF` are allowlisted but only run on self-hosted VEP
instances — see [architecture](docs/architecture.md#default-on-predictors)).

The data are Ensembl's, not this repository's: consult
[Ensembl's legal notice](https://www.ensembl.org/info/about/legal/disclaimer.html) for the
terms governing their use.

Required citation, returned verbatim as `recommended_citation` on every annotation:

> McLaren W, et al. *The Ensembl Variant Effect Predictor.* Genome Biol. 2016;17:122.
> PMID:27268795.

## Documentation

- [Usage guide](docs/usage.md) — practical workflows and example tool calls.
- [MCP tool reference](docs/mcp-tools.md) — per-tool arguments, payloads, and resources.
- [Architecture](docs/architecture.md) — the normalization pipeline, dual-assembly routing, predictors, circuit breaker, and `response_mode` tiers.
- [Configuration](docs/configuration.md) — every `VEP_LINK_*` variable, the Host/Origin/CORS guards, and the CLI.
- [Deployment](docs/deployment.md) — Docker, reverse proxy, MCP client configuration, and the stdio entrypoint.
- [API reference](docs/api.md) — REST surface, error envelope, `_meta`.
- [Changelog](CHANGELOG.md) — release history.
- [AGENTS.md](AGENTS.md) — engineering conventions for humans and coding agents.

## Contributing

See [`AGENTS.md`](AGENTS.md) for engineering conventions, the file-size budget, and the
zero-real-network test rule. `make ci-local` is the definition-of-done gate: format, lint, line
budget, README standard, mypy, and tests.

## License

[MIT](LICENSE) © Bernt Popp — the code only. The annotations this server returns are Ensembl
data, governed by [Ensembl's terms](https://www.ensembl.org/info/about/legal/disclaimer.html)
and subject to the citation above.
