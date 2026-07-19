# MCP Tool Reference

`vep-link` exposes **seven** MCP tools over Streamable HTTP (and stdio). The seven
registered tools are read-only, idempotent Ensembl operations and carry
`annotations=READ_ONLY_OPEN_WORLD`. Each section below documents its tool's
success fields and error behavior; shared error-envelope fields are defined in
[api.md](api.md).

Canonical workflow: `get_capabilities` (discovery) → `resolve_variant` (any input
→ canonical coordinate) → `recode_variant` (equivalent IDs, optional) →
`annotate_variant` / `annotate_variants_batch` (full VEP) → `liftover_variant`
(cross-build coordinate conversion).

The registry also includes `check_upstream_health`, an on-demand diagnostic for
the GRCh38 and GRCh37 Ensembl REST hosts. Call it before a large batch or after
upstream failures; it is not a variant-processing step in the workflow above.

> **Research use only; not for clinical decision support.**

---

## Common metadata

Successful payload bodies are tool-specific. Where the examples below include
`_meta`, its shared fields are described fully in [api.md](api.md):

```json
"_meta": {
  "tool": "annotate_variant",
  "request_id": "a1b2c3d4e5f6",
  "timing": {"elapsed_ms": 37},
  "capabilities_version": "<12-hex-char hash>",
  "unsafe_for_clinical_use": true,
  "next_commands": [],
  "assembly": "GRCh38"
}
```

`timing.elapsed_ms` is the measured wall-clock cost of the tool call (stamped on
both success and error envelopes). `annotate_variant` additionally carries a
`provenance` block (with a real `retrieved` timestamp) and populates
`_meta.next_commands` with ready-to-call follow-ups (`recode_variant`,
`liftover_variant`, and a widen-to-`all` re-call when the `standard` view is
truncated); `resolve_variant` points its `next_commands` at `annotate_variant`.

---

## `get_capabilities`

**Purpose.** The always-readable discovery document. Makes no upstream call and
never fails, so a confused client can always fall back to it. Read it first in a
cold session; a warm client compares the `capabilities_version` hash echoed in
every `_meta` and skips re-fetching when unchanged.

**Arguments.** None.

**Returns.** The static capabilities document plus `_meta`:

```json
{
  "server": "vep-link",
  "server_version": "0.2.0",
  "mcp_protocol_version": "2025-06-18",
  "research_use_only": true,
  "disclaimer": "Research use only; not for clinical decision support.",
  "assemblies": ["GRCh38", "GRCh37"],
  "default_assembly": "GRCh38",
  "input_formats": [
    "coordinate (CHR-POS-REF-ALT)", "rsID", "HGVS (g./c./n./p.)", "SPDI",
    "CNV (chr:start-end:TYPE)"
  ],
  "response_modes": ["minimal", "compact", "standard", "full"],
  "response_mode_tiers": {
    "minimal": "variant_id + most_severe_consequence + gene_symbol + _meta",
    "compact": "representative (prioritized) transcript + key fields + position_scores (default)",
    "standard": "transcript consequences (filtered/capped, null-stripped); transcripts='all' for every isoform",
    "full": "raw-ish VEP payload (all fields) + colocated variants/frequencies"
  },
  "tools": [{"name": "get_capabilities", "summary": "...", "token_cost_hint": "low"}, "..."],
  "error_codes": [
    "invalid_input", "not_found", "ambiguous_query",
    "upstream_unavailable", "rate_limited", "internal"
  ],
  "error_subcodes": [
    "unsupported_input", "build_mismatch", "upstream_timeout", "output_validation_failed"
  ],
  "warning_codes": ["multiple_alts", "ref_not_validated"],
  "vep_default_options": {"CADD": "1", "REVEL": "1", "AlphaMissense": "1", "Conservation": "1", "hgvs": "1", "mane": "1", "numbers": "1", "canonical": "1", "domains": "1"},
  "vep_option_allowlist": ["AlphaMissense", "CADD", "Conservation", "EVE", "REVEL", "SpliceAI", "dbNSFP", "..."],
  "batch_max": 200,
  "citation": {
    "vep": "McLaren W, et al. The Ensembl Variant Effect Predictor. Genome Biol. 2016;17:122. PMID:27268795.",
    "variant_recoder": "Ensembl Variant Recoder, Ensembl REST API. https://rest.ensembl.org"
  },
  "resources": ["vep://capabilities", "vep://usage", "vep://reference", "vep://citations", "vep://research-use"],
  "notes": ["..."],
  "_meta": {"...": "..."}
}
```

