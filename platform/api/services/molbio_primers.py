"""Primer Tm calculation, primer design schemas, and primer pair search."""
from __future__ import annotations

from typing import Any, List, Literal, Optional

from Bio.SeqUtils import MeltingTemp as mt, gc_fraction
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.molbio_ops import reverse_complement
from services.nucleotide_validation import canonicalize_nucleotide_sequence
from services.primer_qc import (
    PRIMER_QC_PROFILE,
    PrimerQcProfile,
    _evaluate_primer_qc_canonical,
    evaluate_primer_pair_qc,
)


class SequenceInput(BaseModel):
    sequence_id: Optional[str] = None
    name: Optional[str] = None
    sequence: Optional[str] = None
    sequence_type: Optional[str] = None
    is_circular: bool = False


class PrimerTmSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["primer-tm-settings-v1"] = "primer-tm-settings-v1"
    algorithm: str = "nn_santalucia_hicks_2004"
    salt_correction: str = "owczarzy_2008"
    primer_concentration_nM: float = Field(default=250.0, gt=0, allow_inf_nan=False)
    template_concentration_nM: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    na_mM: float = Field(default=50.0, ge=0, allow_inf_nan=False)
    k_mM: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    tris_mM: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    mg_mM: float = Field(default=1.5, ge=0, allow_inf_nan=False)
    dntps_mM: float = Field(default=0.6, ge=0, allow_inf_nan=False)
    dmso_percent: float = Field(default=0.0, ge=0, le=100, allow_inf_nan=False)
    formamide_percent: float = Field(default=0.0, ge=0, le=100, allow_inf_nan=False)
    self_complementary: bool = False


TM_ALGORITHM_DEFS = {
    "wallace": {
        "label": "Wallace rule",
        "description": "Rule-of-thumb 2/4 formula for short oligos.",
        "kind": "wallace",
        "sequence_types": ["dna", "rna"],
    },
    "gc_empirical": {
        "label": "GC empirical",
        "description": "DNA GC formula; additive salt corrections only. Oligo concentration is not used.",
        "kind": "gc",
        "sequence_types": ["dna"],
        "gc_valueset": 7,
    },
    "nn_breslauer_1986": {
        "label": "Nearest-neighbor: Breslauer 1986",
        "description": "Legacy DNA/DNA nearest-neighbor thermodynamic table.",
        "kind": "nn",
        "sequence_types": ["dna"],
        "nn_table_name": "DNA_NN1",
        "polymer_pairing": "dna_dna",
    },
    "nn_sugimoto_1996": {
        "label": "Nearest-neighbor: Sugimoto 1996",
        "description": "DNA/DNA nearest-neighbor parameters from Sugimoto et al.",
        "kind": "nn",
        "sequence_types": ["dna"],
        "nn_table_name": "DNA_NN2",
        "polymer_pairing": "dna_dna",
    },
    "nn_allawi_santalucia_1997": {
        "label": "Nearest-neighbor: Allawi & SantaLucia 1997",
        "description": "DNA/DNA nearest-neighbor table used as Biopython's default NN parameter set.",
        "kind": "nn",
        "sequence_types": ["dna"],
        "nn_table_name": "DNA_NN3",
        "polymer_pairing": "dna_dna",
    },
    "nn_santalucia_hicks_2004": {
        "label": "Nearest-neighbor: SantaLucia & Hicks 2004",
        "description": "Modern DNA/DNA nearest-neighbor parameter refinement.",
        "kind": "nn",
        "sequence_types": ["dna"],
        "nn_table_name": "DNA_NN4",
        "polymer_pairing": "dna_dna",
    },
    "rna_nn_freier_1986": {
        "label": "RNA/RNA NN: Freier 1986",
        "description": "Legacy RNA/RNA nearest-neighbor thermodynamic table.",
        "kind": "nn",
        "sequence_types": ["rna"],
        "nn_table_name": "RNA_NN1",
        "polymer_pairing": "rna_rna",
    },
    "rna_nn_xia_1998": {
        "label": "RNA/RNA NN: Xia 1998",
        "description": "RNA/RNA nearest-neighbor parameters from Xia et al.",
        "kind": "nn",
        "sequence_types": ["rna"],
        "nn_table_name": "RNA_NN2",
        "polymer_pairing": "rna_rna",
    },
    "rna_nn_chen_2012": {
        "label": "RNA/RNA NN: Chen 2012",
        "description": "Modern RNA/RNA nearest-neighbor parameter set.",
        "kind": "nn",
        "sequence_types": ["rna"],
        "nn_table_name": "RNA_NN3",
        "polymer_pairing": "rna_rna",
    },
    "rna_dna_sugimoto_1995": {
        "label": "RNA/DNA hybrid NN: Sugimoto 1995",
        "description": "RNA/DNA hybrid nearest-neighbor table. Sequence must be RNA.",
        "kind": "nn",
        "sequence_types": ["rna"],
        "nn_table_name": "R_DNA_NN1",
        "polymer_pairing": "rna_dna_hybrid",
    },
}

