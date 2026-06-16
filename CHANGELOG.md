# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