**Error codes.** None (never fails).

---

## `resolve_variant`

**Purpose.** Normalize any supported input to a canonical `CHR-POS-REF-ALT` plus
`gene_symbol` and `most_severe_consequence`. Coordinates are normalized locally;
rsID/HGVS/SPDI are recoded via Ensembl, then a single VEP region POST fills the
gene/consequence summary. Cheap (sub-kilobyte). Then call `annotate_variant` for
the full annotation.

**v0.2 contract.** Returns `{query, assembly, variants[], warnings[]}`. A
multi-allelic input (e.g. `rs6025`, which maps to both C>A and C>T) expands to
**one entry per ALT allele** — deterministically sorted — instead of a silent
single pick, and carries a `multiple_alts` warning. Single-alt inputs yield a
`variants` of length 1 and `warnings: []`.

**Arguments.**

| Argument | Type | Default | Notes |
|----------|------|---------|-------|
| `variant` | `str` (1–200 chars) | required | Coordinate, rsID, HGVS, or SPDI. Examples: `rs6025`, `1-169549811-C-A`, `NM_000059.3:c.274G>A`. |
| `assembly` | `"GRCh38" \| "GRCh37"` | `"GRCh38"` | Reference build. |
| `allele` | `str \| None` | `None` | Optional ALT filter for a multi-allelic input: an ALT base (e.g. `"A"`) or a full `CHR-POS-REF-ALT`. |

**Example call.**

```json
{"name": "resolve_variant", "arguments": {"variant": "rs6025", "assembly": "GRCh38"}}
```

**Example success payload** (multi-allelic → two ALTs + a warning):

```json
{
  "query": "rs6025",
  "assembly": "GRCh38",
  "variants": [
    {"variant_id": "1-169549811-C-A", "assembly": "GRCh38", "gene_symbol": "F5", "most_severe_consequence": "missense_variant"},
    {"variant_id": "1-169549811-C-T", "assembly": "GRCh38", "gene_symbol": "F5", "most_severe_consequence": "missense_variant"}
  ],
  "warnings": [
    {"code": "multiple_alts", "message": "Input maps to 2 ALT alleles; all are returned in variants[].",
     "context": {"count": 2, "variants": ["1-169549811-C-A", "1-169549811-C-T"]}}
  ],
  "_meta": {
    "tool": "resolve_variant",
    "request_id": "9f0e1d2c3b4a",
    "timing": {"elapsed_ms": 21},
    "capabilities_version": "<hash>",
    "unsafe_for_clinical_use": true,
    "next_commands": [
      {"tool": "annotate_variant", "arguments": {"variant": "1-169549811-C-A", "assembly": "GRCh38"}}
    ],
    "assembly": "GRCh38"
  }
}
```

**Error codes.** `invalid_input`, `not_found`, `rate_limited`,
`upstream_unavailable`, `internal` (with additive `error_subcode` where
finer, e.g. `unsupported_input`, `upstream_timeout`).

---

## `recode_variant`

**Purpose.** Translate a variant (or a batch, cap 200) between identifier systems
via the Ensembl Variant Recoder: rsID ↔ HGVS (g./c./p./t.) ↔ VCF string ↔ SPDI.
No full VEP annotation. One result object per input.

**Arguments.**

| Argument | Type | Default | Notes |
|----------|------|---------|-------|
| `variants` | `list[str]` (1–200) | required | One or more rsID/HGVS/coordinate/SPDI inputs. Cap 200. |
| `assembly` | `"GRCh38" \| "GRCh37"` | `"GRCh38"` | Reference build. |
| `fields` | `str \| None` | `None` | Optional comma-separated Recoder field filter (e.g. `"hgvsg,spdi,vcf_string"`); omit for the default set. |

**Example call.**

```json
{"name": "recode_variant", "arguments": {"variants": ["rs6025"], "assembly": "GRCh38"}}
```

**Example success payload.** Each result aggregates the per-allele HGVS/SPDI/VCF
arrays into flat, de-duplicated lists:

