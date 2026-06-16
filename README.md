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
batched through the VEP `region` POST endpoint:

```
input ─► classify (VCF | CNV | HGVS | rsID)
          ├─ VCF / CNV ─────────────────► VEP region POST
          └─ HGVS / rsID ─► Variant Recoder ─► vcf_string ─► VEP region POST
       ─► extract (transcript consequences, gnomAD AF, prioritized transcript)
       ─► shape (minimal | compact | standard | full) ─► response + _meta
```

## MCP tools

| Tool | Purpose |
|------|---------|
| `get_capabilities` | Server/tool metadata, assemblies, options, error codes. |
| `resolve_variant` | Any input → canonical `CHR-POS-REF-ALT` (+ gene, consequence). |
| `recode_variant` | All equivalent identifiers (rsID, HGVS g./c./p., VCF, SPDI). |
| `annotate_variant` | Full VEP annotation for one variant. |
| `annotate_variants_batch` | Batch VEP annotation (≤200/call, internal chunking). |
| `liftover_variant` | Lift a coordinate between GRCh37 and GRCh38. |

## Quickstart

```bash
uv sync --group dev
uv run vep-link serve              # FastAPI host (/health) + MCP at /mcp
uv run vep-link --help
```

See [`docs/`](docs/) for the full tool reference, response-mode tiers, and the
dual-assembly notes.

## Development

```bash
make ci-local      # format-check, lint, line-budget, typecheck, tests
make test-cov      # tests with coverage
```

## License

MIT © Bernt Popp. Built on the [Ensembl REST API](https://rest.ensembl.org).
Cite: McLaren W, et al. *The Ensembl Variant Effect Predictor.* Genome Biol.
2016;17:122. PMID:27268795.
