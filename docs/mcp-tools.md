# MCP Tool Reference

`vep-link` exposes **six** MCP tools over Streamable HTTP (and stdio). All tools
are read-only, idempotent Ensembl lookups, carry `annotations=READ_ONLY_OPEN_WORLD`,
and return a payload with a `_meta` block. Failures return the structured error
envelope (see [api.md](api.md)) instead of raising.

Canonical workflow: `get_capabilities` (discovery) → `resolve_variant` (any input
→ canonical coordinate) → `recode_variant` (equivalent IDs, optional) →
`annotate_variant` / `annotate_variants_batch` (full VEP) → `liftover_variant`
(cross-build coordinate conversion).

> **Research use only; not for clinical decision support.**

---

## Common envelope

Every success payload carries `_meta` (see [api.md](api.md) for the full field
list):

```json
"_meta": {
  "tool": "annotate_variant",
  "request_id": "a1b2c3d4e5f6",
  "timing": {"elapsed_ms": 0},
  "capabilities_version": "<12-hex-char hash>",
  "unsafe_for_clinical_use": true,
  "next_commands": [],
  "assembly": "GRCh38"
}
```

`annotate_variant` additionally carries a `provenance` block; `resolve_variant`
populates `_meta.next_commands` with a ready-to-call `annotate_variant` follow-up.

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
  "server_version": "0.1.0",
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
    "compact": "representative (prioritized) transcript + key fields (default)",
    "standard": "all transcript consequences, key fields each",
    "full": "raw-ish VEP payload (all fields) + colocated variants/frequencies"
  },
  "tools": [{"name": "get_capabilities", "summary": "...", "token_cost_hint": "low"}, "..."],
  "error_codes": [
    "invalid_input", "unsupported_input", "not_found", "build_mismatch",
    "ambiguous", "rate_limited", "upstream_unavailable", "upstream_timeout",
    "output_validation_failed", "internal_error"
  ],
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

**Arguments.**

| Argument | Type | Default | Notes |
|----------|------|---------|-------|
| `variant` | `str` (1–200 chars) | required | Coordinate, rsID, HGVS, or SPDI. Examples: `rs6025`, `1-169549811-C-A`, `NM_000059.3:c.274G>A`. |
| `assembly` | `"GRCh38" \| "GRCh37"` | `"GRCh38"` | Reference build. |

**Example call.**

```json
{"name": "resolve_variant", "arguments": {"variant": "rs6025", "assembly": "GRCh38"}}
```

**Example success payload.**

```json
{
  "variant_id": "1-169549811-T-C",
  "assembly": "GRCh38",
  "gene_symbol": "F5",
  "most_severe_consequence": "missense_variant",
  "_meta": {
    "tool": "resolve_variant",
    "request_id": "9f0e1d2c3b4a",
    "timing": {"elapsed_ms": 0},
    "capabilities_version": "<hash>",
    "unsafe_for_clinical_use": true,
    "next_commands": [
      {"tool": "annotate_variant", "arguments": {"variant": "rs6025", "assembly": "GRCh38"}}
    ],
    "assembly": "GRCh38"
  }
}
```

**Error codes.** `invalid_input`, `unsupported_input`, `not_found`,
`rate_limited`, `upstream_unavailable`, `upstream_timeout`, `internal_error`.

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

**Error codes.** `invalid_input`, `unsupported_input`, `not_found`,
`rate_limited`, `upstream_unavailable`, `upstream_timeout`, `internal_error`.

---

## `annotate_variant`

**Purpose.** Full VEP annotation for one variant: consequences, gene/transcript
impact, HGVS, MANE/canonical flags, the precomputed pathogenicity / conservation
predictors (SIFT, PolyPhen, **CADD**, **REVEL**, **AlphaMissense**, **GERP**), and
gnomAD frequencies. The input is parsed, recoded if needed, sent to the VEP region
endpoint, then shaped to `response_mode`. Carries a `provenance` block.

**Arguments.**

| Argument | Type | Default | Notes |
|----------|------|---------|-------|
| `variant` | `str` (1–200 chars) | required | Coordinate, rsID, HGVS, SPDI, or CNV. |
| `assembly` | `"GRCh38" \| "GRCh37"` | `"GRCh38"` | Reference build. |
| `response_mode` | `"minimal" \| "compact" \| "standard" \| "full"` | `"compact"` | Verbosity tier (see below). |
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

| Field(s) | Toggle | Meaning |
|----------|--------|---------|
| `cadd_phred`, `cadd_raw` | `CADD` | CADD deleteriousness (PHRED-scaled + raw). |
| `revel` | `REVEL` | REVEL missense pathogenicity ensemble score (0–1). |
| `am_pathogenicity`, `am_class` | `AlphaMissense` | AlphaMissense score (0–1) + class (`benign`/`pathogenic`/`ambiguous`). |
| `conservation` | `Conservation` | GERP conservation score. |
| `sift_score`, `sift_prediction` | (default) | SIFT. |
| `polyphen_score`, `polyphen_prediction` | (default) | PolyPhen. |

`revel`, `am_pathogenicity`, `am_class`, and `conservation` (plus `cadd_phred`)
appear in the `compact` `representative_transcript` and in every `standard`
transcript; `cadd_raw` and the `*_score` predictor values appear in `full`. Other
allowlisted toggles (e.g. `EVE`, `dbscSNV`, `MaxEntScan`) can be requested via
`vep_options` and are returned under their native VEP keys in `full`.

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
| `compact` (default) | minimal + position (`seq_region_name`, `start`, `end`, `allele_string`) + a single prioritized `representative_transcript` + gnomAD `frequencies`. |
| `standard` | identity/position + **all** `transcript_consequences` (each projected to the compact key set) + `frequencies`. |
| `full` | the entire normalized annotation, including all transcripts, `colocated_variants`, and `strand`. |