```json
{
  "assembly": "GRCh38",
  "results": [
    {
      "input": "rs6025",
      "id": "rs6025",
      "vcf_string": ["1-169549811-T-C"],
      "hgvsg": ["NC_000001.11:g.169549811T>C"],
      "hgvsc": ["NM_000130.5:c.1601G>A"],
      "hgvsp": ["NP_000121.2:p.Arg534Gln"],
      "spdi": ["NC_000001.11:169549810:T:C"]
    }
  ],
  "_meta": {"tool": "recode_variant", "request_id": "...", "assembly": "GRCh38", "...": "..."}
}
```

**Error codes.** `invalid_input`, `not_found`, `rate_limited`,
`upstream_unavailable`, `internal` (with additive `error_subcode` where
finer, e.g. `unsupported_input`, `upstream_timeout`).

---

## `annotate_variant`

**Purpose.** Full VEP annotation for one variant: consequences, gene/transcript
impact, HGVS, MANE/canonical flags, the precomputed pathogenicity / conservation
predictors (SIFT, PolyPhen, **CADD**, **REVEL**, **AlphaMissense**, **GERP**), and
gnomAD frequencies. The input is parsed, recoded if needed, sent to the VEP region
endpoint, then shaped to `response_mode`. Carries a `provenance` block.

**v0.2 contract.** Returns `{query, assembly, variants[], warnings[], provenance,
_meta}`. Each element of `variants[]` is one ALT allele shaped to `response_mode`
(so the per-tier fields below live inside `variants[i]`, not at the top level). A
multi-allelic input expands to several entries + a `multiple_alts` warning; pass
`allele` to annotate just one ALT. When the `standard` view is truncated, each
variant carries its own `transcripts_summary` `{shown, total}` in-row.

**Arguments.**

| Argument | Type | Default | Notes |
|----------|------|---------|-------|
| `variant` | `str` (1–200 chars) | required | Coordinate, rsID, HGVS, SPDI, or CNV. |
| `assembly` | `"GRCh38" \| "GRCh37"` | `"GRCh38"` | Reference build. |
| `response_mode` | `"minimal" \| "compact" \| "standard" \| "full"` | `"compact"` | Verbosity tier (see below); applied to each variant. |
| `transcripts` | `"auto" \| "all"` | `"auto"` | `standard` tier only. `auto` drops uninformative MODIFIER neighbour transcripts and caps to the most severe; `all` returns every isoform. Each variant carries `transcripts_summary` `{shown, total}` when truncated. |
| `allele` | `str \| None` | `None` | Optional ALT filter for a multi-allelic input: an ALT base (e.g. `"A"`) or a full `CHR-POS-REF-ALT`. |
| `vep_options` | `dict[str, str] \| None` | `None` | VEP flag overrides; keys must be in the allowlist. Disallowed keys → `invalid_input`. |

### `vep_options` allowlist

Caller-supplied flags are validated against this allowlist (values `"1"` enable a
flag per Ensembl REST convention). Disallowed keys raise `invalid_input` listing
the bad keys.

```
# transcript / identifier annotation
hgvs, hgvsg, mane, mane_select, numbers, canonical, domains, merged, refseq,
protein, uniprot, ccds, tsl, appris, biotype, symbol, xref_refseq,
transcript_version, variant_class, var_synonyms, mirna, gene_phenotype,
regulatory, shift_3prime, pick, pick_allele, per_gene, flag_pick, minimal,
vcf_string
# precomputed predictor scores served by the public REST
CADD, REVEL, AlphaMissense, Conservation, Blosum62, EVE, dbscSNV, MaxEntScan,
GeneSplicer, Phenotypes
# instance-dependent plugins (not run by the public REST)
SpliceAI, dbNSFP, LoF
```

The default profile applied to every call is
`CADD=1, REVEL=1, AlphaMissense=1, Conservation=1, hgvs=1, mane=1, numbers=1, canonical=1, domains=1`.

### Pathogenicity & conservation scores

The public Ensembl REST serves several precomputed predictors as dedicated
toggles, and vep-link enables the headline ones **by default** so a plain
`annotate_variant` call already carries them on the relevant transcript (they
populate only for applicable variants, e.g. missense/coding):