TM_SALT_CORRECTION_DEFS = {
    "none": {
        "label": "None",
        "description": "No salt correction.",
        "method": 0,
    },
    "schildkraut_lifson_1965": {
        "label": "Schildkraut-Lifson 1965",
        "description": "Legacy monovalent salt correction.",
        "method": 1,
    },
    "wetmur_1991": {
        "label": "Wetmur 1991",
        "description": "Monovalent salt correction using the Wetmur formulation.",
        "method": 2,
    },
    "santalucia_1996": {
        "label": "SantaLucia 1996",
        "description": "Monovalent salt correction from SantaLucia et al. 1996.",
        "method": 3,
    },
    "santalucia_1998_tm": {
        "label": "SantaLucia 1998 (Tm)",
        "description": "SantaLucia 1998 salt correction applied directly to Tm.",
        "method": 4,
    },
    "santalucia_1998_entropy": {
        "label": "SantaLucia 1998 (entropy)",
        "description": "SantaLucia 1998 entropy correction. Good general-purpose PCR default.",
        "method": 5,
    },
    "owczarzy_2004": {
        "label": "Owczarzy 2004",
        "description": "GC-aware monovalent salt correction.",
        "method": 6,
    },
    "owczarzy_2008": {
        "label": "Owczarzy 2008",
        "description": "Mg2+/dNTP-aware salt correction for mixed monovalent/divalent PCR conditions.",
        "method": 7,
    },
}


def tm_algorithm_capabilities(algorithm: str) -> dict[str, Any]:
    definition = TM_ALGORITHM_DEFS[algorithm]
    if definition["kind"] == "wallace":
        salts = ["none"]
        scope = "Rough base-count estimate. Concentrations, salt and self-complementarity are not used."
    elif definition["kind"] == "gc":
        salts = [key for key, value in TM_SALT_CORRECTION_DEFS.items() if value["method"] <= 4]
        scope = "DNA empirical estimate. Oligo concentrations and self-complementarity are not used."
    elif definition.get("polymer_pairing", "").startswith("rna"):
        salts = ["none"]
        scope = "RNA reference condition: 1 M Na. Only perfectly paired overlap is supported; RNA/RNA dangling ends use RNA_DE1. No DNA salt extrapolation."
    else:
        salts = list(TM_SALT_CORRECTION_DEFS)
        scope = "Perfect DNA duplex unless an explicit 3′→5′ complementary strand is supplied in Advanced duplex Tm."
    return {"salt_corrections": salts, "applicability": scope}


DEFAULT_TM_SETTINGS_BY_SEQUENCE_TYPE = {
    "dna": {
        "algorithm": "nn_santalucia_hicks_2004",
        "salt_correction": "owczarzy_2008",
        "primer_concentration_nM": 250.0,
        "template_concentration_nM": 0.0,
        "na_mM": 50.0,
        "k_mM": 0.0,
        "tris_mM": 0.0,
        "mg_mM": 1.5,
        "dntps_mM": 0.6,
        "dmso_percent": 0.0,
        "formamide_percent": 0.0,
        "self_complementary": False,
    },
    "rna": {
        "algorithm": "rna_nn_chen_2012",
        "salt_correction": "none",
        "primer_concentration_nM": 250.0,
        "template_concentration_nM": 0.0,
        "na_mM": 1000.0,
        "k_mM": 0.0,
        "tris_mM": 0.0,
        "mg_mM": 0.0,
        "dntps_mM": 0.0,
        "dmso_percent": 0.0,
        "formamide_percent": 0.0,
        "self_complementary": False,
    },
}


class PrimerDesignSearchProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["flanking_exact_v1"] = "flanking_exact_v1"
    candidates_per_strand: Literal[48] = 48
    target_overlap_bases: Literal[4] = 4
    origin_wrapping_candidates: bool = True
    product_size_includes_tails: Literal[True] = True
    overlapping_footprints: Literal[False] = False
    substrate: Literal["dna_template"] = "dna_template"
    qc: PrimerQcProfile = Field(default_factory=PrimerQcProfile)


class PrimerTmInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["primer-tm-input-v1"] = "primer-tm-input-v1"
    id: Optional[str] = None
    name: Optional[str] = None
    sequence: str
    sequence_type: Optional[Literal["dna", "rna"]] = None
    complement_sequence: Optional[str] = Field(default=None, description="Aligned complementary strand written 3′ to 5′; omitted means a perfect complement.")
    shift: int = Field(default=0, description="Complement index aligned to oligo index zero; positive shifts move oligo right.")


class PrimerTmResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["primer-tm-result-v1"] = "primer-tm-result-v1"
    # Historical results did not record thermodynamic provenance. Keep it
    # unknown on read; current calculations always supply the resolved receipt.
    effective_settings: Optional[PrimerTmSettings] = None
    effective_input: Optional[PrimerTmInput] = None
    id: Optional[str] = None
    name: Optional[str] = None
    sequence: str
    sequence_type: str
    length: int
    gc_percent: float
    tm: Optional[float]
    algorithm: str
    algorithm_label: str
    salt_correction: str
    salt_correction_label: str
    polymer_pairing: str
    warnings: List[str] = Field(default_factory=list)


class PrimerTmBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["primer-tm-batch-v1"] = "primer-tm-batch-v1"
    primers: List[PrimerTmInput]
    settings: Optional[PrimerTmSettings] = None


class PrimerTmOption(BaseModel):
    id: str
    label: str
    description: str
    sequence_types: List[str]
    polymer_pairing: Optional[str] = None
    salt_corrections: List[str] = Field(default_factory=list)
    applicability: str = ""


class PrimerTmSaltCorrectionOption(BaseModel):
    id: str
    label: str
    description: str


class PrimerTmOptionsResponse(BaseModel):
    schema_version: Literal["primer-options-v1"] = "primer-options-v1"
    qc_profile: PrimerQcProfile = Field(default_factory=PrimerQcProfile)
    design_profile: PrimerDesignSearchProfile = Field(default_factory=PrimerDesignSearchProfile)
    algorithms: List[PrimerTmOption]
    salt_corrections: List[PrimerTmSaltCorrectionOption]
    defaults: dict[str, PrimerTmSettings]


def normalize_sequence_type(
    sequence_type: Optional[str], sequence: Optional[str]
) -> str:
    normalized = (sequence_type or "").strip().lower()
    if normalized in {"dna", "rna"}:
        return normalized

    sequence_text = (sequence or "").upper()
    if "U" in sequence_text and "T" not in sequence_text:
        return "rna"
    return "dna"


