"""
Molecular biology operations API.
Provides digest, PCR, ligation, mutagenesis, Gibson, and Golden Gate workflows.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
import uuid
from Bio.SeqUtils import MeltingTemp as mt

from database import NucleotideSequence, get_session
from services.molbio_ops import (
    DigestEnzyme,
    digest_sequence,
    pcr_product,
    apply_mutations,
)


router = APIRouter(prefix="/api/molbio", tags=["molbio"])


class SequenceInput(BaseModel):
    sequence_id: Optional[str] = None
    name: Optional[str] = None
    sequence: Optional[str] = None
    sequence_type: Optional[str] = None
    is_circular: bool = False


class EnzymeSchema(BaseModel):
    name: str
    site: str
    cut_index: Optional[int] = None


class DigestRequest(SequenceInput):
    enzymes: List[EnzymeSchema]
    save: bool = False
    new_name: Optional[str] = None


class PCRRequest(SequenceInput):
    primer_fwd: str
    primer_rev: str
    save: bool = True
    new_name: Optional[str] = None


class LigationRequest(BaseModel):
    fragments: List[str]
    circular: bool = True
    parent_id: Optional[str] = None
    save: bool = True
    new_name: Optional[str] = None


class MutationSchema(BaseModel):
    pos: int
    to: str
    from_base: Optional[str] = Field(default=None, alias="from")

    class Config:
        populate_by_name = True


class MutagenesisRequest(SequenceInput):
    mutations: List[MutationSchema]
    save: bool = True
    new_name: Optional[str] = None


class GibsonRequest(BaseModel):
    fragments: List[str]
    overlap_length: int = 20
    circular: bool = True
    parent_id: Optional[str] = None
    save: bool = True
    new_name: Optional[str] = None


class GoldenGateRequest(BaseModel):
    fragments: List[str]
    enzymes: List[EnzymeSchema]
    circular: bool = True
    parent_id: Optional[str] = None
    save: bool = True
    new_name: Optional[str] = None


class NucleotideSequenceResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    sequence: str
    sequence_type: str
    is_circular: bool
    length: int
    features: Optional[List[Any]]
    primers: Optional[List[Any]]
    organism: Optional[str]
    accession: Optional[str]
    source_file: Optional[str]
    gc_content: Optional[float]
    parent_id: Optional[str]
    operation: Optional[str]
    operation_params: Optional[dict]
    version: int
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class DigestFragmentResponse(BaseModel):
    sequence: str
    start: int
    end: int
    length: int
    wraps_origin: bool


class PCRProductResponse(BaseModel):
    sequence: str
    start: int
    end: int
    length: int
    wraps_origin: bool


class MolbioOperationResponse(BaseModel):
    sequence: Optional[NucleotideSequenceResponse] = None
    fragments: Optional[List[DigestFragmentResponse]] = None
    product: Optional[PCRProductResponse] = None
    message: str


def normalize_sequence_type(sequence_type: Optional[str], sequence: Optional[str]) -> str:
    normalized = (sequence_type or "").strip().lower()
    if normalized in {"dna", "rna"}:
        return normalized

    sequence_text = (sequence or "").upper()
    if "U" in sequence_text and "T" not in sequence_text:
        return "rna"
    return "dna"


def clean_inline_sequence(sequence: str, sequence_type: str) -> str:
    upper = sequence.upper().replace(" ", "").replace("\n", "").replace("\r", "")
    valid_chars = set("ATCGNRYMKSWHBVD") if sequence_type == "dna" else set("AUCGNRYMKSWHBVD")
    return "".join(char for char in upper if char in valid_chars)


async def resolve_sequence(data: SequenceInput, session: AsyncSession) -> NucleotideSequence:
    if data.sequence_id:
        result = await session.execute(
            select(NucleotideSequence).where(NucleotideSequence.id == data.sequence_id)
        )
        seq = result.scalar_one_or_none()
        if not seq:
            raise HTTPException(status_code=404, detail="Sequence not found")
        return seq
    if not data.sequence:
        raise HTTPException(status_code=400, detail="Sequence or sequence_id is required")
    sequence_type = normalize_sequence_type(data.sequence_type, data.sequence)
    seq_clean = clean_inline_sequence(data.sequence, sequence_type)
    if not seq_clean:
        raise HTTPException(status_code=400, detail="Sequence contains no valid nucleotides")
    gc = 0.0
    if seq_clean:
        gc = round(((seq_clean.count('G') + seq_clean.count('C')) / len(seq_clean)) * 100, 2)
    # Construct a temporary sequence object
    return NucleotideSequence(
        id=str(uuid.uuid4()),
        name=data.name or "Unnamed Sequence",
        description=None,
        sequence=seq_clean,
        sequence_type=sequence_type,
        is_circular=data.is_circular,
        length=len(seq_clean),
        features=[],
        primers=[],
        organism=None,
        accession=None,
        source_file=None,
        gc_content=gc,
        parent_id=None,
        operation=None,
        operation_params=None,
        version=1,
    )


def create_child_sequence(
    parent: Optional[NucleotideSequence],
    sequence: str,
    name: str,
    circular: bool,
    operation: str,
    operation_params: dict,
    sequence_type: Optional[str] = None,
) -> NucleotideSequence:
    def calc_gc(seq: str) -> float:
        if not seq:
            return 0.0
        gc = seq.count('G') + seq.count('C')
        return round((gc / len(seq)) * 100, 2)

    parent_id = parent.id if parent else None
    version = (parent.version + 1) if parent and parent.version else 1
    return NucleotideSequence(
        id=str(uuid.uuid4()),
        name=name,
        description=parent.description if parent else None,
        sequence=sequence,
        sequence_type=normalize_sequence_type(sequence_type or (parent.sequence_type if parent else None), sequence),
        is_circular=circular,
        length=len(sequence),
        features=[],
        primers=[],
        organism=parent.organism if parent else None,
        accession=parent.accession if parent else None,
        source_file=None,
        gc_content=calc_gc(sequence),
        parent_id=parent_id,
        operation=operation,
        operation_params=operation_params,
        version=version,
    )


@router.post("/digest", response_model=MolbioOperationResponse)
async def digest(
    request: DigestRequest,
    session: AsyncSession = Depends(get_session)
):
    parent = await resolve_sequence(request, session)
    enzymes = [DigestEnzyme(name=e.name, site=e.site, cut_index=e.cut_index) for e in request.enzymes]
    fragments = digest_sequence(parent.sequence, enzymes, circular=parent.is_circular)
    fragment_payload = [
        DigestFragmentResponse(
            sequence=f.sequence,
            start=f.start,
            end=f.end,
            length=len(f.sequence),
            wraps_origin=f.start >= f.end,
        )
        for f in fragments
    ]

    if not request.save:
        return MolbioOperationResponse(
            fragments=fragment_payload,
            message=f"Digest produced {len(fragment_payload)} fragments"
        )

    new_name = request.new_name or f"{parent.name}_digest"
    seq_obj = create_child_sequence(
        parent=parent if request.sequence_id else None,
        sequence="".join(f.sequence for f in fragments),
        name=new_name,
        circular=False,
        operation="digest",
        operation_params={"enzymes": [e.dict() for e in request.enzymes]},
    )
    session.add(seq_obj)
    await session.commit()
    await session.refresh(seq_obj)
    return MolbioOperationResponse(
        sequence=seq_obj,
        fragments=fragment_payload,
        message="Digest complete"
    )


@router.post("/pcr", response_model=MolbioOperationResponse)
async def pcr(
    request: PCRRequest,
    session: AsyncSession = Depends(get_session)
):
    parent = await resolve_sequence(request, session)
    try:
        product = pcr_product(
            parent.sequence,
            request.primer_fwd,
            request.primer_rev,
            circular=parent.is_circular,
            sequence_type=parent.sequence_type or "dna",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    product_payload = PCRProductResponse(
        sequence=product.sequence,
        start=product.start,
        end=product.end,
        length=product.length,
        wraps_origin=product.wraps_origin,
    )
    if not request.save:
        return MolbioOperationResponse(product=product_payload, message="PCR complete")

    new_name = request.new_name or f"{parent.name}_PCR"
    seq_obj = create_child_sequence(
        parent=parent if request.sequence_id else None,
        sequence=product.sequence,
        name=new_name,
        circular=False,
        operation="pcr",
        operation_params={"primer_fwd": request.primer_fwd, "primer_rev": request.primer_rev},
    )
    session.add(seq_obj)
    await session.commit()
    await session.refresh(seq_obj)
    return MolbioOperationResponse(sequence=seq_obj, product=product_payload, message="PCR complete")


@router.post("/ligate", response_model=MolbioOperationResponse)
async def ligate(
    request: LigationRequest,
    session: AsyncSession = Depends(get_session)
):
    raise HTTPException(
        status_code=501,
        detail=(
            "Ligation is disabled because the previous implementation used simplified fragment concatenation "
            "without end-compatibility validation. Re-enable only after robust ligation logic is implemented."
        ),
    )


@router.post("/mutagenesis", response_model=MolbioOperationResponse)
async def mutagenesis(
    request: MutagenesisRequest,
    session: AsyncSession = Depends(get_session)
):
    parent = await resolve_sequence(request, session)
    mutations = [
        {"pos": m.pos, "from": m.from_base, "to": m.to} for m in request.mutations
    ]
    try:
        mutated = apply_mutations(parent.sequence, mutations)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not request.save:
        return MolbioOperationResponse(message="Mutagenesis complete")

    new_name = request.new_name or f"{parent.name}_mut"
    seq_obj = create_child_sequence(
        parent=parent if request.sequence_id else None,
        sequence=mutated,
        name=new_name,
        circular=parent.is_circular,
        operation="mutagenesis",
        operation_params={"mutations": mutations},
    )
    session.add(seq_obj)
    await session.commit()
    await session.refresh(seq_obj)
    return MolbioOperationResponse(sequence=seq_obj, message="Mutagenesis complete")


@router.post("/gibson", response_model=MolbioOperationResponse)
async def gibson(
    request: GibsonRequest,
    session: AsyncSession = Depends(get_session)
):
    raise HTTPException(
        status_code=501,
        detail=(
            "Gibson assembly is disabled because the previous implementation only accepted exact overlap-string matches "
            "and did not perform robust assembly validation."
        ),
    )


@router.post("/golden-gate", response_model=MolbioOperationResponse)
async def golden_gate(
    request: GoldenGateRequest,
    session: AsyncSession = Depends(get_session)
):
    raise HTTPException(
        status_code=501,
        detail=(
            "Golden Gate assembly is disabled because the previous implementation removed recognition sites and concatenated fragments "
            "without robust overhang validation."
        ),
    )


# ============================================================================
# Auto-Annotation using pLannotate
# ============================================================================

class AutoAnnotateRequest(BaseModel):
    """Request for automatic feature detection using pLannotate."""
    sequence: str = Field(..., description="DNA sequence to annotate")
    is_linear: bool = Field(False, description="Whether the sequence is linear (default: circular)")
    detailed: bool = Field(False, description="Use detailed search mode (more hits, more false positives)")
    min_identity: float = Field(50.0, description="Minimum percent identity threshold for features")


class DetectedFeature(BaseModel):
    """A feature detected by pLannotate."""
    name: str
    type: str
    start: int
    end: int
    strand: int  # 1 or -1
    identity_pct: float
    match_length_pct: float
    is_fragment: bool
    database: str
    description: str


class AutoAnnotateResponse(BaseModel):
    """Response from auto-annotation."""
    features: List[DetectedFeature]
    message: str


@router.post("/auto-annotate", response_model=AutoAnnotateResponse)
async def auto_annotate(request: AutoAnnotateRequest):
    """
    Auto-detect plasmid features using pLannotate.
    
    Uses BLAST-based detection to identify common plasmid components like:
    - Origins of replication (ori, ColE1, etc.)
    - Antibiotic resistance genes (KanR, AmpR, CmR, etc.)
    - Promoters and terminators
    - Common tags and reporters
    
    Requires pLannotate to be installed via micromamba.
    """
    import subprocess
    import tempfile
    import csv
    import os
    from pathlib import Path
    
    # Validate sequence
    sequence = request.sequence.upper().replace(" ", "").replace("\n", "")
    if not sequence:
        raise HTTPException(status_code=400, detail="Empty sequence provided")
    
    if not all(c in "ATGCNRYSWKMBDHV" for c in sequence):
        raise HTTPException(status_code=400, detail="Invalid DNA sequence characters")
    
    # Create temporary files for input/output
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = os.path.join(tmpdir, "input.fasta")
        output_dir = tmpdir
        
        # Write input FASTA
        with open(input_file, "w") as f:
            f.write(">input_sequence\n")
            # Write sequence in 60-char lines
            for i in range(0, len(sequence), 60):
                f.write(sequence[i:i+60] + "\n")
        
        # Build plannotate command with optional sensitive search config.
        sensitive_yaml = os.getenv(
            "BMS_PLANNOTATE_SENSITIVE_YAML",
            str(Path.home() / ".plannotate_sensitive.yml"),
        )
        micromamba_bin = os.getenv("BMS_MICROMAMBA_BIN", "micromamba")
        micromamba_root_prefix = os.getenv("BMS_MICROMAMBA_ROOT_PREFIX")
        plannotate_env = os.getenv("BMS_PLANNOTATE_ENV", "plannotate")

        cmd = [micromamba_bin, "run", "-n", plannotate_env]
        if micromamba_root_prefix:
            cmd.extend(["--root-prefix", micromamba_root_prefix])
        cmd.extend([
            "plannotate", "batch",
            "-i", input_file,
            "-o", output_dir,
            "--csv",
        ])
        if os.path.exists(sensitive_yaml):
            cmd.extend(["-y", sensitive_yaml])
        
        if request.is_linear:
            cmd.append("-l")  # --linear flag
        
        if request.detailed:
            cmd.append("-d")  # --detailed flag
        
        # Run pLannotate
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120  # 2 minute timeout
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="pLannotate timed out")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"pLannotate execution failed: {str(e)}")

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            detail = stderr or stdout or f"pLannotate exited with status {result.returncode}"
            raise HTTPException(status_code=500, detail=detail[:1000])
        
        # Find and parse CSV output
        csv_files = [f for f in os.listdir(output_dir) if f.endswith("_pLann.csv")]
        if not csv_files:
            # pLannotate ran but found no features
            return AutoAnnotateResponse(features=[], message="No features detected")
        
        csv_path = os.path.join(output_dir, csv_files[0])
        features = []
        
        try:
            with open(csv_path, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    identity = float(row.get("percent identity", 0))
                    
                    # Skip features below identity threshold
                    if identity < request.min_identity:
                        continue
                    
                    strand_str = row.get("strand", "+")
                    strand = 1 if strand_str == "1" or strand_str == "+" else -1
                    
                    is_fragment = row.get("fragment", "False").lower() == "true"
                    
                    features.append(DetectedFeature(
                        name=row.get("Feature", "Unknown"),
                        type=row.get("Type", "misc_feature"),
                        start=int(row.get("start location", 0)),
                        end=int(row.get("end location", 0)),
                        strand=strand,
                        identity_pct=identity,
                        match_length_pct=float(row.get("percent match length", 0)),
                        is_fragment=is_fragment,
                        database=row.get("database", "unknown"),
                        description=row.get("Description", "")[:500]  # Truncate long descriptions
                    ))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to parse pLannotate output: {str(e)}")
        
        # Sort by start position
        features.sort(key=lambda f: f.start)
        
        return AutoAnnotateResponse(
            features=features,
            message=f"Detected {len(features)} features"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# PRIMER LIBRARY API
# ═══════════════════════════════════════════════════════════════════════════════

from database import Primer

TM_ALGORITHM_DEFS = {
    "wallace": {
        "label": "Wallace rule",
        "description": "Rule-of-thumb 2/4 formula for short oligos.",
        "kind": "wallace",
        "sequence_types": ["dna", "rna"],
    },
    "gc_empirical": {
        "label": "GC empirical",
        "description": "Empirical GC-based formula with selectable salt correction.",
        "kind": "gc",
        "sequence_types": ["dna", "rna"],
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
}


class PrimerTmSettings(BaseModel):
    algorithm: str = "nn_santalucia_hicks_2004"
    salt_correction: str = "owczarzy_2008"
    primer_concentration_nM: float = 250.0
    template_concentration_nM: float = 0.0
    na_mM: float = 50.0
    k_mM: float = 0.0
    tris_mM: float = 0.0
    mg_mM: float = 1.5
    dntps_mM: float = 0.6
    dmso_percent: float = 0.0
    formamide_percent: float = 0.0
    self_complementary: bool = False


class PrimerTmInput(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    sequence: str
    sequence_type: Optional[str] = None
    complement_sequence: Optional[str] = None
    shift: int = 0


class PrimerTmResult(BaseModel):
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
    primers: List[PrimerTmInput]
    settings: Optional[PrimerTmSettings] = None


class PrimerTmOption(BaseModel):
    id: str
    label: str
    description: str
    sequence_types: List[str]
    polymer_pairing: Optional[str] = None


class PrimerTmSaltCorrectionOption(BaseModel):
    id: str
    label: str
    description: str


class PrimerTmOptionsResponse(BaseModel):
    algorithms: List[PrimerTmOption]
    salt_corrections: List[PrimerTmSaltCorrectionOption]
    defaults: dict[str, PrimerTmSettings]


def clean_primer_sequence(sequence: str) -> str:
    """Normalize primer sequence text."""
    return (sequence or "").upper().replace(" ", "").replace("\n", "").replace("\r", "")


def infer_primer_sequence_type(sequence: str) -> str:
    upper = clean_primer_sequence(sequence)
    if "U" in upper and "T" not in upper:
        return "rna"
    return "dna"


def calculate_gc_percent(sequence: str) -> float:
    """Calculate GC content percentage."""
    if not sequence or len(sequence) == 0:
        return 0.0
    upper = sequence.upper()
    gc = upper.count('G') + upper.count('C')
    return round((gc / len(sequence)) * 100, 1)


def default_tm_settings_for_sequence_type(sequence_type: str) -> PrimerTmSettings:
    defaults = DEFAULT_TM_SETTINGS_BY_SEQUENCE_TYPE.get(sequence_type, DEFAULT_TM_SETTINGS_BY_SEQUENCE_TYPE["dna"])
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
    resolved_settings = settings or default_tm_settings_for_sequence_type(resolved_sequence_type)
    gc_percent = calculate_gc_percent(cleaned)
    warnings: List[str] = []

    if not cleaned:
        return PrimerTmResult(
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
            sequence=cleaned,
            sequence_type=resolved_sequence_type,
            length=len(cleaned),
            gc_percent=gc_percent,
            tm=None,
            algorithm=resolved_settings.algorithm,
            algorithm_label=algorithm_def["label"],
            salt_correction=resolved_settings.salt_correction,
            salt_correction_label=resolved_settings.salt_correction,
            polymer_pairing=algorithm_def.get("polymer_pairing", resolved_sequence_type),
            warnings=[f"Unknown salt correction '{resolved_settings.salt_correction}'."],
        )

    if resolved_sequence_type not in algorithm_def["sequence_types"]:
        return PrimerTmResult(
            sequence=cleaned,
            sequence_type=resolved_sequence_type,
            length=len(cleaned),
            gc_percent=gc_percent,
            tm=None,
            algorithm=resolved_settings.algorithm,
            algorithm_label=algorithm_def["label"],
            salt_correction=resolved_settings.salt_correction,
            salt_correction_label=salt_def["label"],
            polymer_pairing=algorithm_def.get("polymer_pairing", resolved_sequence_type),
            warnings=[f"Algorithm '{algorithm_def['label']}' does not support {resolved_sequence_type.upper()} primers."],
        )

    tm_value: Optional[float] = None
    try:
        if algorithm_def["kind"] == "wallace":
            tm_value = float(mt.Tm_Wallace(cleaned, strict=True))
        elif algorithm_def["kind"] == "gc":
            tm_value = float(mt.Tm_GC(
                cleaned,
                strict=True,
                valueset=algorithm_def.get("gc_valueset", 7),
                Na=resolved_settings.na_mM,
                K=resolved_settings.k_mM,
                Tris=resolved_settings.tris_mM,
                Mg=resolved_settings.mg_mM,
                dNTPs=resolved_settings.dntps_mM,
                saltcorr=salt_def["method"],
            ))
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
            if complement_sequence:
                tm_kwargs["c_seq"] = clean_primer_sequence(complement_sequence)
                tm_kwargs["shift"] = shift
            tm_value = float(mt.Tm_NN(cleaned, **tm_kwargs))

        if resolved_settings.dmso_percent or resolved_settings.formamide_percent:
            warnings.append("DMSO/formamide corrections are approximate.")
            tm_value = float(mt.chem_correction(
                tm_value,
                DMSO=resolved_settings.dmso_percent,
                fmd=resolved_settings.formamide_percent,
                GC=gc_percent,
            ))
    except Exception as exc:
        warnings.append(str(exc))

    return PrimerTmResult(
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


def build_primer_response(primer: Primer) -> "PrimerResponse":
    return PrimerResponse(
        id=primer.id,
        name=primer.name,
        sequence=primer.sequence,
        sequence_type=primer.sequence_type or infer_primer_sequence_type(primer.sequence),
        length=primer.length,
        tm=primer.tm,
        gc_percent=primer.gc_percent,
        tm_algorithm=primer.tm_algorithm,
        tm_salt_correction=primer.tm_salt_correction,
        tm_settings=primer.tm_settings,
        primer_type=primer.primer_type,
        description=primer.description,
        target_sequence_id=primer.target_sequence_id,
        binding_start=primer.binding_start,
        binding_end=primer.binding_end,
        binding_strand=primer.binding_strand or 1,
        tags=primer.tags,
        is_favorite=primer.is_favorite,
        created_at=primer.created_at,
        updated_at=primer.updated_at,
    )


class PrimerCreate(BaseModel):
    """Request to create a new primer."""
    name: str
    sequence: str
    sequence_type: Optional[str] = None
    primer_type: str = "general"
    description: Optional[str] = None
    target_sequence_id: Optional[str] = None
    binding_start: Optional[int] = None
    binding_end: Optional[int] = None
    binding_strand: int = 1
    tags: Optional[List[str]] = None
    tm_settings: Optional[PrimerTmSettings] = None


class PrimerUpdate(BaseModel):
    """Request to update an existing primer."""
    name: Optional[str] = None
    sequence: Optional[str] = None
    sequence_type: Optional[str] = None
    primer_type: Optional[str] = None
    description: Optional[str] = None
    target_sequence_id: Optional[str] = None
    binding_start: Optional[int] = None
    binding_end: Optional[int] = None
    binding_strand: Optional[int] = None
    tags: Optional[List[str]] = None
    is_favorite: Optional[bool] = None
    tm_settings: Optional[PrimerTmSettings] = None


class PrimerResponse(BaseModel):
    """Primer library entry response."""
    id: str
    name: str
    sequence: str
    sequence_type: str
    length: int
    tm: Optional[float]
    gc_percent: Optional[float]
    tm_algorithm: Optional[str]
    tm_salt_correction: Optional[str]
    tm_settings: Optional[dict[str, Any]]
    primer_type: str
    description: Optional[str]
    target_sequence_id: Optional[str]
    binding_start: Optional[int]
    binding_end: Optional[int]
    binding_strand: int
    tags: Optional[List[str]]
    is_favorite: bool
    created_at: datetime
    updated_at: Optional[datetime]


@router.get("/primer-tm/options", response_model=PrimerTmOptionsResponse)
async def primer_tm_options():
    """Return supported Tm algorithms, salt corrections, and default settings."""
    return PrimerTmOptionsResponse(
        algorithms=[
            PrimerTmOption(
                id=option_id,
                label=definition["label"],
                description=definition["description"],
                sequence_types=definition["sequence_types"],
                polymer_pairing=definition.get("polymer_pairing"),
            )
            for option_id, definition in TM_ALGORITHM_DEFS.items()
        ],
        salt_corrections=[
            PrimerTmSaltCorrectionOption(
                id=option_id,
                label=definition["label"],
                description=definition["description"],
            )
            for option_id, definition in TM_SALT_CORRECTION_DEFS.items()
        ],
        defaults={
            sequence_type: PrimerTmSettings(**settings)
            for sequence_type, settings in DEFAULT_TM_SETTINGS_BY_SEQUENCE_TYPE.items()
        },
    )


@router.post("/primer-tm/calculate", response_model=List[PrimerTmResult])
async def calculate_primer_tm_batch(request: PrimerTmBatchRequest):
    """Calculate Tm for one or more primers using selectable thermodynamic models."""
    results: List[PrimerTmResult] = []
    for primer in request.primers:
        resolved_sequence_type = primer.sequence_type or infer_primer_sequence_type(primer.sequence)
        settings = request.settings or default_tm_settings_for_sequence_type(resolved_sequence_type)
        result = calculate_primer_tm_result(
            sequence=primer.sequence,
            sequence_type=resolved_sequence_type,
            settings=settings,
            complement_sequence=primer.complement_sequence,
            shift=primer.shift,
        )
        result.id = primer.id
        result.name = primer.name
        results.append(result)
    return results


@router.get("/primers", response_model=List[PrimerResponse])
async def list_primers(
    search: Optional[str] = None,
    primer_type: Optional[str] = None,
    favorites_only: bool = False,
    target_sequence_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """List all primers with optional filtering."""
    query = select(Primer).order_by(Primer.created_at.desc())
    
    if favorites_only:
        query = query.where(Primer.is_favorite == True)
    if primer_type:
        query = query.where(Primer.primer_type == primer_type)
    if target_sequence_id:
        query = query.where(Primer.target_sequence_id == target_sequence_id)
    
    result = await session.execute(query)
    primers = result.scalars().all()
    
    # Filter by search term if provided
    if search:
        search_lower = search.lower()
        primers = [p for p in primers if 
                   search_lower in p.name.lower() or 
                   search_lower in p.sequence.lower() or
                   (p.description and search_lower in p.description.lower())]
    
    return [build_primer_response(p) for p in primers]


@router.post("/primers", response_model=PrimerResponse)
async def create_primer(
    request: PrimerCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new primer in the library."""
    # Validate and clean sequence
    sequence = clean_primer_sequence(request.sequence)
    if not all(c in "ATCGUMRWSYKVHDBN" for c in sequence):
        raise HTTPException(status_code=400, detail="Invalid nucleotide sequence")

    sequence_type = request.sequence_type or infer_primer_sequence_type(sequence)
    tm_settings = request.tm_settings or default_tm_settings_for_sequence_type(sequence_type)
    tm_result = calculate_primer_tm_result(sequence, sequence_type=sequence_type, settings=tm_settings)

    primer = Primer(
        id=str(uuid.uuid4()),
        name=request.name,
        sequence=sequence,
        sequence_type=sequence_type,
        length=len(sequence),
        tm=tm_result.tm,
        gc_percent=tm_result.gc_percent,
        tm_algorithm=tm_result.algorithm,
        tm_salt_correction=tm_result.salt_correction,
        tm_settings=tm_settings.model_dump(),
        primer_type=request.primer_type,
        description=request.description,
        target_sequence_id=request.target_sequence_id,
        binding_start=request.binding_start,
        binding_end=request.binding_end,
        binding_strand=request.binding_strand,
        tags=request.tags,
        is_favorite=False,
        created_at=datetime.utcnow()
    )
    
    session.add(primer)
    await session.commit()
    await session.refresh(primer)
    
    return build_primer_response(primer)