| Field(s) | Toggle | Location | Meaning |
|----------|--------|----------|---------|
| `cadd_phred`, `cadd_raw` | `CADD` | `position_scores` | CADD deleteriousness (PHRED-scaled + raw). |
| `conservation` | `Conservation` | `position_scores` | GERP conservation score. |
| `revel` | `REVEL` | per transcript | REVEL missense pathogenicity ensemble score (0–1). |
| `am_pathogenicity`, `am_class` | `AlphaMissense` | per transcript | AlphaMissense score (0–1) + class (`benign`/`pathogenic`/`ambiguous`). |
| `sift_score`, `sift_prediction` | (default) | per transcript | SIFT. |
| `polyphen_score`, `polyphen_prediction` | (default) | per transcript | PolyPhen. |

**Position vs. substitution scores.** CADD and GERP (`conservation`) are
genomic-position values — identical across a variant's transcripts — so they are
hoisted **once** to a variant-level `position_scores` object instead of being
repeated on every transcript row (a large token saving on multi-transcript
variants). `revel`, `am_pathogenicity`, and `am_class` are substitution-specific
and stay per transcript. `position_scores` is present in `compact`/`standard`
(when non-empty); `cadd_raw` and the `*_score` predictor values also surface in
`full`. Other allowlisted toggles (e.g. `EVE`, `dbscSNV`, `MaxEntScan`) can be
requested via `vep_options` and are returned under their native VEP keys in
`full`.

> **Instance-dependent plugins.** `SpliceAI`, `dbNSFP`, and `LoF` are allowlisted
> (so they can be sent to a VEP instance configured with them) but are **not run
> by the public Ensembl REST API**. The scores commonly pulled *from* dbNSFP
> (REVEL, CADD, SIFT, PolyPhen, AlphaMissense) are available via the dedicated
> toggles above. Requesting an instance-dependent plugin returns the annotation
> plus a `note` field rather than silently dropping the flag:
>
> ```json
> "note": "VEP plugin(s) ['SpliceAI'] are instance-dependent and are not run by the public Ensembl REST API; results for these fields are only populated against a VEP instance configured with the plugin."
> ```

### response_mode tiers

| Mode | Fields |
|------|--------|
| `minimal` | `variant_id`, `assembly`, `most_severe_consequence`, `gene_symbol` (+ `provenance`, `_meta`). |
| `compact` (default) | minimal + position (`seq_region_name`, `start`, `end`, `allele_string`) + variant-level `position_scores` (CADD/GERP, when present) + a single prioritized `representative_transcript` (null fields dropped) + gnomAD `frequencies`. |
| `standard` | identity/position + `position_scores` + `transcript_consequences` (each projected and null-stripped) + `frequencies`. By default (`transcripts="auto"`) uninformative MODIFIER neighbour transcripts are dropped and the list is capped to the most severe `max_transcripts`; `_meta.transcripts` then reports `{shown, total}`. Pass `transcripts="all"` for every isoform. |
| `full` | the entire normalized annotation, including all transcripts, `position_scores`, `colocated_variants`, and `strand`. |

Transcript prioritization (for `representative_transcript`): `pick == 1` > MANE >
`canonical == 1` > first. A transcript is **uninformative** (and dropped from the
`standard` auto view) when its impact is `MODIFIER` and it carries no
substitution signal (no HGVS, SIFT/PolyPhen, REVEL, or AlphaMissense) — e.g. an
`upstream_gene_variant` on a flanking gene.

**Example call.**

```json
{"name": "annotate_variant", "arguments": {"variant": "1-169549811-T-C", "assembly": "GRCh38", "response_mode": "compact"}}
```

### Example output per tier (`1-169549811-T-C`, F5 Leiden)

> **v0.2 envelope.** Each block below is the projection of a single ALT — it is
> the shape of one element of `variants[]`. The full response wraps these:
> `{"query": "...", "assembly": "GRCh38", "variants": [ <block> ], "warnings": [],
> "provenance": {...}, "_meta": {...}}`. The `provenance`/`_meta` shown inside the
> blocks are top-level on the real response (one per call, not per variant).

**`minimal`**

```json
{
  "variant_id": "1-169549811-T-C",
  "assembly": "GRCh38",
  "most_severe_consequence": "missense_variant",
  "gene_symbol": "F5",
  "provenance": {"data_source": "Ensembl VEP / Variant Recoder REST", "assembly": "GRCh38",
    "endpoint": "https://rest.ensembl.org/vep/homo_sapiens/region", "retrieved": "2026-06-17T12:00:00+00:00",
    "recommended_citation": "McLaren W, et al. The Ensembl Variant Effect Predictor. Genome Biol. 2016;17:122. PMID:27268795."},
  "_meta": {"tool": "annotate_variant", "request_id": "...", "assembly": "GRCh38", "...": "..."}
}
```