def clean_inline_sequence(sequence: str, sequence_type: str) -> str:
    try:
        return canonicalize_nucleotide_sequence(
            sequence, sequence_type, allow_empty=True
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def clean_primer_sequence(sequence: str) -> str:
    """Normalize primer sequence text."""
    return "".join((sequence or "").upper().split())


def infer_primer_sequence_type(sequence: str) -> str:
    upper = clean_primer_sequence(sequence)
    if "U" in upper and "T" not in upper:
        return "rna"
    return "dna"


def calculate_gc_percent(sequence: str) -> float:
    """Calculate GC content percentage."""
    if not sequence or len(sequence) == 0:
        return 0.0
    return round(gc_fraction(sequence.upper(), ambiguous="weighted") * 100, 1)


def default_tm_settings_for_sequence_type(sequence_type: str) -> PrimerTmSettings:
    defaults = DEFAULT_TM_SETTINGS_BY_SEQUENCE_TYPE.get(
        sequence_type, DEFAULT_TM_SETTINGS_BY_SEQUENCE_TYPE["dna"]
    )
    return PrimerTmSettings(**defaults)


def calculate_primer_tm_result(
    sequence: str,
    sequence_type: Optional[str] = None,
    settings: Optional[PrimerTmSettings] = None,
    complement_sequence: Optional[str] = None,
    shift: int = 0,
) -> PrimerTmResult:
    cleaned = clean_primer_sequence(sequence)
    resolved_sequence_type = sequence_type or infer_primer_sequence_type(cleaned)
    resolved_settings = settings or default_tm_settings_for_sequence_type(
        resolved_sequence_type
    )
    gc_percent = calculate_gc_percent(cleaned)
    receipt = dict(effective_settings=resolved_settings, effective_input=PrimerTmInput(
        sequence=cleaned, sequence_type=resolved_sequence_type,
        complement_sequence=clean_primer_sequence(complement_sequence) if complement_sequence is not None else None, shift=shift))
    warnings: List[str] = []

    if not cleaned:
        return PrimerTmResult(
            **receipt,
            sequence=cleaned,
            sequence_type=resolved_sequence_type,
            length=0,
            gc_percent=0.0,
            tm=None,
            algorithm=resolved_settings.algorithm,
            algorithm_label=resolved_settings.algorithm,
            salt_correction=resolved_settings.salt_correction,
            salt_correction_label=resolved_settings.salt_correction,
            polymer_pairing="unknown",
            warnings=["Primer sequence is empty."],
        )

    if not all(base in "ATCGUMRWSYKVHDBN" for base in cleaned):
        return PrimerTmResult(
            **receipt,
            sequence=cleaned,
            sequence_type=resolved_sequence_type,
            length=len(cleaned),
            gc_percent=gc_percent,
            tm=None,
            algorithm=resolved_settings.algorithm,
            algorithm_label=resolved_settings.algorithm,
            salt_correction=resolved_settings.salt_correction,
            salt_correction_label=resolved_settings.salt_correction,
            polymer_pairing="unknown",
            warnings=["Primer contains invalid nucleotide characters."],
        )

    algorithm_def = TM_ALGORITHM_DEFS.get(resolved_settings.algorithm)
    salt_def = TM_SALT_CORRECTION_DEFS.get(resolved_settings.salt_correction)

    if algorithm_def is None:
        return PrimerTmResult(
            **receipt,
            sequence=cleaned,
            sequence_type=resolved_sequence_type,
            length=len(cleaned),
            gc_percent=gc_percent,
            tm=None,
            algorithm=resolved_settings.algorithm,
            algorithm_label=resolved_settings.algorithm,
            salt_correction=resolved_settings.salt_correction,
            salt_correction_label=resolved_settings.salt_correction,
            polymer_pairing="unknown",
            warnings=[f"Unknown Tm algorithm '{resolved_settings.algorithm}'."],
        )

    if salt_def is None:
        return PrimerTmResult(
            **receipt,
            sequence=cleaned,
            sequence_type=resolved_sequence_type,
            length=len(cleaned),
            gc_percent=gc_percent,
            tm=None,
            algorithm=resolved_settings.algorithm,
            algorithm_label=algorithm_def["label"],
            salt_correction=resolved_settings.salt_correction,
            salt_correction_label=resolved_settings.salt_correction,
            polymer_pairing=algorithm_def.get(
                "polymer_pairing", resolved_sequence_type
            ),
            warnings=[
                f"Unknown salt correction '{resolved_settings.salt_correction}'."
            ],
        )

    if resolved_sequence_type not in algorithm_def["sequence_types"]:
        return PrimerTmResult(
            **receipt,
            sequence=cleaned,
            sequence_type=resolved_sequence_type,
            length=len(cleaned),
            gc_percent=gc_percent,
            tm=None,
            algorithm=resolved_settings.algorithm,
            algorithm_label=algorithm_def["label"],
            salt_correction=resolved_settings.salt_correction,
            salt_correction_label=salt_def["label"],
            polymer_pairing=algorithm_def.get(
                "polymer_pairing", resolved_sequence_type
            ),
            warnings=[
                f"Algorithm '{algorithm_def['label']}' does not support {resolved_sequence_type.upper()} primers."
            ],
        )

    tm_value: Optional[float] = None
    try:
        if resolved_settings.salt_correction not in tm_algorithm_capabilities(resolved_settings.algorithm)["salt_corrections"]:
            raise ValueError("Selected salt correction is not supported by this Tm model; choose a compatible correction.")
        ambiguous = set(cleaned) - set("ACGTU")
        if ambiguous:
            warnings.append("GC is an estimate using equal weighting of IUPAC alternatives.")
            if algorithm_def["kind"] == "nn":
                raise ValueError("Nearest-neighbor Tm is unavailable for ambiguous bases; supply an unambiguous oligo. No bases were removed.")
        rna_model = algorithm_def.get("polymer_pairing", "").startswith("rna")
        if rna_model:
            if (resolved_settings.na_mM != 1000 or resolved_settings.k_mM or resolved_settings.tris_mM
                    or resolved_settings.mg_mM or resolved_settings.dntps_mM
                    or resolved_settings.dmso_percent or resolved_settings.formamide_percent):
                raise ValueError("RNA thermodynamics are supported only at the tabulated 1 M Na reference condition (other ions and additives zero); DNA salt corrections are not applied to RNA.")
            warnings.append("Reference-condition RNA Tm (1 M Na); not a mixed-ion reaction prediction.")
        if algorithm_def["kind"] != "nn":
            if complement_sequence is not None or shift:
                raise ValueError("Complementary-strand and shift inputs require a supported nearest-neighbor model.")
            warnings.append("This approximate model does not use oligo concentrations or self-complementarity.")
        if algorithm_def["kind"] == "wallace":
            tm_value = float(mt.Tm_Wallace(cleaned, strict=True))
        elif algorithm_def["kind"] == "gc":
            tm_value = float(
                mt.Tm_GC(
                    cleaned,
                    strict=True,
                    userset=(81.5, 0.41, 600, 1),
                    Na=resolved_settings.na_mM,
                    K=resolved_settings.k_mM,
                    Tris=resolved_settings.tris_mM,
                    Mg=resolved_settings.mg_mM,
                    dNTPs=resolved_settings.dntps_mM,
                    saltcorr=salt_def["method"],
                )
            )
        else:
            tm_kwargs = {
                "nn_table": getattr(mt, algorithm_def["nn_table_name"]),
                "dnac1": resolved_settings.primer_concentration_nM,
                "dnac2": resolved_settings.template_concentration_nM,
                "selfcomp": resolved_settings.self_complementary,
                "Na": resolved_settings.na_mM,
                "K": resolved_settings.k_mM,
                "Tris": resolved_settings.tris_mM,
                "Mg": resolved_settings.mg_mM,
                "dNTPs": resolved_settings.dntps_mM,
                "saltcorr": salt_def["method"],
                "strict": True,
            }
            if complement_sequence is not None:
                complement = clean_primer_sequence(complement_sequence)
                if not complement or any(base not in "ACGTU" for base in complement):
                    raise ValueError("Complementary strand must contain only unambiguous nucleotide bases; no bases were removed.")
                complement = complement.replace("U", "T")
                if rna_model:
                    overlap = [(base, complement[index + shift]) for index, base in enumerate(cleaned.replace("U", "T"))
                               if 0 <= index + shift < len(complement)]
                    if len(overlap) < 2 or any(base.translate(str.maketrans("ACGT", "TGCA")) != paired for base, paired in overlap):
                        raise ValueError("RNA mismatch thermodynamics are not supported; the overlapping complementary strand must be perfectly paired.")
                    if algorithm_def["polymer_pairing"] == "rna_dna_hybrid" and (shift or len(complement) != len(cleaned)):
                        raise ValueError("RNA/DNA hybrid dangling-end thermodynamics are not supported.")
                tm_kwargs["c_seq"] = complement
                tm_kwargs["shift"] = shift
            elif shift:
                raise ValueError("A shift requires an explicit complementary strand.")
            # Bypass Biopython's destructive ambiguity filtering after validation.
            tm_kwargs["check"] = False
            if rna_model:
                tm_kwargs["de_table"] = mt.RNA_DE1
            tm_value = float(mt.Tm_NN(cleaned.replace("U", "T"), **tm_kwargs))

        if resolved_settings.dmso_percent or resolved_settings.formamide_percent:
            warnings.append("DMSO/formamide corrections are approximate.")
            tm_value = float(
                mt.chem_correction(
                    tm_value,
                    DMSO=resolved_settings.dmso_percent,
                    fmd=resolved_settings.formamide_percent,
                    GC=gc_percent,
                )
            )
    except Exception as exc:
        tm_value = None
        warnings.append(str(exc))

    return PrimerTmResult(
        **receipt,
        sequence=cleaned,
        sequence_type=resolved_sequence_type,
        length=len(cleaned),
        gc_percent=gc_percent,
        tm=round(tm_value, 2) if tm_value is not None else None,
        algorithm=resolved_settings.algorithm,
        algorithm_label=algorithm_def["label"],
        salt_correction=resolved_settings.salt_correction,
        salt_correction_label=salt_def["label"],
        polymer_pairing=algorithm_def.get("polymer_pairing", resolved_sequence_type),
        warnings=warnings,
    )


class PrimerDesignRequest(SequenceInput):
    sequence_type: Optional[Literal["dna", "rna"]] = None
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["primer-design-v1"] = "primer-design-v1"
    search_profile: PrimerDesignSearchProfile = Field(default_factory=PrimerDesignSearchProfile)
    target_start: int = 0
    target_end: Optional[int] = None
    primer_min_length: int = 18
    primer_max_length: int = 28
    product_min_length: int = 120
    product_max_length: int = 1500
    flank_search_span: int = 80
    gc_min_percent: float = 35.0
    gc_max_percent: float = 65.0
    tm_target_c: float = 62.0
    tm_max_delta_c: float = 3.0
    gc_clamp_min: int = 1
    max_poly_x: int = 4
    max_pairs: int = 8
    overhang_forward: str = ""
    overhang_reverse: str = ""
    tm_settings: Optional[PrimerTmSettings] = None


class PrimerDesignCandidateResponse(BaseModel):
    sequence: str
    anneal_sequence: str
    start: int
    end: int
    strand: int
    length: int
    anneal_length: int
    overhang_length: int
    tm: float
    gc_percent: float
    gc_clamp: int
    max_homopolymer: int
    max_self_complement: int = 0
    three_prime_self_complement: int = 0
    max_hairpin_stem: int = 0
    hairpin_loop_size: Optional[int] = None
    binding_site_count: Optional[int] = None
    off_target_site_count: Optional[int] = None
    warnings: List[str] = Field(default_factory=list)


class PrimerDesignPairResponse(BaseModel):
    rank: int
    penalty: float
    tm_delta: float
    product_start: int
    product_end: int
    product_length: int
    heterodimer_complement: int = 0
    three_prime_heterodimer: int = 0
    warnings: List[str] = Field(default_factory=list)
    forward: PrimerDesignCandidateResponse
    reverse: PrimerDesignCandidateResponse


class PrimerDesignResponse(BaseModel):
    schema_version: Literal["primer-design-result-v1"] = "primer-design-result-v1"
    effective_request: PrimerDesignRequest
    search_profile: PrimerDesignSearchProfile = Field(default_factory=PrimerDesignSearchProfile)
    sequence_name: Optional[str] = None
    sequence_type: str
    target_start: int
    target_end: int
    target_length: int
    pair_count: int
    pairs: List[PrimerDesignPairResponse] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


def _max_homopolymer_run(sequence: str) -> int:
    longest = 0
    current = 0
    previous = None
    for base in sequence:
        if base == previous:
            current += 1
        else:
            current = 1
            previous = base
        longest = max(longest, current)
    return longest


def _gc_clamp(sequence: str, window: int = 5) -> int:
    return sum(1 for base in sequence[-window:] if base in {"G", "C"})


def _linear_segment(sequence: str, start: int, end: int) -> Optional[str]:
    if start < 0 or end > len(sequence) or start >= end:
        return None
    return sequence[start:end]


def _design_candidate(
    anneal_sequence: str,
    start: int,
    end: int,
    strand: int,
    overhang: str,
    sequence_type: str,
    tm_settings: PrimerTmSettings,
    gc_min: float,
    gc_max: float,
    gc_clamp_min: int,
    max_poly_x: int,
    tm_target_c: float,
    tm_max_delta_c: float,
) -> Optional[dict[str, Any]]:
    primer_sequence = (overhang + anneal_sequence).upper()
    # Candidate ranking requires defined Tm and structure metrics.
    if any(base not in "ACGTU" for base in primer_sequence):
        return None
    gc_percent = calculate_gc_percent(anneal_sequence)
    if gc_percent < gc_min or gc_percent > gc_max:
        return None

    clamp = _gc_clamp(primer_sequence)
    if clamp < gc_clamp_min:
        return None

    homopolymer = _max_homopolymer_run(primer_sequence)
    if homopolymer > max_poly_x:
        return None

    tm_result = calculate_primer_tm_result(
        anneal_sequence,
        sequence_type=sequence_type,
        settings=tm_settings,
    )
    if tm_result.tm is None:
        return None

    if abs(tm_result.tm - tm_target_c) > tm_max_delta_c:
        return None

    return {
        "sequence": primer_sequence,
        "anneal_sequence": anneal_sequence,
        "start": start,
        "end": end,
        "strand": strand,
        "length": len(primer_sequence),
        "anneal_length": len(anneal_sequence),
        "overhang_length": len(overhang),
        "tm": round(tm_result.tm, 2),
        "gc_percent": gc_percent,
        "gc_clamp": clamp,
        "max_homopolymer": homopolymer,
        "warnings": list(tm_result.warnings),
    }


def design_primer_pairs_for_request(
    request: PrimerDesignRequest, sequence_name: Optional[str]
) -> PrimerDesignResponse:
    search_profile = request.search_profile
    sequence_type = normalize_sequence_type(request.sequence_type, request.sequence)
    if sequence_type != "dna":
        raise HTTPException(status_code=400, detail="PCR primer design requires a DNA template. Supply the intended cDNA sequence for an RNA source; reverse transcription is not modeled.")
    template = clean_inline_sequence(request.sequence or "", sequence_type)
    if not template:
        raise HTTPException(
            status_code=400, detail="Sequence contains no valid nucleotides"
        )

    sequence_length = len(template)
    target_end = (
        request.target_end if request.target_end is not None else sequence_length
    )
    if (
        request.target_start < 0
        or target_end > sequence_length
        or request.target_start >= target_end
    ):
        raise HTTPException(
            status_code=400, detail="Target range is invalid for the current sequence"
        )
    if (
        request.primer_min_length < 12
        or request.primer_max_length < request.primer_min_length
    ):
        raise HTTPException(status_code=400, detail="Primer length range is invalid")
    if (
        request.product_min_length < 40
        or request.product_max_length < request.product_min_length
    ):
        raise HTTPException(status_code=400, detail="Product length range is invalid")

    tm_settings = request.tm_settings or default_tm_settings_for_sequence_type(
        sequence_type
    )
    forward_overhang = clean_inline_sequence(request.overhang_forward, sequence_type) if request.overhang_forward else ""
    reverse_overhang = clean_inline_sequence(request.overhang_reverse, sequence_type) if request.overhang_reverse else ""
    if any(base not in "ACGTU" for base in forward_overhang + reverse_overhang):
        raise HTTPException(status_code=400, detail="Primer design requires unambiguous 5′ tails so candidates can be scored.")
    forward_candidates: list[dict[str, Any]] = []
    reverse_candidates: list[dict[str, Any]] = []
    warnings: list[str] = ["Search considers at most 48 Tm-ranked candidates per strand and excludes overlapping primer footprints; results are not an exhaustive primer search."]

    # On a circular template candidates may cross the origin. Search coordinates
    # are "unrolled" (start may be negative, end may exceed the length); the
    # reported start/end are folded back so end > length marks a wrapped site.
    wrap = bool(request.is_circular and search_profile.origin_wrapping_candidates)

    def _window(u_start: int, u_end: int) -> Optional[str]:
        if not wrap:
            return _linear_segment(template, u_start, u_end)
        if u_end - u_start > sequence_length or u_start >= u_end:
            return None
        return (template * 3)[u_start + sequence_length:u_end + sequence_length]

    def _place(candidate: dict[str, Any], u_start: int, u_end: int) -> dict[str, Any]:
        candidate["u_start"], candidate["u_end"] = u_start, u_end
        if wrap:
            candidate["start"] = u_start % sequence_length
            candidate["end"] = candidate["start"] + (u_end - u_start)
        return candidate

    forward_start_min = max(-sequence_length if wrap else 0, request.target_start - request.flank_search_span)
    forward_start_max = min(sequence_length, request.target_start + 1)
    reverse_end_min = max(0, target_end)
    reverse_end_max = min(2 * sequence_length if wrap else sequence_length, target_end + request.flank_search_span)

    for start in range(forward_start_min, forward_start_max):
        for anneal_length in range(
            request.primer_min_length, request.primer_max_length + 1
        ):
            end = start + anneal_length
            anneal_sequence = _window(start, end)
            if anneal_sequence is None:
                continue
            if end > request.target_start + search_profile.target_overlap_bases:
                continue
            candidate = _design_candidate(
                anneal_sequence=anneal_sequence,
                start=start,
                end=end,
                strand=1,
                overhang=forward_overhang,
                sequence_type=sequence_type,
                tm_settings=tm_settings,
                gc_min=request.gc_min_percent,
                gc_max=request.gc_max_percent,
                gc_clamp_min=request.gc_clamp_min,
                max_poly_x=request.max_poly_x,
                tm_target_c=request.tm_target_c,
                tm_max_delta_c=request.tm_max_delta_c,
            )
            if candidate:
                forward_candidates.append(_place(candidate, start, end))

    for end in range(reverse_end_min, reverse_end_max + 1):
        for anneal_length in range(
            request.primer_min_length, request.primer_max_length + 1
        ):
            start = end - anneal_length
            anneal_template = _window(start, end)
            if anneal_template is None:
                continue
            if start < target_end - search_profile.target_overlap_bases:
                continue
            anneal_sequence = reverse_complement(anneal_template, sequence_type)
            candidate = _design_candidate(
                anneal_sequence=anneal_sequence,
                start=start,
                end=end,
                strand=-1,
                overhang=reverse_overhang,
                sequence_type=sequence_type,
                tm_settings=tm_settings,
                gc_min=request.gc_min_percent,
                gc_max=request.gc_max_percent,
                gc_clamp_min=request.gc_clamp_min,
                max_poly_x=request.max_poly_x,
                tm_target_c=request.tm_target_c,
                tm_max_delta_c=request.tm_max_delta_c,
            )
            if candidate:
                reverse_candidates.append(_place(candidate, start, end))

    forward_candidates.sort(
        key=lambda candidate: (
            abs(candidate["tm"] - request.tm_target_c),
            candidate["start"],
        )
    )
    reverse_candidates.sort(
        key=lambda candidate: (
            abs(candidate["tm"] - request.tm_target_c),
            candidate["start"],
        )
    )

    forward_candidates = forward_candidates[:search_profile.candidates_per_strand]
    reverse_candidates = reverse_candidates[:search_profile.candidates_per_strand]
    # QC does not participate in the existing Tm/start shortlist ordering.
    for candidate in [*forward_candidates, *reverse_candidates]:
        qc = _evaluate_primer_qc_canonical(
            candidate["sequence"], sequence_type=sequence_type,  # type: ignore[arg-type]
            template_sequence=template, circular_template=request.is_circular,
            min_binding_anneal_length=PRIMER_QC_PROFILE.binding_core_bases,
            intended_binding=(candidate["start"], candidate["end"], candidate["strand"]),
        )
        for field in ("max_self_complement", "three_prime_self_complement",
                      "max_hairpin_stem", "hairpin_loop_size", "binding_site_count",
                      "off_target_site_count"):
            candidate[field] = getattr(qc, field)
        candidate["warnings"].extend(qc.warnings)

    pair_candidates: list[dict[str, Any]] = []
    for forward in forward_candidates:
        for reverse in reverse_candidates:
            # Match the supported PCR sequence geometry; overlapping 3′ ends
            # require a different extension/primer-dimer model.
            if forward["u_end"] > reverse["u_start"]:
                continue
            product_length = (reverse["u_end"] - forward["u_start"]
                              + forward["overhang_length"] + reverse["overhang_length"])
            if (
                product_length < request.product_min_length
                or product_length > request.product_max_length
            ):
                continue
            if forward["u_start"] > request.target_start or reverse["u_end"] < target_end:
                continue
            if wrap and reverse["u_end"] - forward["u_start"] > sequence_length:
                continue

            tm_delta = abs(forward["tm"] - reverse["tm"])
            pair_qc = evaluate_primer_pair_qc(forward["sequence"], reverse["sequence"])
            penalty = round(
                abs(forward["tm"] - request.tm_target_c)
                + abs(reverse["tm"] - request.tm_target_c)
                + tm_delta * 2.5
                + pair_qc.heterodimer_complement * 1.8
                + pair_qc.three_prime_heterodimer * 2.8
                + max(forward.get("off_target_site_count") or 0, 0) * 1.2
                + max(reverse.get("off_target_site_count") or 0, 0) * 1.2
                + abs(product_length - (target_end - request.target_start))
                / max(20.0, request.flank_search_span),
                3,
            )
            pair_candidates.append(
                {
                    "penalty": penalty,
                    "tm_delta": round(tm_delta, 2),
                    "product_start": forward["start"],
                    "product_end": (reverse["u_end"] - sequence_length
                                    if wrap and reverse["u_end"] > sequence_length else reverse["u_end"]),
                    "product_length": product_length,
                    "heterodimer_complement": pair_qc.heterodimer_complement,
                    "three_prime_heterodimer": pair_qc.three_prime_heterodimer,
                    "warnings": [
                        *forward["warnings"],
                        *reverse["warnings"],
                        *pair_qc.warnings,
                    ],
                    "forward": forward,
                    "reverse": reverse,
                }
            )

    pair_candidates.sort(
        key=lambda pair: (pair["penalty"], pair["tm_delta"], pair["product_length"])
    )
    top_pairs = pair_candidates[: request.max_pairs]
    pairs = [
        PrimerDesignPairResponse(
            rank=index + 1,
            penalty=pair["penalty"],
            tm_delta=pair["tm_delta"],
            product_start=pair["product_start"],
            product_end=pair["product_end"],
            product_length=pair["product_length"],
            heterodimer_complement=pair["heterodimer_complement"],
            three_prime_heterodimer=pair["three_prime_heterodimer"],
            warnings=pair["warnings"],
            forward=PrimerDesignCandidateResponse(**pair["forward"]),
            reverse=PrimerDesignCandidateResponse(**pair["reverse"]),
        )
        for index, pair in enumerate(top_pairs)
    ]

    if not pairs:
        warnings.append(
            "No primer pairs in the searched candidates met the current GC/Tm/product constraints. Relax the design settings or widen the target flanks."
        )

    return PrimerDesignResponse(
        search_profile=search_profile,
        effective_request=request.model_copy(update={"sequence": template, "sequence_type": sequence_type, "target_end": target_end, "tm_settings": tm_settings, "overhang_forward": forward_overhang, "overhang_reverse": reverse_overhang}),
        sequence_name=sequence_name,
        sequence_type=sequence_type,
        target_start=request.target_start,
        target_end=target_end,
        target_length=target_end - request.target_start,
        pair_count=len(pairs),
        pairs=pairs,
        warnings=warnings,
    )
