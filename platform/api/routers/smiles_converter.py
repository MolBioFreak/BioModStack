"""
SMILES Converter API Router
Convert peptide sequences and short nucleotide sequences to SMILES strings.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Literal

router = APIRouter()


# Nucleotide SMILES building blocks (5'->3' direction, phosphate linkable)
# These are nucleoside monophosphates that can be linked
NUCLEOTIDE_SMILES = {
    # DNA nucleotides (deoxyribose)
    'A': 'Nc1ncnc2c1ncn2[C@H]3C[C@H](O)[C@@H](CO)O3',  # dA
    'T': 'Cc1cn([C@H]2C[C@H](O)[C@@H](CO)O2)c(=O)[nH]c1=O',  # dT
    'G': 'Nc1nc2c(ncn2[C@H]3C[C@H](O)[C@@H](CO)O3)c(=O)[nH]1',  # dG
    'C': 'Nc1ccn([C@H]2C[C@H](O)[C@@H](CO)O2)c(=O)n1',  # dC
    # RNA nucleotides (ribose)
    'rA': 'Nc1ncnc2c1ncn2[C@@H]3O[C@H](CO)[C@@H](O)[C@H]3O',  # A
    'rU': 'O=c1ccn([C@@H]2O[C@H](CO)[C@@H](O)[C@H]2O)c(=O)[nH]1',  # U
    'rG': 'Nc1nc2c(ncn2[C@@H]3O[C@H](CO)[C@@H](O)[C@H]3O)c(=O)[nH]1',  # G
    'rC': 'Nc1ccn([C@@H]2O[C@H](CO)[C@@H](O)[C@H]2O)c(=O)n1',  # C
}

# Single nucleotide triphosphates (for when user wants just one NTP)
NTP_SMILES = {
    'dATP': 'Nc1ncnc2c1ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3',
    'dTTP': 'Cc1cn([C@H]2C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O2)c(=O)[nH]c1=O',
    'dGTP': 'Nc1nc2c(ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3)c(=O)[nH]1',
    'dCTP': 'Nc1ccn([C@H]2C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O2)c(=O)n1',
    'ATP': 'Nc1ncnc2c1ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O',
    'UTP': 'O=c1ccn([C@@H]2O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]2O)c(=O)[nH]1',
    'GTP': 'Nc1nc2c(ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O)c(=O)[nH]1',
    'CTP': 'Nc1ccn([C@@H]2O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]2O)c(=O)n1',
}


class SequenceToSmilesRequest(BaseModel):
    """Request for sequence to SMILES conversion."""
    sequence: str = Field(..., min_length=1, max_length=50, description="Sequence to convert (peptide AA or nucleotide)")
    sequence_type: Literal['peptide', 'dna', 'rna', 'ntp'] = Field(..., description="Type of sequence")


class SmilesResponse(BaseModel):
    """Response with SMILES string."""
    smiles: str
    sequence: str
    sequence_type: str
    length: int
    notes: Optional[str] = None


def peptide_to_smiles(sequence: str) -> str:
    """
    Convert peptide sequence to SMILES.
    Note: For short peptides (2-5 aa), we return a simplified representation.
    For longer peptides, this requires specialized tools.
    """
    sequence = sequence.upper().strip()
    
    # Validate - only standard amino acids
    valid_aa = set('ACDEFGHIKLMNPQRSTVWY')
    if not all(aa in valid_aa for aa in sequence):
        invalid = [aa for aa in sequence if aa not in valid_aa]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid amino acid(s): {invalid}. Only standard 20 amino acids allowed."
        )
    
    if len(sequence) > 10:
        raise HTTPException(
            status_code=400,
            detail="Peptide too long for SMILES conversion (max 10 aa). Use PDB structure instead."
        )
    
    # For short peptides, we can't easily generate accurate SMILES without
    # a dedicated library. Return a helpful message with the sequence.
    raise HTTPException(
        status_code=501,
        detail=f"Peptide→SMILES conversion coming soon. For now, use sequence '{sequence}' directly or find SMILES in PubChem/ChEMBL for specific peptides."
    )


def dna_to_smiles(sequence: str) -> str:
    """
    Convert short DNA sequence to SMILES.
    Returns individual nucleoside SMILES for very short sequences,
    or a note for longer sequences (phosphate linking is complex).
    """
    sequence = sequence.upper().replace(' ', '')
    
    # Validate
    valid_bases = set('ATGC')
    if not all(base in valid_bases for base in sequence):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid DNA sequence. Only A, T, G, C allowed. Got: {sequence}"
        )
    
    if len(sequence) > 15:
        raise HTTPException(
            status_code=400,
            detail="DNA sequence too long for SMILES conversion (max 15 nt)"
        )
    
    # For single nucleotide, return NTP
    if len(sequence) == 1:
        ntp_key = f"d{sequence}TP"
        return NTP_SMILES.get(ntp_key, NUCLEOTIDE_SMILES[sequence])
    
    # For multiple nucleotides, return individual nucleoside SMILES joined with "."
    # (dot notation indicates separate molecules - user can use this for docking)
    nucleoside_smiles = [NUCLEOTIDE_SMILES[base] for base in sequence]
    return '.'.join(nucleoside_smiles)


def rna_to_smiles(sequence: str) -> str:
    """Convert short RNA sequence to SMILES."""
    sequence = sequence.upper().replace(' ', '').replace('T', 'U')  # Convert T to U
    
    # Validate
    valid_bases = set('AUGC')
    if not all(base in valid_bases for base in sequence):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid RNA sequence. Only A, U, G, C allowed. Got: {sequence}"
        )
    
    if len(sequence) > 15:
        raise HTTPException(
            status_code=400,
            detail="RNA sequence too long for SMILES conversion (max 15 nt)"
        )
    
    # For single nucleotide, return NTP
    if len(sequence) == 1:
        ntp_map = {'A': 'ATP', 'U': 'UTP', 'G': 'GTP', 'C': 'CTP'}
        return NTP_SMILES[ntp_map[sequence]]
    
    # For multiple nucleotides, return individual nucleoside SMILES
    rna_map = {'A': 'rA', 'U': 'rU', 'G': 'rG', 'C': 'rC'}
    nucleoside_smiles = [NUCLEOTIDE_SMILES[rna_map[base]] for base in sequence]
    return '.'.join(nucleoside_smiles)


def ntp_to_smiles(sequence: str) -> str:
    """Convert NTP name (e.g., 'dATP', 'ATP') to SMILES."""
    sequence = sequence.upper().strip()
    
    if sequence in NTP_SMILES:
        return NTP_SMILES[sequence]
    
    # Try adding 'd' prefix for DNA
    if f"d{sequence}" in NTP_SMILES:
        return NTP_SMILES[f"d{sequence}"]
    
    raise HTTPException(
        status_code=400,
        detail=f"Unknown NTP: {sequence}. Valid options: {', '.join(NTP_SMILES.keys())}"
    )


@router.post("/convert", response_model=SmilesResponse)
async def convert_sequence_to_smiles(request: SequenceToSmilesRequest):
    """
    Convert a sequence to SMILES string.
    
    - **peptide**: Short amino acid sequence (1-50 aa) → SMILES via p2smi
    - **dna**: Short DNA sequence (1-15 nt) → SMILES for nucleosides
    - **rna**: Short RNA sequence (1-15 nt) → SMILES for ribonucleosides
    - **ntp**: Single NTP name (e.g., 'dATP', 'ATP') → SMILES
    """
    sequence = request.sequence.strip()
    seq_type = request.sequence_type
    
    notes = None
    
    if seq_type == 'peptide':
        smiles = peptide_to_smiles(sequence)
    elif seq_type == 'dna':
        smiles = dna_to_smiles(sequence)
        if len(sequence) > 1:
            notes = "Multiple nucleosides returned as dot-separated SMILES (separate molecules)"
    elif seq_type == 'rna':
        smiles = rna_to_smiles(sequence)
        if len(sequence) > 1:
            notes = "Multiple ribonucleosides returned as dot-separated SMILES (separate molecules)"
    elif seq_type == 'ntp':
        smiles = ntp_to_smiles(sequence)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown sequence type: {seq_type}")
    
    return SmilesResponse(
        smiles=smiles,
        sequence=sequence,
        sequence_type=seq_type,
        length=len(sequence),
        notes=notes
    )


@router.get("/ntp-library")
async def get_ntp_library():
    """Get all available NTP SMILES strings."""
    return {
        "dna_ntps": {
            "dATP": {"smiles": NTP_SMILES["dATP"], "name": "Deoxyadenosine triphosphate"},
            "dTTP": {"smiles": NTP_SMILES["dTTP"], "name": "Deoxythymidine triphosphate"},
            "dGTP": {"smiles": NTP_SMILES["dGTP"], "name": "Deoxyguanosine triphosphate"},
            "dCTP": {"smiles": NTP_SMILES["dCTP"], "name": "Deoxycytidine triphosphate"},
        },
        "rna_ntps": {
            "ATP": {"smiles": NTP_SMILES["ATP"], "name": "Adenosine triphosphate"},
            "UTP": {"smiles": NTP_SMILES["UTP"], "name": "Uridine triphosphate"},
            "GTP": {"smiles": NTP_SMILES["GTP"], "name": "Guanosine triphosphate"},
            "CTP": {"smiles": NTP_SMILES["CTP"], "name": "Cytidine triphosphate"},
        }
    }