**`compact`** (default)

```json
{
  "variant_id": "1-169549811-T-C",
  "assembly": "GRCh38",
  "most_severe_consequence": "missense_variant",
  "gene_symbol": "F5",
  "seq_region_name": "1",
  "start": 169549811,
  "end": 169549811,
  "allele_string": "T/C",
  "position_scores": {"cadd_phred": 23.1, "cadd_raw": 4.02, "conservation": 5.1},
  "representative_transcript": {
    "gene_symbol": "F5",
    "transcript_id": "ENST00000367797",
    "consequence_terms": ["missense_variant"],
    "impact": "MODERATE",
    "hgvsc": "ENST00000367797.4:c.1601G>A",
    "hgvsp": "ENSP00000356790.4:p.Arg534Gln",
    "protein_position": "534",
    "sift_prediction": "deleterious",
    "polyphen_prediction": "probably_damaging",
    "revel": 0.81,
    "am_pathogenicity": 0.74,
    "am_class": "pathogenic"
  },
  "frequencies": [{"allele": "C", "gnomade": 0.012, "gnomadg": 0.014}],
  "provenance": {"...": "..."},
  "_meta": {"...": "..."}
}
```

CADD/GERP appear once under `position_scores`; the `representative_transcript`
keeps only the substitution-specific predictors (REVEL, AlphaMissense) and drops
any null keys.

**`standard`** — same identity/position fields plus `position_scores`, but
`representative_transcript` is replaced by a `transcript_consequences` array (each
projected and null-stripped). By default the noisy MODIFIER neighbour transcripts
are filtered out and the list is capped, with `_meta.transcripts` reporting how
many of the total are shown. Pass `transcripts="all"` to get every isoform.

```json
{
  "variant_id": "1-169549811-T-C",
  "assembly": "GRCh38",
  "most_severe_consequence": "missense_variant",
  "gene_symbol": "F5",
  "seq_region_name": "1", "start": 169549811, "end": 169549811, "allele_string": "T/C",
  "position_scores": {"cadd_phred": 23.1, "cadd_raw": 4.02, "conservation": 5.1},
  "transcript_consequences": [
    {"gene_symbol": "F5", "transcript_id": "ENST00000367797", "consequence_terms": ["missense_variant"], "impact": "MODERATE", "hgvsc": "...", "hgvsp": "...", "protein_position": "534", "sift_prediction": "deleterious", "polyphen_prediction": "probably_damaging", "revel": 0.81, "am_pathogenicity": 0.74, "am_class": "pathogenic"}
  ],
  "frequencies": [{"allele": "C", "gnomade": 0.012, "gnomadg": 0.014}],
  "provenance": {"...": "..."},
  "_meta": {"tool": "annotate_variant", "transcripts": {"shown": 1, "total": 9},
    "next_commands": [{"tool": "annotate_variant", "arguments": {"variant": "1-169549811-T-C", "assembly": "GRCh38", "response_mode": "standard", "transcripts": "all"}}, "..."], "...": "..."}
}
```

**`full`** — the entire normalized annotation, including `strand`,
`position_scores`, the full `transcript_consequences` (with `gene_id`, `biotype`,
`amino_acids`, `codons`, `sift_score`, `polyphen_score`, `revel`,
`am_pathogenicity`, `am_class`, etc. — note CADD/GERP live in `position_scores`,
not on the rows), and the raw `colocated_variants` array.

