# Architecture

How `vep-link` turns arbitrary variant input into a shaped, cited annotation.

## Normalization pipeline

Every input is normalized to a canonical `CHR-POS-REF-ALT` coordinate, then
batched through the VEP `region` POST endpoint. Only VCF/CNV inputs skip the
recoder; rsID / HGVS / SPDI inputs go through Variant Recoder first to obtain a
`vcf_string`, then through VEP.

```
input ─► parse (coordinate | CNV | HGVS | rsID | SPDI)
          ├─ coordinate / CNV ──────────────► VEP region POST
          └─ HGVS / rsID / SPDI ─► Variant Recoder ─► vcf_string ─► VEP region POST
       ─► extract (transcript consequences, pathogenicity scores, gnomAD AF, prioritized transcript)
       ─► shape (minimal | compact | standard | full) ─► response + _meta + provenance
```

This single path is a contract, not an implementation detail: do not reintroduce
per-type VEP GET endpoints on the main annotation path (see `AGENTS.md`).

## Dual-assembly routing

The base URL is chosen per `assembly` argument:

| Assembly | Ensembl REST host |
|----------|-------------------|
| GRCh38 (default) | `https://rest.ensembl.org` |
| GRCh37 | `https://grch37.rest.ensembl.org` |

The two builds are **different hosts with independent health**, which is why the
circuit breaker below is per-assembly rather than global. Coordinates are
assembly-specific: use `liftover_variant` to convert between builds, never a
plain re-annotation.

## Default-on predictors

The public Ensembl REST serves several precomputed predictors as dedicated VEP
toggles, and `vep-link` enables the headline ones **by default**, so a plain
`annotate_variant` already carries them on the relevant (e.g. missense)
transcript:

- **CADD** (`cadd_phred` / `cadd_raw`)
- **REVEL** (`revel`)
- **AlphaMissense** (`am_pathogenicity` / `am_class`)
- **GERP** (`conservation`)
- plus SIFT and PolyPhen

The scores commonly pulled *from* dbNSFP (REVEL, CADD, SIFT, PolyPhen,
AlphaMissense) are covered by these toggles. `revel`, `am_pathogenicity`,
`am_class` and `conservation` ride along in `compact`, so they cost no extra
round trip.

> **Self-hosted-only caveat.** `dbNSFP`, `SpliceAI` and `LoF` remain allowlisted
> in `vep_options` for self-hosted VEP instances, but the **public Ensembl REST
> does not run them** — requesting them against the public endpoint yields no
> such fields.

## Rate-limit etiquette and batch caps

Ensembl is an external research data service and is treated as one:

- Batch POSTs are chunked at `VEP_LINK_CHUNK_SIZE` (200) variants with a
  `VEP_LINK_INTER_CHUNK_DELAY_MS` (100 ms) politeness delay between chunks.
- The batch tools accept at most `VEP_LINK_BATCH_MAX` (200) variants per call.
- A concurrency semaphore caps in-flight upstream requests
  (`VEP_LINK_MAX_CONCURRENCY`, default 5).
- HTTP 429 honours the upstream `Retry-After` header; retryable failures back off
  with jittered exponential backoff.

See [usage → rate limits, backoff, and resilience](usage.md#rate-limits-backoff-and-resilience).

## Upstream health: per-assembly circuit breaker

A circuit breaker tracks the two Ensembl REST hosts, fed by real call outcomes
plus a cheap background `/info/ping` probe
(`VEP_LINK_HEALTH_PROBE_INTERVAL_SECONDS`). When a host is degraded, `vep-link`
**warns early and fails fast** instead of hanging:

- every response carries a compact `_meta.upstream` status;
- retryable upstream errors include `retry_after_s` and a "retry on the healthy
  assembly" hint;
- an open circuit short-circuits to a clean `upstream_unavailable` rather than
  burning the caller's deadline.

Query it explicitly with the `check_upstream_health` tool, or read the
`vep://health` resource.

## response_mode tiers

`annotate_variant` and `annotate_variants_batch` shape their output into four
tiers:

| Mode | Returns |
|------|---------|
| `minimal` | `variant_id` + `most_severe_consequence` + `gene_symbol` + `_meta`. |
| `compact` (default) | minimal fields + position + a single prioritized `representative_transcript` + gnomAD `frequencies`. |
| `standard` | identity/position + **all** transcript consequences (each projected to the compact key set) + frequencies. |
| `full` | the entire normalized annotation (all transcripts + colocated variants/frequencies). |

Start with `compact` and widen only if needed, to control token cost.

## Naming and the gateway namespace

`serverInfo.name` is `vep-link`. Tool names are intentionally **unprefixed**
([Tool-Naming Standard v1](https://github.com/berntpopp/genefoundry-router/blob/main/docs/TOOL-NAMING-STANDARD-v1.md));
namespacing is the gateway's job. The canonical gateway namespace token is
**`vep`** — the GeneFoundry router mounts it as `mount(namespace="vep")`, so
`annotate_variant` surfaces as **`vep_annotate_variant`**. A CI guard
(`tests/unit/test_tool_names.py`) lints the live roster against the Standard.

As an **action / compute** server, `vep-link` uses domain action verbs
(`annotate`, `recode`, `liftover`) drawn from the ratified Tier-2 verb canon.
The readiness verb `check` (`check_upstream_health`) falls outside both verb
tiers and conforms instead through the ratified **ops/meta tag carve-out**: the
tool carries the `ops` tag, which the router's `check_leaf_name` validator and
this repo's naming guard both honour.
