"""DISCO 82b594f native settings, input and output grammar (stdlib-only).

Omitted settings inherit the selected native config. BMS historical defaults are
applied only at the legacy request boundary, never inferred from molecular task.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import re
from typing import Literal, TypedDict

NATIVE_REVISION = "82b594f838eb61dd8c78ae3a403ccb8f00cf7abf"


class NoisyGuidance(TypedDict, total=False):
    enabled: bool
    guide_struct: bool
    omega_struct: float
    uncond_seq_time_mode: Literal["fixed", "offset", "proportional"]
    uncond_seq_time: float
    guide_seq: bool
    omega_seq: float
    uncond_struct_time_mode: Literal["fixed", "offset", "proportional"]
    uncond_struct_time: float
    guidance_start_frac: float
    guidance_end_frac: float
    rescale_phi: float
    omega_schedule: Literal["constant", "linear_decay"]


class SequenceSampling(TypedDict, total=False):
    score_type: Literal["confidence", "random"]
    logits_temp: float
    score_temp: float
    mask_stochasticity_strength: float
    planner_name: Literal[
        "esm2_8M", "esm2_35M", "esm2_150M", "esm2_650M", "esm2_3B", "esm2_15B",
        "dplm_150M", "dplm_650M", "dplm_3B", "evoflow_650M",
        "evoflow_650M_Base", "evoflow_650M_calibrated",
    ] | None
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


# Read against the adopted cogen inference loop, PathPlanningSequenceSampling,
# noise scheduler and runner/inference.py. No constructor/reference defaults are
# advertised here as resolved experiment/effort values.
_SETTINGS_HELP = {
    "experiment": ("Experiment profile", "experiment", "Select the native designable or diverse configuration group."),
    "effort": ("Sampling effort", "effort", "Select native fast or max configuration; explicit leaf overrides are applied afterward."),
    "use_deepspeed_evo_attention": ("DeepSpeed attention", "use_deepspeed_evo_attention", "Select the native DeepSpeed Evoformer attention implementation."),
    "seeds": ("Random seeds", "seeds", "Explicit integer seed or ordered seed list. A nonempty list or scalar seed takes precedence over the seed count."),
    "num_inference_seeds": ("Seed count", "num_inference_seeds", "Generate integer seeds from zero through count minus one when no explicit seeds are supplied; null leaves the native seeds list in effect."),
    "N_step": ("Diffusion steps", "sample_diffusion.N_step", "Number of structural diffusion integration steps."),
    "N_cycle": ("Trunk cycles", "model.N_cycle", "Number of native model trunk recycling cycles."),
    "noise_scale_lambda": ("Structural noise multiplier", "sample_diffusion.noise_scale_lambda", "Dimensionless multiplier of structural Gaussian noise during initialization and stochastic updates."),
    "gamma0": ("Structural churn", "sample_diffusion.gamma0", "Dimensionless noise-increase factor used above the native gamma_min threshold."),
    "rho": ("Noise-schedule exponent", "inference_noise_scheduler.rho", "Dimensionless exponent shaping interpolation between the native minimum and maximum structural noise levels."),
    "n_seq_duplicates_per_structure": ("Sequences per structure", "n_seq_duplicates_per_structure", "Number of sequence copies sampled with a shared structure."),
    "need_atom_confidence": ("Atom confidence", "need_atom_confidence", "Request the native atom-confidence calculation."),
    "deterministic": ("Deterministic kernels", "deterministic", "Request native deterministic execution settings; this is not a cross-device reproducibility guarantee."),
    "output_format": ("Output format", "output_format", "Null uses the native general writer; unconditional_monomer_protein uses the monomer protein writer."),
}
_GUIDANCE_HELP = {
    "enabled": ("Enable cross-modal guidance", "Enable noisy cross-modal guidance; native inverse folding bypasses it."),
    "guide_struct": ("Guide structure", "Use the more-noised sequence branch to guide structural denoising."),
    "omega_struct": ("Structure guidance weight", "Dimensionless structural guidance weight; one leaves the conditional prediction unchanged."),
    "guide_seq": ("Guide sequence", "Use the more-noised structure branch to guide sequence predictions."),
    "omega_seq": ("Sequence guidance weight", "Dimensionless sequence guidance weight; one leaves the conditional prediction unchanged."),
    "uncond_seq_time_mode": ("Sequence noise mode", "fixed uses an absolute noise fraction; offset shifts scheduler time; proportional multiplies current noise. Sequence remasking uses the configured fraction for fixed/offset and multiplies the current masked fraction for proportional."),
    "uncond_seq_time": ("Sequence noise value", "Noise fraction for fixed, scheduler-time offset for offset, or dimensionless multiplier for proportional. Also controls the guidance branch's sequence remasking fraction."),
    "uncond_struct_time_mode": ("Structure noise mode", "fixed uses an absolute noise fraction; offset shifts scheduler time toward noise; proportional multiplies current sigma."),
    "uncond_struct_time": ("Structure noise value", "Noise fraction (zero clean, one fully noised) for fixed; scheduler-time offset for offset; dimensionless sigma multiplier for proportional."),
    "guidance_start_frac": ("Guidance start", "Fraction of the native noise schedule at which guidance begins."),
    "guidance_end_frac": ("Guidance end", "Fraction of the native noise schedule at which guidance ends."),
    "rescale_phi": ("Guidance rescaling mix", "Dimensionless interpolation weight between standard-deviation-rescaled and unrescaled guided structural predictions; zero skips rescaling."),
    "omega_schedule": ("Guidance weight schedule", "Keep guidance weights constant or decay them linearly toward one."),
}
_SEQUENCE_HELP = {
    "score_type": ("Mask ranking", "Rank mask candidates by sampled-token log probability or random log scores."),
    "logits_temp": ("Token temperature", "Dimensionless temperature applied to amino-acid logits before categorical sampling."),
    "score_temp": ("Score temperature (retained)", "The adopted path-planning constructor stores this value but its step does not read it; no sampling effect is claimed."),
    "mask_stochasticity_strength": ("Unmasked-position score scale", "Dimensionless multiplier of ranking scores for already unmasked positions before selecting the lowest-scored positions to mask."),
    "planner_name": ("Sequence planner", "Optional native registered masked language model for planning logits; null disables the planner."),
    "should_ensure_unmasked_stay": ("Reuse unmasked confidence", "For confidence ranking, reuse cached likelihoods at already unmasked positions."),
    "allow_remasking": ("Remasking diagnostic flag", "The adopted step uses this flag in its remasking diagnostic; it does not gate the masking update. No change in sampling is claimed."),
    "switch_temp": ("Late temperature switch", "Use the configured token temperature while sequence time is above 0.2, then the native 0.1 temperature."),
    "entropy_adaptive_temp": ("Entropy-adaptive temperature", "Scale token temperature per position using entropy of the raw amino-acid logits."),
    "entropy_adaptive_beta": ("Entropy temperature strength", "Dimensionless coefficient in 1 + beta * time^power * (log(20)-entropy)/log(20); used when adaptive temperature is enabled."),
    "entropy_adaptive_anneal_power": ("Entropy annealing power", "Exponent of sequence time multiplying the adaptive-temperature coefficient."),
    "power": ("Sequence noise exponent", "Dimensionless power of the polynomial sequence-noise schedule."),
}


def native_profile_reference() -> dict:
    """Bounded pinned YAML observations, not effective execution settings."""
    return json.loads(Path(__file__).with_name('disco_profile_reference.json').read_text())


def settings_schema() -> dict:
    """Enrich the existing typed schema without changing accepted values."""
    from pydantic import TypeAdapter
    schema = TypeAdapter(NativeSettings).json_schema()

    def annotate(leaf, title, native, effect, applicability):
        leaf.update(title=title, description=f"{effect} {applicability} Native: {native}.")
        leaf.update({"x-native-key": native, "x-applicability": applicability,
                     "x-default-source": "selected native composition; not resolved by discovery"})

    for key, (title, native, effect) in _SETTINGS_HELP.items():
        annotate(schema['properties'][key], title, native, effect, "DISCO generation.")
    for group, type_name, definitions, prefix, applicability in (
        ('noisy_guidance', 'NoisyGuidance', _GUIDANCE_HELP, 'sample_diffusion.noisy_guidance', 'Active only when noisy guidance is enabled and the native guidance window/branch applies.'),
        ('sequence_sampling', 'SequenceSampling', _SEQUENCE_HELP, 'sequence_sampling_strategy', 'Native path-planning sequence sampling.'),
    ):
        schema['properties'][group]['title'] = 'Cross-modal guidance' if group == 'noisy_guidance' else 'Sequence sampling'
        for key, (title, effect) in definitions.items():
            native = prefix + ('.sequence_noise_scheduler.power' if key == 'power' else '.' + key)
            annotate(schema['$defs'][type_name]['properties'][key], title, native, effect, applicability)
    for leaf, label in (
        (schema['properties']['num_inference_seeds'], 'Use native seeds list (null)'),
        (schema['properties']['output_format'], 'General writer (null)'),
        (schema['$defs']['SequenceSampling']['properties']['planner_name'], 'No sequence planner (null)'),
    ):
        leaf['x-null-label'] = label
    return schema


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