```json
{
  "variant_id": "1-169549811-T-C", "assembly": "GRCh38", "input": "1 169549811 . T C . . .",
  "seq_region_name": "1", "start": 169549811, "end": 169549811, "allele_string": "T/C", "strand": 1,
  "most_severe_consequence": "missense_variant", "gene_symbol": "F5",
  "position_scores": {"cadd_phred": 23.1, "cadd_raw": 4.02, "conservation": 5.1},
  "transcript_consequences": [{"gene_id": "ENSG00000198734", "gene_symbol": "F5", "transcript_id": "ENST00000367797", "biotype": "protein_coding", "consequence_terms": ["missense_variant"], "impact": "MODERATE", "canonical": 1, "mane": ["MANE_Select"], "hgvsc": "...", "hgvsp": "...", "amino_acids": "R/Q", "codons": "cGg/cAg", "sift_score": 0.01, "sift_prediction": "deleterious", "polyphen_score": 0.98, "polyphen_prediction": "probably_damaging", "revel": 0.81, "am_pathogenicity": 0.74, "am_class": "pathogenic", "protein_position": "534"}],
  "frequencies": [{"allele": "C", "gnomade": 0.012, "gnomadg": 0.014}],
  "colocated_variants": [{"id": "rs6025", "frequencies": {"C": {"gnomade": 0.012, "gnomadg": 0.014}}}],
  "provenance": {"...": "..."},
  "_meta": {"...": "..."}
}
```

**Error codes.** `invalid_input` (bad input or disallowed `vep_options`; also
covers unsupported contig and assembly/coordinate mismatch), `not_found`,
`rate_limited`, `upstream_unavailable`, `internal`. Finer detail rides in the
additive `error_subcode` (`unsupported_input`, `build_mismatch`,
`upstream_timeout`, `output_validation_failed`).

---

## `annotate_variants_batch`

**Purpose.** Annotate up to 200 variants in one call instead of looping
`annotate_variant`. Internally chunked (200/POST) with an inter-chunk delay and a
concurrency cap; identical canonical variants are de-duplicated into a single VEP
request and fanned back out. One bad variant never fails the batch — its error is
collected per-input.

**Arguments.**

| Argument | Type | Default | Notes |
|----------|------|---------|-------|
| `variants` | `list[str]` (1–200) | required | Up to 200 variants. >200 → `invalid_input`. |
| `assembly` | `"GRCh38" \| "GRCh37"` | `"GRCh38"` | Reference build. |
| `response_mode` | `"minimal" \| "compact" \| "standard" \| "full"` | `"compact"` | Applied to every result. |
| `transcripts` | `"auto" \| "all"` | `"auto"` | `standard` tier only; applied to every result. Each truncated result carries its own `transcripts_summary` (`{shown, total}`) in its body. |
| `vep_options` | `dict[str, str] \| None` | `None` | Same allowlist as `annotate_variant`. |

**Example call.**

```json
{"name": "annotate_variants_batch", "arguments": {"variants": ["1-169549811-T-C", "rs1799963"], "assembly": "GRCh38", "response_mode": "compact"}}
```

**Example success payload.** Each result is shaped to `response_mode` and tagged
with its original `input`; failures are listed separately; `summary` counts them:

```json
{
  "assembly": "GRCh38",
  "results": [
    {"input": "1-169549811-T-C", "variant_id": "1-169549811-T-C", "assembly": "GRCh38", "most_severe_consequence": "missense_variant", "gene_symbol": "F5", "seq_region_name": "1", "start": 169549811, "end": 169549811, "allele_string": "T/C", "representative_transcript": {"...": "..."}, "frequencies": [{"...": "..."}]}
  ],
  "errors": [
    {"input": "not-a-variant", "error_code": "invalid_input", "message": "..."}
  ],
  "summary": {"requested": 2, "annotated": 1, "failed": 1},
  "_meta": {"tool": "annotate_variants_batch", "request_id": "...", "assembly": "GRCh38", "...": "..."}
}
```

Per-input `error_code` values inside `errors[]` are limited to `invalid_input`
(parse failure) and `not_found` (no genomic coordinate / no VEP record). A
batch-level failure (e.g. `>200` variants, upstream rate limit) returns the
top-level error envelope instead.

**Error codes (batch-level).** `invalid_input` (>200 variants or disallowed
`vep_options`), `rate_limited`, `upstream_unavailable`, `internal` (with
additive `error_subcode` where finer, e.g. `upstream_timeout`).

---

## `liftover_variant`

**Purpose.** Map a genomic coordinate (`CHR-POS-REF-ALT`) from one human assembly
to the other (GRCh37 ↔ GRCh38) via the Ensembl assembly-map endpoint. The two
assemblies must differ. HGVS/rsID inputs are unsupported — resolve them first.

