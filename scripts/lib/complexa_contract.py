"""Complexa 1.1 source-owned scientific request (NGC checkpoint release 1.0).

No arbitrary Hydra targets, runtime paths, or model aliases are accepted here.
Optional groups inherit the selected official profile; explicit null reward disables it.
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL_ID = "complexa"
SOURCE_REVISION = "acf55a89a9a7206d5ce9abc0bf2246c3b3829f92"
RESULT_CONTRACT = "complexa_v1"
TASKS = ("protein_binder", "ligand_binder", "AME", "monomer_motif_indexed", "monomer_motif_unindexed")
PROFILES = {
    "protein_binder": ("search_binder_local_pipeline", "complexa", "binder/binder"),
    "ligand_binder": ("search_ligand_binder_local_pipeline", "complexa_ligand", "ligand_binder/ligand_binder"),
    "AME": ("search_ame_local_pipeline", "complexa_ame", "ame/ame"),
    "monomer_motif_indexed": ("search_motif_local_pipeline", "complexa_ame", "motif/idx_motif"),
    "monomer_motif_unindexed": ("search_motif_local_pipeline", "complexa_ame", "motif/uidx_motif"),
}

class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")

class AtomSelection(Closed):
    chain: str = Field(min_length=1, max_length=1)
    residue: int
    insertion_code: str = Field(default="", max_length=1)
    atoms: list[str] = Field(min_length=1)

class Source(Closed):
    # Bound by existing StructureSourceRequest/portable-input owner, never a URL/path.
    document_id: str = Field(min_length=1)
    input_spec: str | None = None
    target_hotspots: list[int] | None = None
    binder_center: tuple[float, float, float] | None = None
    pdb_id: str | None = None
    ligand: str | list[str] | None = None
    ligand_only: bool = False
    SMILES: str | None = None
    use_bonds_from_file: bool = True
    motif_atoms: list[AtomSelection] = Field(default_factory=list)
    ligand_atoms: list[AtomSelection] = Field(default_factory=list)
    contig_string: str | None = None
    atom_selection_mode: Literal["ca", "bb3o", "all_atom", "tip_atoms"] = "all_atom"
    motif_only: bool = False
    segment_order: str | None = None
    motif_csv_slot: str | None = None
    padding: bool | None = None  # omitted/null inherits indexed/unindexed profile
    binder_gen_only: bool | None = None  # omitted/null retains native task profile

class BestOfN(Closed):
    replicas: int = Field(default=2, ge=1)

class Beam(Closed):
    n_branch: int = Field(default=4, ge=1)
    beam_width: int = Field(default=4, ge=1)
    keep_lookahead_samples: bool = True
    save_intermediate_states: bool = False

class FK(Closed):
    n_branch: int = Field(default=4, ge=1)
    beam_width: int = Field(default=4, ge=1)
    temperature: float = Field(default=0.1, gt=0)
    keep_lookahead_samples: bool = True
    save_intermediate_states: bool = False

class MCTS(Closed):
    n_simulations: int = Field(default=20, ge=1)
    exploration_prob: float = Field(default=0.5, ge=0, le=1)
    exploration_constant: float = 1.0
    keep_lookahead_samples: bool = True

class Search(Closed):
    algorithm: Literal["single-pass", "best-of-n", "beam-search", "fk-steering", "mcts"]
    max_batch_size: int | None = Field(default=None, ge=1)
    reward_threshold: float | None = None
    step_checkpoints: list[int] = Field(default_factory=lambda: [0, 100, 200, 300, 400])
    best_of_n: BestOfN = Field(default_factory=BestOfN)
    beam_search: Beam = Field(default_factory=Beam)
    fk_steering: FK = Field(default_factory=FK)
    mcts: MCTS = Field(default_factory=MCTS)

class LossWeights(Closed):
    pae: float = 0.4
    plddt: float = 0.1
    i_pae: float = 0.1
    con: float = 1.0
    i_con: float = 1.0
    dgram_cce: float = 0.0
    rg: float = 0.3
    i_ptm: float = 0.05
    helix_binder: float = -0.3

class Refinement(Closed):
    algorithm: Literal["sequence_hallucination"] | None = None
    refine_targets: Literal["final", "all"] = "final"
    save_pre_refinement: Literal["none", "all", "final"] = "none"
    enable_soft_optimization: bool = False
    enable_greedy_optimization: bool = True
    n_temp_iters: int = 45
    n_hard_iters: int = 5
    n_recycles: int = 3
    n_greedy_iters: int = 15
    greedy_percentage: float = 1
    loss_weights: LossWeights | None = None

class Schedule(Closed):
    mode: Literal["uniform", "power", "cos_sch_v_snr", "loglinear", "edm", "log"]
    p: float = 2.0

class GT(Closed):
    mode: Literal["1-t/t", "tan", "1/t", "1/t2", "1/t1p5", "2/t", "2/t2", "2/t1p5"]
    p: float = 1.0
    clamp_val: float | None = None

class Simulation(Closed):
    sampling_mode: Literal["vf", "sc", "vf_ss", "vf_ss_sc_sn", "vf_tsr"] = "sc"
    sc_scale_noise: float = 0.1
    sc_scale_score: float = 1.0
    t_lim_ode: float = 0.98
    t_lim_ode_below: float = 0.02
    tsr_k: float = 1.0
    tsr_sigma: float = 1.0
    center_every_step: bool = False

class Component(Closed):
    schedule: Schedule
    gt: GT
    simulation_step_params: Simulation = Field(default_factory=Simulation)

class SamplingModel(Closed):
    bb_ca: Component = Field(default_factory=lambda: Component(schedule=Schedule(mode="log"), gt=GT(mode="1/t")))
    local_latents: Component = Field(default_factory=lambda: Component(schedule=Schedule(mode="power"), gt=GT(mode="tan")))

class SamplingArgs(Closed):
    nsteps: int = Field(default=400, ge=1)
    self_cond: bool = True
    guidance_w: float = 1.0
    ag_ratio: float = 0.0
    save_trajectory_every: int = Field(default=0, ge=0)
    fold_cond: bool = False

class AF2Weights(Closed):
    con: float = 0.0
    i_pae: float = -1.0
    plddt: float = 0.0
    dgram_cce: float = 0.0
    min_ipae: float = 0.0
    min_ipsae: float = 0.0
    avg_ipsae: float = 0.0
    max_ipsae: float = 0.0
    min_ipsae_10: float = 0.0
    max_ipsae_10: float = 0.0
    avg_ipsae_10: float = 0.0

class AF2(Closed):
    protocol: Literal["binder", "hallucination"] = "binder"
    model_nums: list[int] | None = None
    use_multimer: bool = True
    num_recycles: int = 3
    use_initial_guess: bool = True
    use_initial_atom_pos: bool = False
    seed: int = 0
    reward_weights: AF2Weights = Field(default_factory=AF2Weights)

class RF3Weights(Closed):
    min_ipAE: float = -1.0
    plddt: float = 0.0
    ipAE: float = 0.0
    mean_min_ipAE: float = 0.0
    mean_ipAE: float = 0.0
    min_mean_ipAE: float = 0.0
    pAE: float = 0.0
    ipTM: float = 0.0
    pTM: float = 0.0
    ranking_score: float = 0.0
    has_clash: float = 0.0
    min_ipSAE: float = 0.0
    max_ipSAE: float = 0.0
    avg_ipSAE: float = 0.0

class RF3(Closed):
    normalize_pae: bool = True
    verbose: bool = False
    reward_weights: RF3Weights = Field(default_factory=RF3Weights)

class TMOL(Closed):
    enable_hbond: bool = True
    enable_elec: bool = False
    hbond_weight: float = 1.0
    elec_weight: float = 1.0
    reward_type: Literal["energy_sum", "interaction_count"] = "energy_sum"
    energy_threshold: float = -0.6
    normalize_by_interface_size: bool = False
    structure_source: Literal["af2folding", "rf3folding"] | None = None

BioMetric = Literal["surface_hydrophobicity", "interface_sc", "interface_dSASA", "interface_fraction", "interface_hydrophobicity", "interface_nres"]

class Bioinformatics(Closed):
    reward_weights: dict[BioMetric, float]
    reward_thresholds: dict[BioMetric, float | None] = Field(default_factory=dict)
    reward_threshold_modes: dict[BioMetric, Literal["min", "max"]] = Field(default_factory=dict)
    structure_source: Literal["af2folding", "rf3folding"] | None = None

class Reward(Closed):
    af2folding: AF2 | None = None
    rf3folding: RF3 | None = None
    tmol: TMOL | None = None
    bioinformatics: Bioinformatics | None = None
    af2_weight: float = 1.0
    rf3_weight: float = 1.0
    tmol_weight: float = 1.0
    bioinformatics_weight: float = 1.0

class InterfaceMetrics(Closed):
    bioinformatics: bool = False
    tmol: bool = False

class Ranking(Closed):
    scale: float = 1.0
    direction: Literal["minimize", "maximize"] = "minimize"

class Evaluation(Closed):
    # Only supplied fields override the mode-specific native defaults.
    sequence_types: list[Literal["self", "mpnn", "mpnn_fixed"]] | None = None
    binder_folding_method: Literal["colabdesign", "rf3_latest"] | None = None
    esm_model: str | None = None
    designability_folding_models: list[Literal["esmfold", "colabfold"]] | None = None
    codesignability_folding_models: list[Literal["esmfold", "colabfold"]] | None = None
    compute_motif_rmsd: bool | None = None
    compute_motif_designability: bool | None = None
    compute_motif_codesignability: bool | None = None
    pmpnn_sampling_temp: float | None = None
    inverse_folding_model: Literal["protein_mpnn", "soluble_mpnn", "ligand_mpnn"] | None = None
    num_redesign_seqs: int | None = None
    interface_cutoff: float | None = None
    compute_binder_metrics: bool | None = None
    compute_motif_binder_metrics: bool | None = None
    compute_motif_metrics: bool | None = None
    compute_esm_metrics: bool | None = None
    compute_pre_refolding_metrics: bool | None = None
    pre_refolding: InterfaceMetrics | None = None
    compute_refolded_structure_metrics: bool | None = None
    refolded: InterfaceMetrics | None = None
    compute_monomer_metrics: bool | None = None
    monomer_folding_models: list[Literal["esmfold", "colabfold"]] | None = None
    compute_designability: bool | None = None
    designability_num_seq: int | None = None
    designability_modes: list[Literal["ca", "bb3o", "all_atom"]] | None = None
    compute_codesignability: bool | None = None
    codesignability_modes: list[Literal["ca", "bb3o", "all_atom"]] | None = None
    motif_rmsd_modes: list[Literal["ca", "bb3o", "all_atom"]] | None = None
    compute_co_sequence_recovery: bool | None = None
    compute_ss: bool | None = None
    compute_novelty_pdb: bool | None = None
    compute_novelty_afdb: bool | None = None
    compute_novelty_afdb_rep_v4: bool | None = None
    compute_novelty_afdb_rep_v4_geniefilters_maxlen512: bool | None = None
    keep_folding_outputs: bool | None = None
    ranking_criteria: dict[str, Ranking] | None = None
    motif_ranking_criteria: dict[str, Ranking] | None = None

class Filter(Closed):
    filter_samples_limit: int | None = 1000
    delete_non_top_n_samples: bool = False
    dedup_sequence: bool = True
    reward_threshold: float | None = None

class Analysis(Closed):
    limit: int | None = None
    analysis_modes: list[Literal["binder", "monomer", "motif", "motif_binder"]] | None = None
    compute_diversity: bool | None = None
    compute_mmseqs_diversity: bool | None = None

class Request(Closed):
    schema_version: Literal[1] = 1
    task: Literal["protein_binder", "ligand_binder", "AME", "monomer_motif_indexed", "monomer_motif_unindexed"]
    operation: Literal["generate", "design", "evaluate", "analysis"] = "generate"
    task_name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    source: Source | None = None
    evaluation_slots: list[str] = Field(default_factory=list,
        description="Exact native PDB documents to evaluate; separate from target/motif source.")
    seed: int = 5
    nsamples: int | None = Field(default=None, ge=1)
    min_length: int | None = Field(default=None, ge=1)
    max_length: int | None = Field(default=None, ge=1)
    batch_size: int | None = Field(default=None, ge=1)
    nrepeat_per_sample: int = Field(default=1, ge=1)
    n_recycle: int = Field(default=0, ge=0)
    args: SamplingArgs = Field(default_factory=SamplingArgs)
    model: SamplingModel = Field(default_factory=SamplingModel)
    search: Search | None = None
    refinement: Refinement | None = None
    reward: Reward | None | Literal["profile"] = "profile"
    evaluation: Evaluation | None = None
    filtering: Filter | None = None
    analysis: Analysis | None = None

    @model_validator(mode="after")
    def native_shapes(self):
        if self.min_length is not None and self.max_length is not None and self.min_length > self.max_length:
            raise ValueError("min_length exceeds max_length")
        return self


def normalize_request(params: dict) -> dict:
    return Request.model_validate(params).model_dump(mode="json", exclude_unset=True)


def parameter_schema() -> dict:
    return Request.model_json_schema()
