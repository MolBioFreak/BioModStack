"""Thermodynamic hairpin, self-dimer and primer-pair dimer analysis (informational).

Engine: ViennaRNA (RNAfold / RNAcofold) with its built-in Mathews 2004 DNA
parameter set. Hairpin = lowest-energy fold of one primer. Self-dimer and pair
dimer = lowest-energy intermolecular duplex (no pairs inside either strand).
Results are predictions, never gates: flags only describe how negative a
delta-G is. See docs/scientific-sources/primer-thermodynamics-sources.md.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

WORKER = Path(__file__).resolve().with_name("primer_thermo_worker.py")

YELLOW_DG = -5.0
ORANGE_DG = -10.0
THREE_PRIME_WINDOW = 4
MAX_PRIMER_LENGTH = 120
MAX_PRIMERS = 100
MAX_PAIRS = 400  # request-size bound (each pair is one RNAcofold run), not a primer gate
# ViennaRNA's salt correction is defined for monovalent salt in this range.
SALT_MIN_M = 0.01
SALT_MAX_M = 1.021

ENGINE = "ViennaRNA (RNAfold/RNAcofold), built-in DNA Mathews 2004 parameters"
CITATIONS = [
    "Lorenz R. et al. ViennaRNA Package 2.0. Algorithms Mol Biol 2011;6:26. doi:10.1186/1748-7188-6-26",
    "Bernhart S.H. et al. Partition function and base pairing probabilities of RNA heterodimers. "
    "Algorithms Mol Biol 2006;1:3. doi:10.1186/1748-7188-1-3",
    "Yao H.-T. et al. Mono-valent salt corrections for RNA secondary structures in the ViennaRNA package. "
    "Algorithms Mol Biol 2023;18. doi:10.1186/s13015-023-00236-0",
    "von Ahsen N. et al. Oligonucleotide melting temperatures under PCR conditions: nearest-neighbor "
    "corrections for Mg2+, deoxynucleotide triphosphate, and dimethyl sulfoxide concentrations. "
    "Clin Chem 2001;47:1956-1961. doi:10.1093/clinchem/47.11.1956",
]
CREDIT = (
    "ViennaRNA, (c) Institute for Theoretical Chemistry, University of Vienna; "
    "DNA Mathews 2004 energy parameters as built into ViennaRNA."
)
CAVEATS = [
    "Predictions from a nearest-neighbor model, not measurements; other tools (for example IDT OligoAnalyzer) use different parameters and will differ.",
    "Hairpin: lowest-energy fold of one primer. Self-dimer and pair dimer: lowest-energy intermolecular duplex, with no pairs inside either strand.",
    "Salt: Mg2+ and dNTPs are converted to a monovalent-equivalent concentration (von Ahsen 2001 form) and applied through ViennaRNA's monovalent salt correction, which was published for RNA. For DNA duplexes it is weaker than the SantaLucia 1998 correction, so values at PCR salt can read more negative than in other DNA tools.",
    "Hairpin and dimer delta-G are reported as 0 when no structure forms; positive values are not shown.",
]

Severity = Literal["none", "yellow", "orange"]


def severity_for(dg: Optional[float]) -> Severity:
    if dg is None:
        return "none"
    if dg <= ORANGE_DG:
        return "orange"
    if dg <= YELLOW_DG:
        return "yellow"
    return "none"


class PrimerThermoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temperature_c: float = Field(default=37.0, ge=0, le=100, allow_inf_nan=False)
    na_mM: float = Field(default=50.0, ge=0, allow_inf_nan=False)
    k_mM: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    tris_mM: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    mg_mM: float = Field(default=1.5, ge=0, allow_inf_nan=False)
    dntps_mM: float = Field(default=0.6, ge=0, allow_inf_nan=False)


class PrimerThermoPrimer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Optional[str] = None
    name: Optional[str] = None
    sequence: str


class PrimerThermoPair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left: int = Field(ge=0, description="Index into primers")
    right: int = Field(ge=0, description="Index into primers")


class PrimerThermoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["primer-thermo-v1"] = "primer-thermo-v1"
    primers: List[PrimerThermoPrimer] = Field(min_length=1)
    pairs: List[PrimerThermoPair] = Field(default_factory=list)
    settings: PrimerThermoSettings = Field(default_factory=PrimerThermoSettings)


class StructureResult(BaseModel):
    dg_kcal_mol: float
    severity: Severity
    structure: str
    three_prime_paired: bool


class PrimerThermoEntry(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    sequence: str
    hairpin: Optional[StructureResult] = None
    self_dimer: Optional[StructureResult] = None
    warnings: List[str] = Field(default_factory=list)


class PrimerThermoPairResult(StructureResult):
    left: int
    right: int
    left_name: Optional[str] = None
    right_name: Optional[str] = None


class PrimerThermoResponse(BaseModel):
    schema_version: Literal["primer-thermo-result-v1"] = "primer-thermo-result-v1"
    engine: str = ENGINE
    engine_version: str
    credit: str = CREDIT
    citations: List[str] = Field(default_factory=lambda: list(CITATIONS))
    caveats: List[str] = Field(default_factory=lambda: list(CAVEATS))
    thresholds_kcal_mol: Dict[str, float] = Field(
        default_factory=lambda: {"yellow_at_or_below": YELLOW_DG, "orange_at_or_below": ORANGE_DG}
    )
    temperature_c: float
    monovalent_equivalent_mM: float
    salt_note: Optional[str] = None
    primers: List[PrimerThermoEntry]
    pairs: List[PrimerThermoPairResult]


class PrimerThermoError(ValueError):
    pass


def monovalent_equivalent_mM(settings: PrimerThermoSettings) -> float:
    """Monovalent-equivalent salt (mM): Na + K + Tris/2 + 120*sqrt(Mg - dNTP)."""
    free_mg = max(settings.mg_mM - settings.dntps_mM, 0.0)
    return settings.na_mM + settings.k_mM + settings.tris_mM / 2.0 + 120.0 * math.sqrt(free_mg)


def _clean(sequence: str) -> str:
    seq = "".join(sequence.upper().split()).replace("U", "T")
    if not seq:
        raise PrimerThermoError("empty sequence")
    bad = sorted(set(seq) - set("ACGT"))
    if bad:
        raise PrimerThermoError("only A/C/G/T supported; found " + ", ".join(bad))
    if len(seq) > MAX_PRIMER_LENGTH:
        raise PrimerThermoError(f"longer than {MAX_PRIMER_LENGTH} nt")
    return seq


def analyze_primer_thermo(request: PrimerThermoRequest) -> PrimerThermoResponse:
    if len(request.primers) > MAX_PRIMERS:
        raise PrimerThermoError(f"at most {MAX_PRIMERS} primers per request")
    if len(request.pairs) > MAX_PAIRS:
        raise PrimerThermoError(f"at most {MAX_PAIRS} pairs per request")
    for pair in request.pairs:
        if pair.left >= len(request.primers) or pair.right >= len(request.primers):
            raise PrimerThermoError("pair index outside primers")

    mv_mM = monovalent_equivalent_mM(request.settings)
    salt_m = mv_mM / 1000.0
    salt_note = None
    if salt_m < SALT_MIN_M or salt_m > SALT_MAX_M:
        clamped = min(max(salt_m, SALT_MIN_M), SALT_MAX_M)
        salt_note = (
            f"Monovalent-equivalent salt {mv_mM:.1f} mM is outside ViennaRNA's supported range; "
            f"calculated at {clamped * 1000:.0f} mM instead."
        )
        salt_m = clamped

    entries: List[PrimerThermoEntry] = []
    sequences: Dict[str, str] = {}
    for index, primer in enumerate(request.primers):
        entry = PrimerThermoEntry(id=primer.id, name=primer.name, sequence=primer.sequence)
        try:
            cleaned = _clean(primer.sequence)
            entry.sequence = cleaned
            sequences[str(index)] = cleaned
        except PrimerThermoError as exc:
            entry.warnings.append(f"not analyzed: {exc}")
        entries.append(entry)

    pair_keys = [
        (str(p.left), str(p.right))
        for p in request.pairs
        if str(p.left) in sequences and str(p.right) in sequences
    ]
    payload = {
        "temperature_c": request.settings.temperature_c,
        "salt_molar": salt_m,
        "three_prime_window": THREE_PRIME_WINDOW,
        "sequences": sequences,
        "pairs": pair_keys,
    }
    proc = subprocess.run(
        [sys.executable, "-I", str(WORKER)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("primer thermodynamics worker failed: " + proc.stderr.strip()[-400:])
    raw = json.loads(proc.stdout)

    def build(item: dict) -> dict:
        return {
            "dg_kcal_mol": item["dg_kcal_mol"],
            "severity": severity_for(item["dg_kcal_mol"]),
            "structure": item["structure"],
            "three_prime_paired": item["three_prime_paired"],
        }

    for key, hairpin in raw["hairpins"].items():
        entries[int(key)].hairpin = StructureResult(**build(hairpin))
    for key, dimer in raw["self_dimers"].items():
        entries[int(key)].self_dimer = StructureResult(**build(dimer))
    pairs = []
    for item in raw["pairs"]:
        left, right = int(item["left"]), int(item["right"])
        pairs.append(
            PrimerThermoPairResult(
                **build(item),
                left=left,
                right=right,
                left_name=entries[left].name,
                right_name=entries[right].name,
            )
        )
    return PrimerThermoResponse(
        engine_version=raw["version"],
        temperature_c=request.settings.temperature_c,
        monovalent_equivalent_mM=round(salt_m * 1000.0, 1),
        salt_note=salt_note,
        primers=entries,
        pairs=pairs,
    )