Transcript prioritization (for `representative_transcript`): `pick == 1` > MANE >
`canonical == 1` > first.

**Example call.**

```json
{"name": "annotate_variant", "arguments": {"variant": "1-169549811-T-C", "assembly": "GRCh38", "response_mode": "compact"}}
```

### Example output per tier (`1-169549811-T-C`, F5 Leiden)

**`minimal`**

```json
{
  "variant_id": "1-169549811-T-C",
  "assembly": "GRCh38",
  "most_severe_consequence": "missense_variant",
  "gene_symbol": "F5",
  "provenance": {"data_source": "Ensembl VEP / Variant Recoder REST", "assembly": "GRCh38",
    "endpoint": "https://rest.ensembl.org/vep/homo_sapiens/region", "retrieved": null,
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
    "cadd_phred": 23.1,
    "revel": 0.81,
    "am_pathogenicity": 0.74,
    "am_class": "pathogenic",
    "conservation": 5.1
  },
  "frequencies": [{"allele": "C", "gnomade": 0.012, "gnomadg": 0.014}],
  "provenance": {"...": "..."},
  "_meta": {"...": "..."}
}
```

**`standard`** — same identity/position fields, but `representative_transcript` is
replaced by a `transcript_consequences` array (every transcript, each projected to
the compact key set) plus `frequencies`.

```json
{
  "variant_id": "1-169549811-T-C",
  "assembly": "GRCh38",
  "most_severe_consequence": "missense_variant",
  "gene_symbol": "F5",
  "seq_region_name": "1", "start": 169549811, "end": 169549811, "allele_string": "T/C",
  "transcript_consequences": [
    {"gene_symbol": "F5", "transcript_id": "ENST00000367797", "consequence_terms": ["missense_variant"], "impact": "MODERATE", "hgvsc": "...", "hgvsp": "...", "protein_position": "534", "sift_prediction": "deleterious", "polyphen_prediction": "probably_damaging", "cadd_phred": 23.1, "revel": 0.81, "am_pathogenicity": 0.74, "am_class": "pathogenic", "conservation": 5.1}
  ],
  "frequencies": [{"allele": "C", "gnomade": 0.012, "gnomadg": 0.014}],
  "provenance": {"...": "..."},
  "_meta": {"...": "..."}
}
```

**`full`** — the entire normalized annotation, including `strand`, the full
`transcript_consequences` (with `gene_id`, `biotype`, `amino_acids`, `codons`,
`sift_score`, `polyphen_score`, `cadd_raw`, `revel`, `am_pathogenicity`,
`am_class`, `conservation`, etc.), and the raw `colocated_variants` array.

```json
{
  "variant_id": "1-169549811-T-C", "assembly": "GRCh38", "input": "1 169549811 . T C . . .",
  "seq_region_name": "1", "start": 169549811, "end": 169549811, "allele_string": "T/C", "strand": 1,
  "most_severe_consequence": "missense_variant", "gene_symbol": "F5",
  "transcript_consequences": [{"gene_id": "ENSG00000198734", "gene_symbol": "F5", "transcript_id": "ENST00000367797", "biotype": "protein_coding", "consequence_terms": ["missense_variant"], "impact": "MODERATE", "canonical": 1, "mane": ["MANE_Select"], "hgvsc": "...", "hgvsp": "...", "amino_acids": "R/Q", "codons": "cGg/cAg", "sift_score": 0.01, "sift_prediction": "deleterious", "polyphen_score": 0.98, "polyphen_prediction": "probably_damaging", "cadd_phred": 23.1, "cadd_raw": 4.02, "revel": 0.81, "am_pathogenicity": 0.74, "am_class": "pathogenic", "conservation": 5.1, "protein_position": "534"}],
  "frequencies": [{"allele": "C", "gnomade": 0.012, "gnomadg": 0.014}],
  "colocated_variants": [{"id": "rs6025", "frequencies": {"C": {"gnomade": 0.012, "gnomadg": 0.014}}}],
  "provenance": {"...": "..."},
  "_meta": {"...": "..."}
}
```

**Error codes.** `invalid_input` (bad input or disallowed `vep_options`),
`unsupported_input`, `not_found`, `build_mismatch`, `rate_limited`,
`upstream_unavailable`, `upstream_timeout`, `output_validation_failed`,
`internal_error`.

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
`vep_options`), `rate_limited`, `upstream_unavailable`, `upstream_timeout`,
`internal_error`.

---

## `liftover_variant`

**Purpose.** Map a genomic coordinate (`CHR-POS-REF-ALT`) from one human assembly
to the other (GRCh37 ↔ GRCh38) via the Ensembl assembly-map endpoint. The two
assemblies must differ. HGVS/rsID inputs are unsupported — resolve them first.

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
  "_meta": {"tool": "liftover_variant", "request_id": "...", "assembly": "GRCh38", "...": "..."}
}
```

**Behavior.** Same `from_assembly`/`to_assembly` → `invalid_input`. HGVS/rsID →
`unsupported_input`. Zero mappings → `not_found`. More than one mapping →
`ambiguous`.

**Error codes.** `invalid_input`, `unsupported_input`, `not_found`, `ambiguous`,
`rate_limited`, `upstream_unavailable`, `upstream_timeout`, `internal_error`.

---

## Resources

The capabilities document advertises five MCP resource URIs:
`vep://capabilities`, `vep://usage`, `vep://reference`, `vep://citations`,
`vep://research-use`. They name the discovery/citation/research-use surfaces the
server publishes; the same content is reachable through the `get_capabilities`
tool.