@router.get("/primers/{primer_id}", response_model=PrimerResponse)
async def get_primer(
    primer_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get a specific primer by ID."""
    result = await session.execute(select(Primer).where(Primer.id == primer_id))
    primer = result.scalar_one_or_none()
    
    if not primer:
        raise HTTPException(status_code=404, detail="Primer not found")
    
    return build_primer_response(primer)


@router.patch("/primers/{primer_id}", response_model=PrimerResponse)
async def update_primer(
    primer_id: str,
    request: PrimerUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update an existing primer."""
    result = await session.execute(select(Primer).where(Primer.id == primer_id))
    primer = result.scalar_one_or_none()
    
    if not primer:
        raise HTTPException(status_code=404, detail="Primer not found")
    
    # Update fields if provided
    recalculate_tm = False
    if request.name is not None:
        primer.name = request.name
    if request.sequence is not None:
        primer.sequence = clean_primer_sequence(request.sequence)
        primer.length = len(primer.sequence)
        recalculate_tm = True
    if request.sequence_type is not None:
        primer.sequence_type = request.sequence_type
        recalculate_tm = True
    if request.primer_type is not None:
        primer.primer_type = request.primer_type
    if request.description is not None:
        primer.description = request.description
    if request.target_sequence_id is not None:
        primer.target_sequence_id = request.target_sequence_id
    if request.binding_start is not None:
        primer.binding_start = request.binding_start
    if request.binding_end is not None:
        primer.binding_end = request.binding_end
    if request.binding_strand is not None:
        primer.binding_strand = request.binding_strand
    if request.tags is not None:
        primer.tags = request.tags
    if request.is_favorite is not None:
        primer.is_favorite = request.is_favorite
    if request.tm_settings is not None:
        primer.tm_settings = request.tm_settings.model_dump()
        recalculate_tm = True

    if recalculate_tm:
        sequence_type = primer.sequence_type or infer_primer_sequence_type(primer.sequence)
        if not sequence_type:
            sequence_type = infer_primer_sequence_type(primer.sequence)
            primer.sequence_type = sequence_type
        tm_settings = request.tm_settings or (
            PrimerTmSettings(**primer.tm_settings)
            if primer.tm_settings
            else default_tm_settings_for_sequence_type(sequence_type)
        )
        tm_result = calculate_primer_tm_result(
            primer.sequence,
            sequence_type=sequence_type,
            settings=tm_settings,
        )
        primer.length = len(primer.sequence)
        primer.tm = tm_result.tm
        primer.gc_percent = tm_result.gc_percent
        primer.tm_algorithm = tm_result.algorithm
        primer.tm_salt_correction = tm_result.salt_correction
        primer.tm_settings = tm_settings.model_dump()
    
    primer.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(primer)
    
    return build_primer_response(primer)


@router.delete("/primers/{primer_id}")
async def delete_primer(
    primer_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Delete a primer from the library."""
    result = await session.execute(select(Primer).where(Primer.id == primer_id))
    primer = result.scalar_one_or_none()
    
    if not primer:
        raise HTTPException(status_code=404, detail="Primer not found")
    
    await session.delete(primer)
    await session.commit()
    
    return {"message": f"Primer '{primer.name}' deleted"}


@router.post("/primers/{primer_id}/toggle-favorite", response_model=PrimerResponse)
async def toggle_primer_favorite(
    primer_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Toggle favorite status for a primer."""
    result = await session.execute(select(Primer).where(Primer.id == primer_id))
    primer = result.scalar_one_or_none()
    
    if not primer:
        raise HTTPException(status_code=404, detail="Primer not found")
    
    primer.is_favorite = not primer.is_favorite
    primer.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(primer)
    
    return build_primer_response(primer)
