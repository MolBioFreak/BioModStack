"""DISCO 82b594f native settings, input and output grammar (stdlib-only).

Omitted settings inherit the selected native config. BMS historical defaults are
applied only at the legacy request boundary, never inferred from molecular task.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Literal, TypedDict

NATIVE_REVISION = "82b594f838eb61dd8c78ae3a403ccb8f00cf7abf"


class NoisyGuidance(TypedDict, total=False):
    enabled: bool
    guide_struct: bool
    omega_struct: float
    uncond_seq_time_mode: str
    uncond_seq_time: float
    guide_seq: bool
    omega_seq: float
    uncond_struct_time_mode: str
    uncond_struct_time: float
    guidance_start_frac: float
    guidance_end_frac: float
    rescale_phi: float
    omega_schedule: str


class SequenceSampling(TypedDict, total=False):
    score_type: Literal["confidence", "random"]
    logits_temp: float
    score_temp: float
    mask_stochasticity_strength: float
    planner_name: str | None
    should_ensure_unmasked_stay: bool
    allow_remasking: bool
    switch_temp: bool
    entropy_adaptive_temp: bool
    entropy_adaptive_beta: float
    entropy_adaptive_anneal_power: float
    # Native polynomial sequence_noise_scheduler.power
    power: float


class NativeSettings(TypedDict, total=False):
    experiment: Literal["designable", "diverse"]
    effort: Literal["fast", "max"]
    use_deepspeed_evo_attention: bool
    seeds: list[int] | int
    num_inference_seeds: int | None
    N_step: int
    N_cycle: int
    noisy_guidance: NoisyGuidance
    sequence_sampling: SequenceSampling
    noise_scale_lambda: float
    gamma0: float
    rho: float
    n_seq_duplicates_per_structure: int
    need_atom_confidence: bool
    deterministic: bool
    output_format: Literal["unconditional_monomer_protein"] | None


class Polymer(TypedDict):
    sequence: str
    count: int


class Ligand(TypedDict):
    # SMILES, CCD_<code>, or FILE_<portable-path>; no chemistry reconstruction.
    ligand: str
    count: int


class Ion(TypedDict):
    ion: str
    count: int


class ProteinEntity(TypedDict):
    proteinChain: Polymer


class DNAEntity(TypedDict):
    dnaSequence: Polymer


class RNAEntity(TypedDict):
    rnaSequence: Polymer


class LigandEntity(TypedDict):
    ligand: Ligand


class IonEntity(TypedDict):
    ion: Ion


Entity = ProteinEntity | DNAEntity | RNAEntity | LigandEntity | IonEntity


class CovalentBond(TypedDict):
    left_entity: int
    left_position: int
    left_atom: str | int
    right_entity: int
    right_position: int
    right_atom: str | int


class CopyCovalentBond(CovalentBond, total=False):
    left_copy: int
    right_copy: int


class NativeJob(TypedDict):
    name: str
    sequences: list[Entity]


class BondedNativeJob(NativeJob, total=False):
    covalent_bonds: list[CopyCovalentBond]


def build_native_jobs(jobs: list[BondedNativeJob]) -> list[dict]:
    """Typed authoring uses native entity order/copy/1-based bond endpoints.

    Protein '-' masks are sequence conditioning, not structural motifs. Explicit
    DNA/RNA rows are strands: do not synthesize reverse complements here. Legacy
    DNA compilation stays at its existing preparer. Native enforces its own
    single-protein limitation; this transport adds no admission conditions.
    """
    return [dict(deepcopy(job)) for job in jobs]


def legacy_effective_settings(settings: dict, task: str) -> dict:
    """Replay-only lowering for saved pre-native-contract CAD requests.

    The Job/revision owner must select this only for a historical request, never
    by molecular task alone for a new native request. Preserve old effective
    conditional effort without retaining a hidden coercion in preparation.
    """
    effective = deepcopy(settings)
    if task in {"ligand_conditioned", "dna_conditioned", "rna_conditioned"} and effective.get("effort", "fast") == "fast":
        effective["effort"] = "max"
    return effective


def native_overrides(settings: NativeSettings) -> list[str]:
    """Hydra group selection first; explicit scalar overrides take precedence.

    Empty legacy seeds means count-based seeds; scalar zero and [0] are explicit.
    ++ permits native constructor defaults absent from the selected YAML. No
    numerical defaults are copied from an experiment into the application.
    """
    result = [f"experiment={settings.get('experiment') or 'designable'}",
              f"effort={settings.get('effort') or 'fast'}",
              "use_deepspeed_evo_attention=" + json.dumps(
                  settings.get('use_deepspeed_evo_attention', False))]
    seeds = settings.get("seeds")
    if seeds is not None and seeds != []:
        result += ["seeds=" + json.dumps(seeds, separators=(",", ":")), "num_inference_seeds=null"]
    else:
        count = settings.get("num_inference_seeds", 8)
        result.append("num_inference_seeds=" + json.dumps(count))
    fields = {
        "N_step": "sample_diffusion.N_step", "N_cycle": "model.N_cycle",
        "noise_scale_lambda": "sample_diffusion.noise_scale_lambda",
        "gamma0": "sample_diffusion.gamma0", "rho": "inference_noise_scheduler.rho",
        "n_seq_duplicates_per_structure": "n_seq_duplicates_per_structure",
        "need_atom_confidence": "need_atom_confidence",
        "deterministic": "deterministic", "output_format": "output_format",
    }
    for key, native in fields.items():
        if key in settings:
            result.append(f"++{native}={json.dumps(settings[key])}")
    for key in NoisyGuidance.__annotations__:
        if key in settings.get("noisy_guidance", {}):
            result.append(f"++sample_diffusion.noisy_guidance.{key}=" +
                          json.dumps(settings["noisy_guidance"][key]))
    for key in SequenceSampling.__annotations__:
        if key in settings.get("sequence_sampling", {}):
            native = "sequence_noise_scheduler.power" if key == "power" else key
            result.append(f"++sequence_sampling_strategy.{native}=" +
                          json.dumps(settings["sequence_sampling"][key]))
    return result


_HEADER = re.compile(r">cogen_seq (\d+)")
_CHAIN = re.compile(r"CHAIN (\S+) type=(protein|dna|rna) mode=(design|fixed) seq=(\S*)")
_PROTEIN = re.compile(r"[ACDEFGHIKLMNPQRSTVWYX-]+")


def parse_native_records(text: str) -> list[dict]:
    """Read native records, not alphabet-filtered text.

    The plain writer returns aa_seq WITHOUT a trailing newline; consequently
    adjacent records may be 'ACDE>cogen_seq 1'. Split on actual header grammar.
    Unknown lines remain raw observations, not repaired protein residues.
    """
    headers = list(_HEADER.finditer(text))
    chunks = [(int(m.group(1)), text[m.end():headers[i + 1].start()
              if i + 1 < len(headers) else len(text)],
              text[m.start():headers[i + 1].start() if i + 1 < len(headers) else len(text)])
              for i, m in enumerate(headers)]
    if not headers and text:
        chunks = [(None, text, text)]
    records = []
    for sample, body, raw in chunks:
        chains, ligands, unknown, plain = [], [], [], []
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            chain = _CHAIN.fullmatch(line)
            if chain:
                name, kind, mode, sequence = chain.groups()
                chains.append(dict(chain_id=name, type=kind, mode=mode, sequence=sequence))
            elif line.startswith("ligand_smiles "):
                ligands.append(line[len("ligand_smiles "):])
            elif _PROTEIN.fullmatch(line):
                plain.append(line)
            else:
                unknown.append(line)
        proteins = [c for c in chains if c["type"] == "protein"]
        # No invented chain ID for the native plain single-protein record.
        if plain:
            proteins.append(dict(chain_id=None, type="protein", mode=None, sequence="".join(plain)))
        sequence = proteins[0]["sequence"] if len(proteins) == 1 else None
        records.append(dict(sample_id=sample, protein_sequence=sequence,
                            protein_chains=proteins, chains=chains,
                            ligand_descriptors=ligands, raw_text=raw, unparsed_lines=unknown))
    return records
