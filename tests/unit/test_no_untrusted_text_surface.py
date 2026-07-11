"""Guard: vep-link exposes no externally sourced free-text field (v1.1 no-untrusted-text).

Classification (see genefoundry-router
``docs/conformance/untrusted-text-inventory.yml``, backend ``vep``):
vep-link's MCP tools return only SO-term consequence enums, HGVS/SPDI
notation, curated identifiers (gene/transcript ids, ClinVar/COSMIC/OMIM
accessions), and numeric predictor scores -- never upstream free-text prose
(no disease/trait *names*, no descriptions, no abstracts). This is a
defense-in-depth regression guard, not a fence: it must fail loudly if a
future change introduces an unclassified free-text surface.

Two things are checked:

1. Every response model (``vep_link.models.responses``) has a field set
   disjoint from ``FORBIDDEN_FREETEXT_KEYS`` and declares
   ``model_config = ConfigDict(extra="ignore")``, so an upstream prose field
   Ensembl might add tomorrow is dropped at the boundary rather than passed
   through unmodeled.
2. The one surface that bypasses those models -- ``response_mode="full"``,
   which returns ``colocated_variants`` RAW from Ensembl (see
   ``vep_link.mcp.shaping._full`` and
   ``vep_link.services.extraction.build_annotation``) -- is pinned to the key
   set actually observed on canned + live-verified Ensembl VEP payloads (id,
   accessions, ``clin_sig`` enum array, PMIDs, boolean flags). This was
   empirically re-verified live against ``rest.ensembl.org`` for rs6025,
   BRCA1 185delAG (NM_007294.4:c.68_69delAG), and rs1801133 with
   ``Phenotypes=1`` (the one caller-allowlisted VEP option most likely to add
   prose): every colocated_variants entry field is a controlled-vocabulary
   enum, a numeric id/PMID, or a nested dict of curated cross-reference
   accessions (ClinVar RCV/VCV, OMIM, PharmGKB, UniProt, COSMIC) -- never a
   disease/trait name or other prose string.
"""

from __future__ import annotations

from tests.fixtures import VEP_REGION_MISSENSE
from vep_link.models.responses import (
    GnomadFrequency,
    LiftoverResult,
    RecodingResult,
    TranscriptConsequence,
    VariantAnnotation,
)
from vep_link.services.extraction import build_annotation

# Curated nomenclature only: SO-term enums, HGVS/SPDI, IDs, numeric scores --
# no upstream prose surface is permitted anywhere in an MCP tool output.
FORBIDDEN_FREETEXT_KEYS = {
    "definition",
    "description",
    "summary",
    "abstract",
    "notes",
    "comment",
    "involvement",
    "match",
    "phenotypes",
    "evidence",
    "criterion_description",
}

_RESPONSE_MODELS = (
    TranscriptConsequence,
    GnomadFrequency,
    VariantAnnotation,
    RecodingResult,
    LiftoverResult,
)


def test_response_models_have_no_free_text_surface() -> None:
    for model in _RESPONSE_MODELS:
        props = set(model.model_fields)
        assert props.isdisjoint(FORBIDDEN_FREETEXT_KEYS), (
            f"{model.__name__} introduced an unclassified free-text field: "
            f"{props & FORBIDDEN_FREETEXT_KEYS}"
        )


def test_response_models_ignore_unmodeled_upstream_fields() -> None:
    """``extra=\"ignore\"`` on every model, so an upstream field Ensembl adds
    tomorrow (e.g. a hypothetical colocated_variants ``phenotype`` name) is
    dropped at the boundary, never silently passed through unmodeled."""
    for model in _RESPONSE_MODELS:
        assert model.model_config.get("extra") == "ignore", model.__name__


def test_transcript_consequence_fields_are_controlled_vocab_or_numeric_only() -> None:
    """Every ``TranscriptConsequence`` field is a curated identifier, an SO/
    predictor enum, HGVS/SPDI notation, or a numeric score -- never a
    free-text label. Fails loudly if a field is added without classifying it
    here first."""
    identifiers_and_enums = {
        "gene_id",
        "gene_symbol",
        "transcript_id",
        "biotype",
        "consequence_terms",
        "impact",
        "canonical",
        "mane",
        "hgvsc",
        "hgvsp",
        "amino_acids",
        "codons",
        "protein_position",
        "sift_prediction",
        "polyphen_prediction",
        "am_class",
    }
    numeric_scores = {
        "sift_score",
        "polyphen_score",
        "cadd_phred",
        "cadd_raw",
        "revel",
        "am_pathogenicity",
        "conservation",
    }
    allowed = identifiers_and_enums | numeric_scores
    fields = set(TranscriptConsequence.model_fields)
    assert fields == allowed, (
        "vep TranscriptConsequence field set changed -- classify the new field(s) "
        f"as controlled-vocab/HGVS or a numeric score, or fence them if they carry "
        f"upstream prose: {fields ^ allowed}"
    )


def test_full_mode_raw_colocated_variants_passthrough_has_no_free_text_keys() -> None:
    """``response_mode=\"full\"`` returns ``colocated_variants`` unfiltered
    straight from the Ensembl VEP record (it bypasses every pydantic model
    above -- see ``build_annotation`` and ``shape_annotation._full``). Pin the
    key set to what is actually observed on the canned fixture (which mirrors
    live Ensembl payloads, including ``Phenotypes=1`` responses re-verified
    against ``rest.ensembl.org``) so a future upstream field addition that
    smuggles in prose (e.g. a disease/trait name) fails this test instead of
    silently reaching an MCP client."""
    annotation = build_annotation(
        VEP_REGION_MISSENSE[0], variant_id="1-1000-A-T", assembly="GRCh38"
    )
    colocated = annotation["colocated_variants"]
    assert colocated, "fixture must carry at least one colocated_variants entry"
    observed_keys: set[str] = set()
    for entry in colocated:
        observed_keys |= set(entry)
    # Everything ever observed (fixture + live rest.ensembl.org spot checks)
    # is an id/accession, a coordinate, a controlled-vocab enum, a PMID, or a
    # nested dict of curated cross-reference accessions -- never prose.
    known_non_freetext_keys = {
        "id",
        "allele_string",
        "seq_region_name",
        "start",
        "end",
        "strand",
        "frequencies",
        "clin_sig",
        "clin_sig_allele",
        "clin_sig_ref_allele",
        "phenotype_or_disease",
        "somatic",
        "pubmed",
        "var_synonyms",
    }
    assert observed_keys <= known_non_freetext_keys, (
        f"colocated_variants gained an unclassified field: {observed_keys - known_non_freetext_keys}"
    )
    assert observed_keys.isdisjoint(FORBIDDEN_FREETEXT_KEYS)