**v0.2 REF validation.** The assembly-map endpoint is coordinate-only, so the
input REF/ALT are carried through unchanged — which can be wrong if the reference
base differs between builds. The lifted REF is now validated against the target
assembly (via `/sequence/region`): on a **match** the full `CHR-POS-REF-ALT` is
returned; on a **mismatch** the `lifted` value is coordinate-only (`CHR-POS`) and
a `ref_not_validated` warning names the expected vs carried base. Every response
carries a top-level `warnings[]` (`[]` on a clean lift). Gated by
`VEP_LINK_LIFTOVER_VALIDATE_REF` (default `true`).

**Arguments.**

| Argument | Type | Default | Notes |
|----------|------|---------|-------|
| `variant` | `str` (1–200 chars) | required | A genomic coordinate `CHR-POS-REF-ALT`. |
| `from_assembly` | `"GRCh38" \| "GRCh37"` | required | Source assembly. |
| `to_assembly` | `"GRCh38" \| "GRCh37"` | required | Target assembly (must differ from `from_assembly`). |

**Example call.**

```json
{"name": "liftover_variant", "arguments": {"variant": "1-169519049-T-C", "from_assembly": "GRCh37", "to_assembly": "GRCh38"}}
```

**Example success payload.**

```json
{
  "input": "1-169519049-T-C",
  "from_assembly": "GRCh37",
  "to_assembly": "GRCh38",
  "lifted": "1-169549811-T-C",
  "mapped_region": "1:169549811",
  "warnings": [],
  "_meta": {"tool": "liftover_variant", "request_id": "...", "assembly": "GRCh38", "...": "..."}
}
```

On a REF mismatch the alleles are dropped and a warning is added:

```json
{
  "input": "1-169549811-C-A", "from_assembly": "GRCh38", "to_assembly": "GRCh37",
  "lifted": "1-169519049", "mapped_region": "1:169519049",
  "warnings": [{"code": "ref_not_validated",
    "message": "Lifted coordinate's reference base does not match the target assembly; alleles omitted. Re-resolve in the target assembly for a usable variant.",
    "context": {"expected_ref": "T", "carried_ref": "C"}}]
}
```

**Behavior.** Same `from_assembly`/`to_assembly` → `invalid_input`. HGVS/rsID →
`invalid_input` (subcode `unsupported_input`). Zero mappings → `not_found`. More
than one mapping → `ambiguous_query`.

**Error codes.** `invalid_input`, `not_found`, `ambiguous_query`,
`rate_limited`, `upstream_unavailable`, `internal` (finer detail in the additive
`error_subcode`, e.g. `unsupported_input`, `upstream_timeout`).

---

## `check_upstream_health`

**Purpose.** Run a fresh `/info/ping` probe against both Ensembl REST hosts and
return their circuit-breaker snapshots. Use it before a large batch or when
variant calls begin failing. Unlike the passive health hints attached to normal
tool activity, this diagnostic intentionally performs two live upstream probes.

**Arguments.** None.

**Example call.**

```json
{"name": "check_upstream_health", "arguments": {}}
```

**Example success payload.** A monitored transport returns an `upstream` object
keyed by assembly. Each host entry reports `status` (`ok`, `degraded`,
`recovering`, or `down`), raw circuit state, whether the next request would be
accepted, probe reachability and timing, and a bounded last-error class name.

```json
{
  "upstream": {
    "GRCh38": {
      "status": "ok",
      "circuit": "closed",
      "accepting": true,
      "reachable": true,
      "checked_at": "2026-06-17T12:00:00+00:00",
      "latency_ms": 42,
      "last_error": null
    },
    "GRCh37": {
      "status": "ok",
      "circuit": "closed",
      "accepting": true,
      "reachable": true,
      "checked_at": "2026-06-17T12:00:00+00:00",
      "latency_ms": 38,
      "last_error": null
    }
  },
  "_meta": {"tool": "check_upstream_health", "request_id": "...", "...": "..."}
}
```

Probe failures are represented in the affected host entry rather than hiding
the other host's state. A transport without health monitoring returns an empty
`upstream` object plus a note. Unexpected tool-boundary faults use the standard
`internal` error envelope.

---

## Resources

The capabilities document advertises five MCP resource URIs:
`vep://capabilities`, `vep://usage`, `vep://reference`, `vep://citations`,
`vep://research-use`. They name the discovery/citation/research-use surfaces the
server publishes; the same content is reachable through the `get_capabilities`
tool.
