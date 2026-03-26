import React, { useEffect, useState } from 'react';

export interface QualitySettings {
    // Local MSA search quality (used by MSA-consuming validators)
    msa_preset: 'maximum' | 'balanced' | 'fast';

    // RFantibody settings (backbone diffusion)
    rfantibody_diffusion_steps: number;
    rfantibody_noise_scale_ca: number;
    rfantibody_noise_scale_frame: number;
    rfantibody_guide_scale: number;
    rfantibody_ckpt_override: string;
    rfantibody_debug_repo_overlay: boolean;

    // Boltz-2 settings (structure validation)
    boltz_sampling_steps: number;
    boltz_recycling_steps: number;
    boltz_num_samples: number;
    boltz_use_potentials: boolean;
    boltz_use_msa: boolean;
    boltz_step_scale: number | null;
    // Boltz-2 affinity prediction (quality feature)
    boltz_predict_affinity: boolean;
    boltz_diffusion_samples_affinity: number;
    boltz_anchor_target: boolean;
    boltz_anchor_strict: boolean;

    // Protenix settings (structure validation)
    protenix_model_weights: string;
    protenix_seeds: string;
    protenix_n_sample: number;
    protenix_n_step: number;
    protenix_n_cycle: number;
    protenix_use_msa: boolean;
    protenix_msa_backend: 'auto' | 'local' | 'colabfold_api';
    protenix_use_template: boolean;
    protenix_anchor_target: boolean;
    protenix_anchor_strict: boolean;
    protenix_enable_cache: boolean;
    protenix_enable_fusion: boolean;
    protenix_auto_oom_retry: boolean;
    protenix_oom_retry_attempts: number;
    colabfold_api_host: string;
    msa_use_gpu: boolean;
    msa_local_db: string;
    msa_cache_dir: string;
    msa_threads: number | null;
    msa_gpu_mode: string;
    msa_gpu_threshold: number;
    msa_preferred_gpus: string;
    msa_excluded_gpus: string;
    msa_gpu_server_mode: string;
    msa_gpu_server_wait_timeout: number;
    msa_gpu_server_db_load_mode: number;
    msa_gpu_server_startup_wait: number;

    // FAMPNN settings (sequence design)
    fampnn_checkpoint: string;
    fampnn_checkpoint_path: string;
    fampnn_temperature: number;
    fampnn_num_steps: number;
    fampnn_psce_threshold: number;
    lock_target_chains: boolean;
    lock_antibody_framework: boolean;

    // PPIFlow stage control (backbone refinement and/or post-sequence maturation)
    run_maturation: boolean;
    ppiflow_stage_mode: PPIFlowStageMode;
    ppiflow_tuning_profile: PPIFlowTuningProfile;
    ppiflow_start_t: number;
    ppiflow_samples_per_target: number;
    ppiflow_retry_limit: number;
    ppiflow_config: string;
    ppiflow_weights_dir: string;
    ppiflow_checkpoint_path: string;
    ppiflow_rotamer_enrichment_enabled: boolean;
    ppiflow_require_anchors: boolean;
    ppiflow_rotamer_shell_cutoff: number;
    ppiflow_objective_mode: PPIFlowObjectiveMode;
    ppiflow_objective_threshold: number;
    maturation_anchor_threshold: number;
    maturation_anchor_distance_cutoff: number;
    maturation_min_improvement: number;
    maturation_redesign_temp: number;
    maturation_redesign_steps: number;
    maturation_design_mode: string;
    maturation_designs_per_job: number;
    maturation_filter_percentile: number;
    maturation_redesign_enabled: boolean;
    maturation_redesign_top_n: number;
    ppiflow_checkpoint: string;
    ppiflow_antigen_chain: string;
    ppiflow_heavy_chain: string;
    ppiflow_light_chain: string;
    ppiflow_backbone_region_mode: PPIFlowRegionMode;
    ppiflow_maturation_region_mode: PPIFlowRegionMode;
    ppiflow_backbone_loop_scope: string;
    ppiflow_maturation_loop_scope: string;

    // Pre-Boltz filtering (saves compute by rejecting low-quality designs before expensive validation)
    fampnn_max_psce: number | null;           // Max avg PSCE score to pass to Boltz (null = no filter)
    fampnn_max_residue_psce: number | null;   // Max per-residue PSCE (catches individual bad residues)

    // ThermoMPNN stability scoring (runs before Boltz when enabled)
    run_thermompnn: boolean;                  // Enable stability scoring before Boltz
    thermompnn_max_ddg: number | null;        // Max ddG to pass (null = score only, no filter)

    // AF2 Backprop CDR refinement (runs after ThermoMPNN, before Boltz when enabled)
    run_af2_backprop: boolean;                // Enable AF2 backprop CDR refinement
    af2_backprop_soft_iters: number;          // Soft optimization iterations (continuous logits)
    af2_backprop_temp_iters: number;          // Temperature annealing iterations
    af2_backprop_hard_iters: number;          // Hard discrete iterations (one-hot)
    af2_backprop_num_recycles: number;        // AF2 recycling iterations (quality vs speed)
    af2_backprop_learning_rate: number;       // Gradient descent step size
    af2_backprop_use_multimer: boolean;       // Use AlphaFold-Multimer (better for complexes)
    af2_backprop_num_models: number;          // Number of AF2 models to ensemble (1-5)
    af2_backprop_loss_plddt: number;          // Weight for pLDDT loss (confidence)
    af2_backprop_loss_pae: number;            // Weight for PAE loss (alignment error)
    af2_backprop_loss_contact: number;        // Weight for interface contact loss

    // Post-Boltz validation filtering (applied after Boltz-2 structure prediction)
    boltz_max_binder_rmsd: number | null;     // Max RMSD (Å) for binder vs design (null = no filter)
    boltz_min_ptm_interface: number | null;   // Min interface pTM score (null = no filter)
}

export type PPIFlowStageMode = 'off' | 'post_rfantibody' | 'post_ppiflow' | 'post_fampnn' | 'both';
export type PPIFlowRegionMode = 'selected_cdrs' | 'all_cdrs' | 'framework_only' | 'all_antibody';
export type PPIFlowObjectiveMode = 'selected_interface' | 'loop_target' | 'loop_epitope' | 'balanced';
export type PPIFlowTuningProfile = 'stage_optimized' | 'manual';

const PRE_SEQUENCE_PPIFLOW_STAGE_MODES = new Set<PPIFlowStageMode>(['post_rfantibody', 'post_ppiflow']);
const POST_SEQUENCE_PPIFLOW_STAGE_MODES = new Set<PPIFlowStageMode>(['post_fampnn']);

const getPpiFlowOptimizationScenario = (stageMode: PPIFlowStageMode): 'pre_sequence' | 'post_sequence' | null => {
    if (PRE_SEQUENCE_PPIFLOW_STAGE_MODES.has(stageMode)) return 'pre_sequence';
    if (POST_SEQUENCE_PPIFLOW_STAGE_MODES.has(stageMode)) return 'post_sequence';
    return null;
};

export const normalizePpiFlowTuningProfile = (raw: unknown): PPIFlowTuningProfile =>
    raw === 'manual' ? 'manual' : 'stage_optimized';

export const getStageOptimizedPpiFlowSettings = (stageMode: PPIFlowStageMode): Partial<QualitySettings> => {
    const scenario = getPpiFlowOptimizationScenario(stageMode);
    if (scenario === 'pre_sequence') {
        return {
            ppiflow_start_t: 0.55,
            ppiflow_samples_per_target: 7,
            ppiflow_require_anchors: false,
            ppiflow_objective_mode: 'loop_epitope',
            ppiflow_objective_threshold: 0,
        };
    }
    if (scenario === 'post_sequence') {
        return {
            ppiflow_start_t: 0.8,
            ppiflow_samples_per_target: 4,
            ppiflow_require_anchors: true,
            ppiflow_objective_mode: 'balanced',
            ppiflow_objective_threshold: 0,
        };
    }
    return {};
};

export const applyPpiFlowStageMode = (
    settings: QualitySettings,
    nextStageMode: PPIFlowStageMode,
): QualitySettings => {
    const nextSettings: QualitySettings = {
        ...settings,
        ppiflow_stage_mode: nextStageMode,
        run_maturation: nextStageMode === 'post_fampnn' || nextStageMode === 'both',
    };
    const tuningProfile = normalizePpiFlowTuningProfile(nextSettings.ppiflow_tuning_profile);
    if (nextStageMode === 'both' && tuningProfile === 'stage_optimized') {
        nextSettings.ppiflow_tuning_profile = 'manual';
        return nextSettings;
    }
    if (tuningProfile === 'stage_optimized') {
        return {
            ...nextSettings,
            ...getStageOptimizedPpiFlowSettings(nextStageMode),
        };
    }
    if (nextStageMode === 'post_ppiflow') {
        nextSettings.ppiflow_require_anchors = false;
    }
    return nextSettings;
};

export const applyPpiFlowTuningProfile = (
    settings: QualitySettings,
    nextProfile: PPIFlowTuningProfile,
): QualitySettings => {
    const normalizedProfile = normalizePpiFlowTuningProfile(nextProfile);
    const stageMode = (settings.ppiflow_stage_mode || (settings.run_maturation ? 'post_fampnn' : 'off')) as PPIFlowStageMode;
    const nextSettings: QualitySettings = {
        ...settings,
        ppiflow_tuning_profile: normalizedProfile,
    };
    if (normalizedProfile !== 'stage_optimized') {
        return nextSettings;
    }
    if (stageMode === 'both') {
        return {
            ...nextSettings,
            ppiflow_tuning_profile: 'manual',
        };
    }
    return {
        ...nextSettings,
        ...getStageOptimizedPpiFlowSettings(stageMode),
    };
};

const normalizeProtenixModel = (model?: string) => {
    if (!model) return 'protenix_base_20250630_v1.0.0';
    if (model === 'protenix_base_20241211_v0.2.1') return 'protenix_base_default_v1.0.0';
    if (model === 'protenix_esm_20241211_v0.2.1') return 'protenix_mini_esm_v0.5.0';
    return model;
};

const FAMPNN_CHECKPOINT_OPTIONS = [
    {
        value: 'fampnn_0_0.pt',
        label: 'FAMPNN (0.0A)',
        description: 'Full PDB dataset, 0.0A noise. Use when you want the strict no-noise checkpoint.',
    },
    {
        value: 'fampnn_0_3.pt',
        label: 'FAMPNN (0.3A)',
        description: 'Full PDB dataset, 0.3A noise. Recommended upstream for sequence design.',
    },
    {
        value: 'fampnn_0_3_cath.pt',
        label: 'FAMPNN (0.3A, CATH)',
        description: 'CATH-trained 0.3A checkpoint. Recommended upstream for mutation scoring rather than primary sequence design.',
    },
] as const;

const DEFAULT_FAMPNN_CHECKPOINT = 'fampnn_0_0.pt';
const DEFAULT_PPIFLOW_CHECKPOINT = 'nanobody';
const DEFAULT_PROTENIX_RUNTIME_SETTINGS: Pick<
    QualitySettings,
    | 'protenix_auto_oom_retry'
    | 'protenix_oom_retry_attempts'
    | 'colabfold_api_host'
    | 'msa_use_gpu'
    | 'msa_local_db'
    | 'msa_cache_dir'
    | 'msa_threads'
    | 'msa_gpu_mode'
    | 'msa_gpu_threshold'
    | 'msa_preferred_gpus'
    | 'msa_excluded_gpus'
    | 'msa_gpu_server_mode'
    | 'msa_gpu_server_wait_timeout'
    | 'msa_gpu_server_db_load_mode'
    | 'msa_gpu_server_startup_wait'
> = {
    protenix_auto_oom_retry: false,
    protenix_oom_retry_attempts: 2,
    colabfold_api_host: 'https://api.colabfold.com',
    msa_use_gpu: true,
    msa_local_db: '',
    msa_cache_dir: '',
    msa_threads: null,
    msa_gpu_mode: 'auto',
    msa_gpu_threshold: 80,
    msa_preferred_gpus: '',
    msa_excluded_gpus: '',
    msa_gpu_server_mode: 'persistent',
    msa_gpu_server_wait_timeout: 120,
    msa_gpu_server_db_load_mode: 0,
    msa_gpu_server_startup_wait: 1.0,
};
const FAMPNN_TEMPERATURE_PRESETS = [
    { label: 'Deterministic', value: 0.03 },
    { label: 'Balanced', value: 0.1 },
    { label: 'Creative', value: 0.3 },
] as const;

const PRESETS: Record<'speed' | 'balanced' | 'quality' | 'maximum', QualitySettings> = {
    speed: {
        msa_preset: 'fast',
        // RFantibody: Fast screening
        rfantibody_diffusion_steps: 20,
        rfantibody_noise_scale_ca: 1.0,
        rfantibody_noise_scale_frame: 1.0,
        rfantibody_guide_scale: 10,
        rfantibody_ckpt_override: '',
        rfantibody_debug_repo_overlay: false,
        // Boltz-2
        boltz_sampling_steps: 50,
        boltz_recycling_steps: 1,
        boltz_num_samples: 1,
        boltz_use_potentials: false,
        boltz_use_msa: true,
        boltz_step_scale: null,
        boltz_predict_affinity: false,
        boltz_diffusion_samples_affinity: 5,
        boltz_anchor_target: false,
        boltz_anchor_strict: false,
        // Protenix
        protenix_model_weights: 'protenix_mini_esm_v0.5.0',
        protenix_seeds: '42',
        protenix_n_sample: 1,
        protenix_n_step: 100,
        protenix_n_cycle: 4,
        protenix_use_msa: false,
        protenix_msa_backend: 'auto',
        protenix_use_template: false,
        protenix_anchor_target: false,
        protenix_anchor_strict: false,
        protenix_enable_cache: true,
        protenix_enable_fusion: true,
        ...DEFAULT_PROTENIX_RUNTIME_SETTINGS,
        // FAMPNN
        fampnn_checkpoint: DEFAULT_FAMPNN_CHECKPOINT,
        fampnn_checkpoint_path: '',
        fampnn_temperature: 0.2,
        fampnn_num_steps: 50,
        fampnn_psce_threshold: 0.4,
        // PPIFlow maturation (off for speed)
        run_maturation: false,
        ppiflow_stage_mode: 'off',
        ppiflow_tuning_profile: 'stage_optimized',
        ppiflow_start_t: 0.5,
        ppiflow_samples_per_target: 3,
        ppiflow_retry_limit: 10,
        ppiflow_config: '/app/ppiflow/configs/inference_nanobody.yaml',
        ppiflow_weights_dir: '',
        ppiflow_checkpoint_path: '',
        ppiflow_rotamer_enrichment_enabled: true,
        ppiflow_require_anchors: true,
        ppiflow_rotamer_shell_cutoff: 20.0,
        ppiflow_objective_mode: 'balanced',
        ppiflow_objective_threshold: 0,
        maturation_anchor_threshold: -5.0,
        maturation_anchor_distance_cutoff: 12.0,
        maturation_min_improvement: -1.0,
        maturation_redesign_temp: 0.1,
        maturation_redesign_steps: 100,
        maturation_design_mode: 'inherit',
        maturation_designs_per_job: 4,
        maturation_filter_percentile: 0,
        maturation_redesign_enabled: true,
        maturation_redesign_top_n: 0,
        ppiflow_checkpoint: DEFAULT_PPIFLOW_CHECKPOINT,
        ppiflow_antigen_chain: '',
        ppiflow_heavy_chain: '',
        ppiflow_light_chain: '',
        ppiflow_backbone_region_mode: 'selected_cdrs',
        ppiflow_maturation_region_mode: 'selected_cdrs',
        ppiflow_backbone_loop_scope: '',
        ppiflow_maturation_loop_scope: '',
        lock_target_chains: true,
        lock_antibody_framework: true,
        // Pre-Boltz filter (null = disabled for speed mode, let everything through)
        fampnn_max_psce: null,
        fampnn_max_residue_psce: null,
        // ThermoMPNN (disabled for speed)
        run_thermompnn: false,
        thermompnn_max_ddg: null,
        // AF2 Backprop (disabled for speed)
        run_af2_backprop: false,
        af2_backprop_soft_iters: 100,
        af2_backprop_temp_iters: 100,
        af2_backprop_hard_iters: 10,
        af2_backprop_num_recycles: 3,
        af2_backprop_learning_rate: 0.1,
        af2_backprop_use_multimer: true,
        af2_backprop_num_models: 1,
        af2_backprop_loss_plddt: 0.1,
        af2_backprop_loss_pae: 0.1,
        af2_backprop_loss_contact: 0.5,
        // Post-Boltz validation filtering (disabled for speed)
        boltz_max_binder_rmsd: null,
        boltz_min_ptm_interface: null,
    },
    balanced: {
        msa_preset: 'fast',
        // RFantibody: Default quality
        rfantibody_diffusion_steps: 50,
        rfantibody_noise_scale_ca: 1.0,
        rfantibody_noise_scale_frame: 1.0,
        rfantibody_guide_scale: 10,
        rfantibody_ckpt_override: '',
        rfantibody_debug_repo_overlay: false,
        // Boltz-2
        boltz_sampling_steps: 200,
        boltz_recycling_steps: 3,
        boltz_num_samples: 1,
        boltz_use_potentials: false,
        boltz_use_msa: true,
        boltz_step_scale: null,
        boltz_predict_affinity: false,
        boltz_diffusion_samples_affinity: 5,
        boltz_anchor_target: false,
        boltz_anchor_strict: false,
        // Protenix
        protenix_model_weights: 'protenix_base_20250630_v1.0.0',
        protenix_seeds: '42',
        protenix_n_sample: 3,
        protenix_n_step: 200,
        protenix_n_cycle: 8,
        protenix_use_msa: true,
        protenix_msa_backend: 'auto',
        protenix_use_template: false,
        protenix_anchor_target: false,
        protenix_anchor_strict: false,
        protenix_enable_cache: true,
        protenix_enable_fusion: true,
        ...DEFAULT_PROTENIX_RUNTIME_SETTINGS,
        // FAMPNN
        fampnn_checkpoint: DEFAULT_FAMPNN_CHECKPOINT,
        fampnn_checkpoint_path: '',
        fampnn_temperature: 0.1,
        fampnn_num_steps: 100,
        fampnn_psce_threshold: 0.3,
        // PPIFlow maturation (off for balanced)
        run_maturation: false,
        ppiflow_stage_mode: 'off',
        ppiflow_tuning_profile: 'stage_optimized',
        ppiflow_start_t: 0.5,
        ppiflow_samples_per_target: 3,
        ppiflow_retry_limit: 10,
        ppiflow_config: '/app/ppiflow/configs/inference_nanobody.yaml',
        ppiflow_weights_dir: '',
        ppiflow_checkpoint_path: '',
        ppiflow_rotamer_enrichment_enabled: true,
        ppiflow_require_anchors: true,
        ppiflow_rotamer_shell_cutoff: 20.0,
        ppiflow_objective_mode: 'balanced',
        ppiflow_objective_threshold: 0,
        maturation_anchor_threshold: -5.0,
        maturation_anchor_distance_cutoff: 12.0,
        maturation_min_improvement: -1.0,
        maturation_redesign_temp: 0.1,
        maturation_redesign_steps: 100,
        maturation_design_mode: 'inherit',
        maturation_designs_per_job: 4,
        maturation_filter_percentile: 0,
        maturation_redesign_enabled: true,
        maturation_redesign_top_n: 0,
        ppiflow_checkpoint: DEFAULT_PPIFLOW_CHECKPOINT,
        ppiflow_antigen_chain: '',
        ppiflow_heavy_chain: '',
        ppiflow_light_chain: '',
        ppiflow_backbone_region_mode: 'selected_cdrs',
        ppiflow_maturation_region_mode: 'selected_cdrs',
        ppiflow_backbone_loop_scope: '',
        ppiflow_maturation_loop_scope: '',
        lock_target_chains: true,
        lock_antibody_framework: true,
        // Pre-Boltz filter: off by default, opt in manually
        fampnn_max_psce: null,
        fampnn_max_residue_psce: null,
        // ThermoMPNN: score-only by default if enabled later
        run_thermompnn: false,
        thermompnn_max_ddg: null,
        // AF2 Backprop (disabled by default)
        run_af2_backprop: false,
        af2_backprop_soft_iters: 100,
        af2_backprop_temp_iters: 100,
        af2_backprop_hard_iters: 10,
        af2_backprop_num_recycles: 3,
        af2_backprop_learning_rate: 0.1,
        af2_backprop_use_multimer: true,
        af2_backprop_num_models: 1,
        af2_backprop_loss_plddt: 0.1,
        af2_backprop_loss_pae: 0.1,
        af2_backprop_loss_contact: 0.5,
        // Post-Boltz validation filtering (permissive for balanced)
        boltz_max_binder_rmsd: null,
        boltz_min_ptm_interface: null,
    },
    quality: {
        msa_preset: 'balanced',
        // RFantibody: Higher quality designs
        rfantibody_diffusion_steps: 50,
        rfantibody_noise_scale_ca: 0.8,
        rfantibody_noise_scale_frame: 0.8,
        rfantibody_guide_scale: 15,
        rfantibody_ckpt_override: '',
        rfantibody_debug_repo_overlay: false,
        // Boltz-2
        boltz_sampling_steps: 500,
        boltz_recycling_steps: 5,
        boltz_num_samples: 3,
        boltz_use_potentials: true,
        boltz_use_msa: true,
        boltz_step_scale: null,
        boltz_predict_affinity: false,
        boltz_diffusion_samples_affinity: 5,
        boltz_anchor_target: false,
        boltz_anchor_strict: false,
        // Protenix
        protenix_model_weights: 'protenix_base_20250630_v1.0.0',
        protenix_seeds: '42',
        protenix_n_sample: 5,
        protenix_n_step: 200,
        protenix_n_cycle: 10,
        protenix_use_msa: true,
        protenix_msa_backend: 'auto',
        protenix_use_template: false,
        protenix_anchor_target: false,
        protenix_anchor_strict: false,
        protenix_enable_cache: true,
        protenix_enable_fusion: true,
        ...DEFAULT_PROTENIX_RUNTIME_SETTINGS,
        // FAMPNN
        fampnn_checkpoint: DEFAULT_FAMPNN_CHECKPOINT,
        fampnn_checkpoint_path: '',
        fampnn_temperature: 0.01,
        fampnn_num_steps: 200,
        fampnn_psce_threshold: 0.2,
        // PPIFlow maturation (enabled for quality)
        run_maturation: true,
        ppiflow_stage_mode: 'post_fampnn',
        ppiflow_tuning_profile: 'stage_optimized',
        ppiflow_start_t: 0.8,
        ppiflow_samples_per_target: 5,
        ppiflow_retry_limit: 10,
        ppiflow_config: '/app/ppiflow/configs/inference_nanobody.yaml',
        ppiflow_weights_dir: '',
        ppiflow_checkpoint_path: '',
        ppiflow_rotamer_enrichment_enabled: true,
        ppiflow_require_anchors: true,
        ppiflow_rotamer_shell_cutoff: 20.0,
        ppiflow_objective_mode: 'balanced',
        ppiflow_objective_threshold: 0,
        maturation_anchor_threshold: -6.0,
        maturation_anchor_distance_cutoff: 12.0,
        maturation_min_improvement: 0.0,
        maturation_redesign_temp: 0.05,
        maturation_redesign_steps: 300,
        maturation_design_mode: 'inherit',
        maturation_designs_per_job: 4,
        maturation_filter_percentile: 0,
        maturation_redesign_enabled: true,
        maturation_redesign_top_n: 0,
        ppiflow_checkpoint: DEFAULT_PPIFLOW_CHECKPOINT,
        ppiflow_antigen_chain: '',
        ppiflow_heavy_chain: '',
        ppiflow_light_chain: '',
        ppiflow_backbone_region_mode: 'selected_cdrs',
        ppiflow_maturation_region_mode: 'selected_cdrs',
        ppiflow_backbone_loop_scope: '',
        ppiflow_maturation_loop_scope: '',
        lock_target_chains: true,
        lock_antibody_framework: true,
        // Pre-Boltz filter: off by default, opt in manually
        fampnn_max_psce: null,
        fampnn_max_residue_psce: null,
        // ThermoMPNN: score-only by default if enabled later
        run_thermompnn: false,
        thermompnn_max_ddg: null,
        // AF2 Backprop (disabled by default, can be enabled for better hit rate)
        run_af2_backprop: false,
        af2_backprop_soft_iters: 100,
        af2_backprop_temp_iters: 100,
        af2_backprop_hard_iters: 10,
        af2_backprop_num_recycles: 5,
        af2_backprop_learning_rate: 0.05,
        af2_backprop_use_multimer: true,
        af2_backprop_num_models: 2,
        af2_backprop_loss_plddt: 0.1,
        af2_backprop_loss_pae: 0.2,
        af2_backprop_loss_contact: 0.4,
        // Post-Boltz validation filtering: off by default, opt in manually
        boltz_max_binder_rmsd: null,
        boltz_min_ptm_interface: null,
    },
    maximum: {
        msa_preset: 'maximum',
        // RFantibody: Best possible quality
        rfantibody_diffusion_steps: 50,
        rfantibody_noise_scale_ca: 0.7,
        rfantibody_noise_scale_frame: 0.7,
        rfantibody_guide_scale: 20,
        rfantibody_ckpt_override: '',
        rfantibody_debug_repo_overlay: false,
        // Boltz-2
        boltz_sampling_steps: 1000,
        boltz_recycling_steps: 10,
        boltz_num_samples: 5,
        boltz_use_potentials: true,
        boltz_use_msa: true,
        boltz_step_scale: null,
        boltz_predict_affinity: true,
        boltz_diffusion_samples_affinity: 10,
        boltz_anchor_target: false,
        boltz_anchor_strict: false,
        // Protenix
        protenix_model_weights: 'protenix_base_20250630_v1.0.0',
        protenix_seeds: '42',
        protenix_n_sample: 8,
        protenix_n_step: 300,
        protenix_n_cycle: 12,
        protenix_use_msa: true,
        protenix_msa_backend: 'colabfold_api',
        protenix_use_template: false,
        protenix_anchor_target: false,
        protenix_anchor_strict: false,
        protenix_enable_cache: true,
        protenix_enable_fusion: true,
        ...DEFAULT_PROTENIX_RUNTIME_SETTINGS,
        // FAMPNN
        fampnn_checkpoint: DEFAULT_FAMPNN_CHECKPOINT,
        fampnn_checkpoint_path: '',
        fampnn_temperature: 0.0001,
        fampnn_num_steps: 500,
        fampnn_psce_threshold: 0.15,
        // PPIFlow maturation (enabled for maximum)
        run_maturation: true,
        ppiflow_stage_mode: 'post_fampnn',
        ppiflow_tuning_profile: 'stage_optimized',
        ppiflow_start_t: 0.9,
        ppiflow_samples_per_target: 8,
        ppiflow_retry_limit: 10,
        ppiflow_config: '/app/ppiflow/configs/inference_nanobody.yaml',
        ppiflow_weights_dir: '',
        ppiflow_checkpoint_path: '',
        ppiflow_rotamer_enrichment_enabled: true,
        ppiflow_require_anchors: true,
        ppiflow_rotamer_shell_cutoff: 20.0,
        ppiflow_objective_mode: 'balanced',
        ppiflow_objective_threshold: 0,
        maturation_anchor_threshold: -7.0,
        maturation_anchor_distance_cutoff: 12.0,
        maturation_min_improvement: 0.0,
        maturation_redesign_temp: 0.01,
        maturation_redesign_steps: 500,
        maturation_design_mode: 'inherit',
        maturation_designs_per_job: 4,
        maturation_filter_percentile: 0,
        maturation_redesign_enabled: true,
        maturation_redesign_top_n: 0,
        ppiflow_checkpoint: DEFAULT_PPIFLOW_CHECKPOINT,
        ppiflow_antigen_chain: '',
        ppiflow_heavy_chain: '',
        ppiflow_light_chain: '',
        ppiflow_backbone_region_mode: 'selected_cdrs',
        ppiflow_maturation_region_mode: 'selected_cdrs',
        ppiflow_backbone_loop_scope: '',
        ppiflow_maturation_loop_scope: '',
        lock_target_chains: true,
        lock_antibody_framework: true,
        // Pre-Boltz filter: off by default, opt in manually
        fampnn_max_psce: null,
        fampnn_max_residue_psce: null,
        // ThermoMPNN: score-only by default if enabled later
        run_thermompnn: false,
        thermompnn_max_ddg: null,
        // AF2 Backprop: enabled for maximum quality runs
        run_af2_backprop: false,
        af2_backprop_soft_iters: 150,
        af2_backprop_temp_iters: 150,
        af2_backprop_hard_iters: 20,
        af2_backprop_num_recycles: 8,
        af2_backprop_learning_rate: 0.03,
        af2_backprop_use_multimer: true,
        af2_backprop_num_models: 3,
        af2_backprop_loss_plddt: 0.1,
        af2_backprop_loss_pae: 0.3,
        af2_backprop_loss_contact: 0.3,
        // Post-Boltz validation filtering: off by default, opt in manually
        boltz_max_binder_rmsd: null,
        boltz_min_ptm_interface: null,
    },
};

const MSA_PRESET_INFO: Record<QualitySettings['msa_preset'], { label: string; description: string }> = {
    maximum: { label: 'Maximum', description: 'Full ColabFold-style search with expansion. Highest coverage, slowest.' },
    balanced: { label: 'Balanced', description: 'Environmental search without expansion. Good default tradeoff.' },
    fast: { label: 'Fast', description: 'UniRef30-only style search. Best for quick screening.' },
};

interface QualitySettingsPanelProps {
    settings: QualitySettings;
    onSettingsChange: (settings: QualitySettings) => void;
    structureValidator?: 'boltz2' | 'protenix';
    allowPostPpiFlowRetry?: boolean;
    showRfantibodySettings?: boolean;
    showStructureValidationSettings?: boolean;
    showFampnnSettings?: boolean;
    showPreValidationFiltering?: boolean;
    showPostValidationFiltering?: boolean;
}

interface PPIFlowSettingsFieldsProps {
    settings: QualitySettings;
    onSettingsChange: (settings: QualitySettings) => void;
    allowPostPpiFlowRetry?: boolean;
}

export const PPIFlowSettingsFields: React.FC<PPIFlowSettingsFieldsProps> = ({
    settings,
    onSettingsChange,
    allowPostPpiFlowRetry = false,
}) => {
    const updateSetting = <K extends keyof QualitySettings>(key: K, value: QualitySettings[K]) => {
        onSettingsChange({
            ...settings,
            [key]: value,
        });
    };
    const updateSettings = (updates: Partial<QualitySettings>) => {
        onSettingsChange({
            ...settings,
            ...updates,
        });
    };

    const defaultCheckpointPath = `/opt/ppiflow/ckpt/${settings.ppiflow_checkpoint}.ckpt`;
    const overridesEnabled = Boolean(
        settings.ppiflow_heavy_chain || settings.ppiflow_light_chain || settings.ppiflow_antigen_chain,
    );
    const [overrideEnabled, setOverrideEnabled] = useState(overridesEnabled);

    useEffect(() => {
        if (overridesEnabled && !overrideEnabled) {
            setOverrideEnabled(true);
        }
    }, [overridesEnabled, overrideEnabled]);

    const stageMode = (settings.ppiflow_stage_mode || (settings.run_maturation ? 'post_fampnn' : 'off')) as PPIFlowStageMode;
    const tuningProfile = normalizePpiFlowTuningProfile(settings.ppiflow_tuning_profile);
    const optimizationScenario = getPpiFlowOptimizationScenario(stageMode);
    const stageOptimizedAvailable = optimizationScenario !== null;
    const managedByStageProfile = tuningProfile === 'stage_optimized' && stageOptimizedAvailable;
    const stageModeLabel: Record<PPIFlowStageMode, string> = {
        off: 'Off',
        post_rfantibody: 'Post RF backbone refine',
        post_ppiflow: 'Post-PPIFlow backbone reattempt',
        post_fampnn: 'Post FA-MPNN maturation',
        both: 'Both stages',
    };
    const updateStageMode = (next: PPIFlowStageMode) => {
        onSettingsChange(applyPpiFlowStageMode(settings, next));
    };
    const backboneStageEnabled = stageMode === 'post_rfantibody' || stageMode === 'post_ppiflow' || stageMode === 'both';
    const maturationStageEnabled = stageMode === 'post_fampnn' || stageMode === 'both';

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm font-medium text-teal-400">
                    PPIFlow Stage Control
                    <a
                        href="https://github.com/Mingchenchen/PPIFlow"
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-slate-400 hover:text-teal-300 underline"
                    >
                        GitHub
                    </a>
                </div>
            </div>
            <p className="text-[11px] text-slate-500">
                Backbone refinement runs before sequence design. Maturation runs after FA-MPNN. Post-PPIFlow reattempt reuses existing PPIFlow outputs for another sequence-free pass and defaults strict anchors off. Leave loop scopes blank to inherit the workflow-selected CDR set.
            </p>

            <div className={`grid grid-cols-2 gap-2 ${allowPostPpiFlowRetry ? 'md:grid-cols-5' : 'md:grid-cols-4'}`}>
                {([
                    ['off', 'Off'],
                    ['post_rfantibody', 'Post RF'],
                    ...(allowPostPpiFlowRetry ? [['post_ppiflow', 'Post PPIFlow']] as Array<[PPIFlowStageMode, string]> : []),
                    ['post_fampnn', 'Post FA-MPNN'],
                    ['both', 'Both'],
                ] as Array<[PPIFlowStageMode, string]>).map(([value, label]) => (
                    <button
                        key={value}
                        type="button"
                        onClick={() => updateStageMode(value)}
                        className={`rounded-lg border px-3 py-2 text-xs transition-colors ${stageMode === value
                            ? 'border-teal-400 bg-teal-500/15 text-teal-100'
                            : 'border-slate-700 bg-slate-900/70 text-slate-300 hover:border-slate-600'
                            }`}
                    >
                        <div className="font-medium">{label}</div>
                        <div className="mt-0.5 text-[10px] text-slate-500">{stageModeLabel[value]}</div>
                    </button>
                ))}
            </div>

            {stageMode === 'off' && (
                <div className="text-[10px] text-slate-600">
                    Select a PPIFlow stage to reveal the full refinement controls.
                </div>
            )}

            {stageMode !== 'off' && (
                <div className="space-y-4">
                    <div className="rounded-lg border border-slate-800 bg-slate-950/30 p-3">
                        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                            <div className="space-y-1">
                                <div className="text-xs text-slate-300">Stage setup strategy</div>
                                <p className="text-[10px] text-slate-500">
                                    Stage-optimized mode follows the repo PPIFlow guidance for the selected stage. Pre-sequence refinement lowers `start_t`, raises samples, and disables strict anchor rejection. Post-FA-MPNN maturation uses higher `start_t`, fewer samples, balanced ranking, and strict anchors.
                                </p>
                                {stageMode === 'both' && (
                                    <p className="text-[10px] text-amber-300">
                                        `Both` shares one set of core PPIFlow knobs across two different stages, so stage-optimized defaults are disabled there. Switch to `Manual` to tune the shared settings directly.
                                    </p>
                                )}
                            </div>
                            <div className="inline-flex rounded-lg border border-slate-700 bg-slate-900/70 p-1">
                                <button
                                    type="button"
                                    disabled={!stageOptimizedAvailable}
                                    onClick={() => onSettingsChange(applyPpiFlowTuningProfile(settings, 'stage_optimized'))}
                                    className={`rounded px-3 py-1 text-xs transition-colors ${tuningProfile === 'stage_optimized'
                                        ? 'bg-teal-500/15 text-teal-100'
                                        : 'text-slate-300 hover:text-slate-100'
                                        } ${!stageOptimizedAvailable ? 'cursor-not-allowed opacity-50' : ''}`}
                                >
                                    Stage optimized
                                </button>
                                <button
                                    type="button"
                                    onClick={() => onSettingsChange(applyPpiFlowTuningProfile(settings, 'manual'))}
                                    className={`rounded px-3 py-1 text-xs transition-colors ${tuningProfile === 'manual'
                                        ? 'bg-slate-700 text-slate-100'
                                        : 'text-slate-300 hover:text-slate-100'
                                        }`}
                                >
                                    Manual
                                </button>
                            </div>
                        </div>
                        {managedByStageProfile && (
                            <div className="mt-2 text-[10px] text-teal-300">
                                Core partial-flow controls below are currently managed by the selected stage strategy. Switch to `Manual` if you want to override them directly.
                            </div>
                        )}
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                <div>
                    <label className="block text-xs text-slate-500 mb-1">
                        Partial Flow Start t <span className="text-slate-600">({settings.ppiflow_start_t.toFixed(2)})</span>
                    </label>
                    <input
                        type="range"
                        min={0.3}
                        max={0.95}
                        step={0.05}
                        value={settings.ppiflow_start_t}
                        disabled={managedByStageProfile}
                        onChange={(e) => updateSetting('ppiflow_start_t', parseFloat(e.target.value))}
                        className={`w-full h-2 bg-slate-700 rounded-lg appearance-none accent-teal-500 ${managedByStageProfile ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}
                    />
                    <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                        <span>0.3 (diverse)</span>
                        <span>0.8</span>
                        <span>0.95 (refine)</span>
                    </div>
                    <p className="text-[10px] text-slate-600 mt-1">
                        Higher t preserves input backbone; lower t increases exploration.
                    </p>
                </div>

                <div>
                    <label className="block text-xs text-slate-500 mb-1">
                        Samples per Target <span className="text-slate-600">({settings.ppiflow_samples_per_target})</span>
                    </label>
                    <input
                        type="range"
                        min={1}
                        max={10}
                        step={1}
                        value={settings.ppiflow_samples_per_target}
                        disabled={managedByStageProfile}
                        onChange={(e) => updateSetting('ppiflow_samples_per_target', parseInt(e.target.value))}
                        className={`w-full h-2 bg-slate-700 rounded-lg appearance-none accent-teal-500 ${managedByStageProfile ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}
                    />
                    <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                        <span>1</span>
                        <span>5</span>
                        <span>10</span>
                    </div>
                    <p className="text-[10px] text-slate-600 mt-1">
                        More samples improves odds of better refinements but costs GPU time.
                    </p>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
                <div>
                    <label className="block text-xs text-slate-500 mb-1">
                        Retry Limit <span className="text-slate-600">({settings.ppiflow_retry_limit})</span>
                    </label>
                    <input
                        type="range"
                        min={1}
                        max={20}
                        step={1}
                        value={settings.ppiflow_retry_limit}
                        onChange={(e) => updateSetting('ppiflow_retry_limit', parseInt(e.target.value))}
                        className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-teal-500"
                    />
                    <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                        <span>1</span>
                        <span>10</span>
                        <span>20</span>
                    </div>
                    <p className="text-[10px] text-slate-600 mt-1">
                        Extra retries help recover from filter failures.
                    </p>
                </div>

                <div>
                    <label className="block text-xs text-slate-500 mb-1">
                        Interface Pair Cutoff <span className="text-slate-600">({settings.maturation_anchor_distance_cutoff.toFixed(1)} Å)</span>
                    </label>
                    <input
                        type="range"
                        min={4}
                        max={12}
                        step={0.5}
                        value={settings.maturation_anchor_distance_cutoff}
                        onChange={(e) => updateSetting('maturation_anchor_distance_cutoff', parseFloat(e.target.value))}
                        className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-teal-500"
                    />
                    <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                        <span>4</span>
                        <span>8</span>
                        <span>12</span>
                    </div>
                    <p className="text-[10px] text-slate-600 mt-1">
                        Distance used when summing negative binder-target pair energies for anchor selection.
                    </p>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
                <div>
                    <label className="block text-xs text-slate-500 mb-1">
                        Anchor Energy Threshold <span className="text-slate-600">({settings.maturation_anchor_threshold.toFixed(1)})</span>
                    </label>
                    <input
                        type="range"
                        min={-10}
                        max={0}
                        step={0.5}
                        value={settings.maturation_anchor_threshold}
                        onChange={(e) => updateSetting('maturation_anchor_threshold', parseFloat(e.target.value))}
                        className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-teal-500"
                    />
                    <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                        <span>-10 (strict)</span>
                        <span>-5</span>
                        <span>0 (loose)</span>
                    </div>
                    <p className="text-[10px] text-slate-600 mt-1">
                        More negative = stricter anchor selection.
                    </p>
                </div>

                <div>
                    <label className="block text-xs text-slate-500 mb-1">Ranking Objective</label>
                    <select
                        value={settings.ppiflow_objective_mode}
                        disabled={managedByStageProfile}
                        onChange={(e) => updateSetting('ppiflow_objective_mode', e.target.value as PPIFlowObjectiveMode)}
                        className={`w-full rounded border border-slate-700 px-2 py-1 text-sm ${managedByStageProfile ? 'cursor-not-allowed bg-slate-900/60 text-slate-500' : 'bg-slate-800 text-slate-300'}`}
                    >
                        <option value="balanced">Balanced loop-target + epitope</option>
                        <option value="loop_epitope">Epitope-focused</option>
                        <option value="loop_target">Whole-target-focused</option>
                        <option value="selected_interface">Selected-interface only</option>
                    </select>
                    <p className="text-[10px] text-slate-600 mt-1">
                        Controls how partial-flow samples are ranked after generation. Loop-aware modes track per-loop contact and distance deltas against the target patch instead of only coarse interface energy.
                    </p>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
                <div>
                    <label className="block text-xs text-slate-500 mb-1">
                        {settings.ppiflow_objective_mode === 'selected_interface' ? 'Min Interface Improvement' : 'Objective Threshold'} <span className="text-slate-600">({(settings.ppiflow_objective_mode === 'selected_interface' ? settings.maturation_min_improvement : settings.ppiflow_objective_threshold).toFixed(1)})</span>
                    </label>
                    <input
                        type="range"
                        min={settings.ppiflow_objective_mode === 'selected_interface' ? -5 : -10}
                        max={settings.ppiflow_objective_mode === 'selected_interface' ? 0 : 10}
                        step={0.5}
                        value={settings.ppiflow_objective_mode === 'selected_interface' ? settings.maturation_min_improvement : settings.ppiflow_objective_threshold}
                        disabled={managedByStageProfile}
                        onChange={(e) => {
                            const nextValue = parseFloat(e.target.value);
                            if (settings.ppiflow_objective_mode === 'selected_interface') {
                                updateSetting('maturation_min_improvement', nextValue);
                            } else {
                                updateSetting('ppiflow_objective_threshold', nextValue);
                            }
                        }}
                        className={`w-full h-2 bg-slate-700 rounded-lg appearance-none accent-teal-500 ${managedByStageProfile ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}
                    />
                    <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                        {settings.ppiflow_objective_mode === 'selected_interface' ? (
                            <>
                                <span>-5 (strict)</span>
                                <span>-1</span>
                                <span>0 (off)</span>
                            </>
                        ) : (
                            <>
                                <span>-10</span>
                                <span>0</span>
                                <span>10</span>
                            </>
                        )}
                    </div>
                    <p className="text-[10px] text-slate-600 mt-1">
                        {settings.ppiflow_objective_mode === 'selected_interface'
                            ? 'Legacy gate on selected delta interface score. More negative is better.'
                            : 'Loop-aware gate on the aggregated objective score. Lower is better; values below zero usually indicate improved loop behavior against the target.'}
                    </p>
                </div>

                <div className="rounded-lg border border-slate-800 bg-slate-950/30 p-3">
                    <div className="text-xs text-slate-300">Objective semantics</div>
                    <div className="mt-2 space-y-1 text-[10px] text-slate-500">
                        <div><span className="text-slate-300">Balanced:</span> mixes loop epitope and whole-target gains with RMSD/clash penalties.</div>
                        <div><span className="text-slate-300">Epitope:</span> prioritize loop movement toward the selected epitope patch.</div>
                        <div><span className="text-slate-300">Whole target:</span> prioritize loop engagement anywhere on the target surface.</div>
                        <div><span className="text-slate-300">Selected iface:</span> old coarse interface-only ranking.</div>
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
                <div>
                    <label className="block text-xs text-slate-500 mb-1">
                        Rotamer Shell <span className="text-slate-600">({settings.ppiflow_rotamer_shell_cutoff.toFixed(1)} Å)</span>
                    </label>
                    <input
                        type="range"
                        min={8}
                        max={24}
                        step={0.5}
                        value={settings.ppiflow_rotamer_shell_cutoff}
                        onChange={(e) => updateSetting('ppiflow_rotamer_shell_cutoff', parseFloat(e.target.value))}
                        className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-teal-500"
                    />
                    <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                        <span>8</span>
                        <span>20</span>
                        <span>24</span>
                    </div>
                    <p className="text-[10px] text-slate-600 mt-1">
                        Local relax/repack shell used for interface rotamer enrichment before anchor selection.
                    </p>
                </div>

                <div className="space-y-3 rounded-lg border border-slate-800 bg-slate-950/30 p-3">
                    <label className="flex items-center justify-between gap-3 text-xs text-slate-300">
                        <span>Interface Rotamer Enrichment</span>
                        <input
                            type="checkbox"
                            checked={settings.ppiflow_rotamer_enrichment_enabled}
                            onChange={(e) => updateSetting('ppiflow_rotamer_enrichment_enabled', e.target.checked)}
                            className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-teal-500 focus:ring-teal-500"
                        />
                    </label>
                    <label className="flex items-center justify-between gap-3 text-xs text-slate-300">
                        <span>Require Non-Movable Anchors</span>
                        <input
                            type="checkbox"
                            checked={settings.ppiflow_require_anchors}
                            disabled={managedByStageProfile}
                            onChange={(e) => updateSetting('ppiflow_require_anchors', e.target.checked)}
                            className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-teal-500 focus:ring-teal-500"
                        />
                    </label>
                    <p className="text-[10px] text-slate-600">
                        {managedByStageProfile && optimizationScenario === 'pre_sequence'
                            ? 'Stage-optimized pre-sequence refinement still scores anchors, but it disables strict zero-anchor rejection so nearby backbones can be explored before sequence design.'
                            : managedByStageProfile && optimizationScenario === 'post_sequence'
                                ? 'Stage-optimized post-FA-MPNN maturation keeps strict anchor enforcement on for sequence-conditioned cleanup.'
                                : 'Default paper-aligned path: repack the interface first, then fail loudly if no usable anchors remain outside the movable region.'}
                    </p>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
                <div>
                    <div className="mb-1 flex items-center justify-between gap-3">
                        <label className="block text-xs text-slate-500">
                            Redesign Temperature <span className="text-slate-600">({settings.maturation_redesign_temp.toFixed(4)})</span>
                        </label>
                        <input
                            type="number"
                            min={0.0001}
                            max={1}
                            step={0.0001}
                            value={settings.maturation_redesign_temp}
                            onChange={(e) => {
                                const next = Number(e.target.value);
                                if (!Number.isFinite(next)) return;
                                updateSetting('maturation_redesign_temp', Math.min(1, Math.max(0.0001, next)));
                            }}
                            className="w-28 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-right text-xs text-slate-200 outline-none focus:border-teal-500"
                        />
                    </div>
                    <input
                        type="range"
                        min={-4}
                        max={0}
                        step={0.1}
                        value={Math.log10(settings.maturation_redesign_temp)}
                        onChange={(e) => updateSetting('maturation_redesign_temp', Math.pow(10, parseFloat(e.target.value)))}
                        className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-teal-500"
                    />
                    <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                        <span>0.0001</span>
                        <span>0.01</span>
                        <span>1.0</span>
                    </div>
                    <p className="text-[10px] text-slate-600 mt-1">
                        Lower temperature = more conservative redesign.
                    </p>
                </div>

                <div>
                    <label className="block text-xs text-slate-500 mb-1">
                        Redesign Steps <span className="text-slate-600">({settings.maturation_redesign_steps})</span>
                    </label>
                    <input
                        type="range"
                        min={50}
                        max={500}
                        step={50}
                        value={settings.maturation_redesign_steps}
                        onChange={(e) => updateSetting('maturation_redesign_steps', parseInt(e.target.value))}
                        className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-teal-500"
                    />
                    <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                        <span>50</span>
                        <span>250</span>
                        <span>500</span>
                    </div>
                    <p className="text-[10px] text-slate-600 mt-1">
                        More steps increases refinement fidelity but costs time.
                    </p>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
                <div>
                    <label className="flex items-center gap-2 text-xs text-slate-400">
                        <input
                            type="checkbox"
                            checked={settings.maturation_redesign_enabled}
                            onChange={(e) => updateSetting('maturation_redesign_enabled', e.target.checked)}
                            className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-teal-600 focus:ring-teal-500"
                        />
                        Redesign after partial flow
                    </label>
                    <p className="text-[10px] text-slate-600 mt-1">
                        Runs a second FAMPNN pass to refresh non-anchor residues.
                    </p>
                </div>

                <div>
                    <label className="block text-xs text-slate-500 mb-1">
                        Redesign Top N <span className="text-slate-600">({settings.maturation_redesign_top_n})</span>
                    </label>
                    <input
                        type="range"
                        min={0}
                        max={50}
                        step={1}
                        value={settings.maturation_redesign_top_n}
                        onChange={(e) => updateSetting('maturation_redesign_top_n', parseInt(e.target.value))}
                        className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-teal-500"
                    />
                    <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                        <span>0 (all)</span>
                        <span>25</span>
                        <span>50</span>
                    </div>
                    <p className="text-[10px] text-slate-600 mt-1">
                        Selects top designs by the active partial-flow objective.
                    </p>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
                <div>
                    <label className="block text-xs text-slate-500 mb-1">
                        Filter Percentile <span className="text-slate-600">({settings.maturation_filter_percentile || 0}%)</span>
                    </label>
                    <input
                        type="range"
                        min={0}
                        max={50}
                        step={5}
                        value={settings.maturation_filter_percentile}
                        onChange={(e) => updateSetting('maturation_filter_percentile', parseInt(e.target.value))}
                        className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-teal-500"
                    />
                    <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                        <span>0 (off)</span>
                        <span>25</span>
                        <span>50</span>
                    </div>
                    <p className="text-[10px] text-slate-600 mt-1">
                        Keep only the top percentile of matured designs by the active objective score.
                    </p>
                </div>

                <div>
                    <label className="block text-xs text-slate-500 mb-1">Maturation Design Mode</label>
                    <select
                        value={settings.maturation_design_mode}
                        onChange={(e) => updateSetting('maturation_design_mode', e.target.value)}
                        className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-slate-300"
                    >
                        <option value="inherit">Inherit from workflow</option>
                        <option value="cdr_only">CDR Only</option>
                        <option value="cdr_selective">CDR Selective</option>
                        <option value="framework_allowed">Framework Allowed</option>
                        <option value="full_design">Full Design</option>
                    </select>
                    <p className="text-[10px] text-slate-600 mt-1">
                        Inherit uses the main workflow design mode. Override only if needed.
                    </p>
                    <p className="text-[10px] text-slate-600 mt-1">
                        PPIFlow redesign also honors the sequence-design protection toggles below: target chains can stay locked, and framework residues can remain fixed outside active CDRs.
                    </p>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
                <div>
                    <label className="block text-xs text-slate-500 mb-1">
                        Designs per Maturation Job <span className="text-slate-600">({settings.maturation_designs_per_job})</span>
                    </label>
                    <input
                        type="range"
                        min={1}
                        max={1000}
                        step={1}
                        value={settings.maturation_designs_per_job}
                        onChange={(e) => updateSetting('maturation_designs_per_job', parseInt(e.target.value))}
                        className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-teal-500"
                    />
                    <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                        <span>1</span>
                        <span>500</span>
                        <span>1000</span>
                    </div>
                    <p className="text-[10px] text-slate-600 mt-1">
                        Controls orchestration batching only.
                    </p>
                </div>

                <div>
                    <label className="block text-xs text-slate-500 mb-1">Checkpoint</label>
                    <select
                        value={settings.ppiflow_checkpoint}
                        onChange={(e) => updateSetting('ppiflow_checkpoint', e.target.value)}
                        className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-slate-300"
                    >
                        <option value="nanobody">Nanobody</option>
                        <option value="antibody">Antibody</option>
                        <option value="binder">Binder</option>
                    </select>
                    <p className="text-[10px] text-slate-600 mt-1">
                        Selects which PPIFlow weights to use. Override below for a specific ckpt path.
                    </p>
                </div>
            </div>

            <div className="space-y-2">
                <label className="flex items-center gap-2 text-xs text-slate-400">
                    <input
                        type="checkbox"
                    checked={overrideEnabled}
                        onChange={(e) => {
                        if (e.target.checked) {
                            setOverrideEnabled(true);
                            return;
                        }
                        setOverrideEnabled(false);
                        updateSettings({
                            ppiflow_heavy_chain: '',
                            ppiflow_light_chain: '',
                            ppiflow_antigen_chain: '',
                        });
                        }}
                        className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-teal-600 focus:ring-teal-500"
                    />
                    Override chain IDs (leave unchecked for auto-detect)
                </label>
                <div className="grid grid-cols-3 gap-3">
                    <div>
                        <label className="block text-xs text-slate-500 mb-1">Heavy Chain Override</label>
                        <input
                            type="text"
                            value={settings.ppiflow_heavy_chain}
                            onChange={(e) => updateSetting('ppiflow_heavy_chain', e.target.value.toUpperCase())}
                            placeholder="auto"
                        disabled={!overrideEnabled}
                        className={`w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-sm font-mono ${overrideEnabled ? 'text-slate-300' : 'text-slate-600 opacity-70'}`}
                        />
                    </div>
                    <div>
                        <label className="block text-xs text-slate-500 mb-1">Light Chain Override</label>
                        <input
                            type="text"
                            value={settings.ppiflow_light_chain}
                            onChange={(e) => updateSetting('ppiflow_light_chain', e.target.value.toUpperCase())}
                            placeholder="auto"
                        disabled={!overrideEnabled}
                        className={`w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-sm font-mono ${overrideEnabled ? 'text-slate-300' : 'text-slate-600 opacity-70'}`}
                        />
                    </div>
                    <div>
                        <label className="block text-xs text-slate-500 mb-1">Antigen Chain Override</label>
                        <input
                            type="text"
                            value={settings.ppiflow_antigen_chain}
                            onChange={(e) => updateSetting('ppiflow_antigen_chain', e.target.value.toUpperCase())}
                            placeholder="auto"
                        disabled={!overrideEnabled}
                        className={`w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-sm font-mono ${overrideEnabled ? 'text-slate-300' : 'text-slate-600 opacity-70'}`}
                        />
                    </div>
                </div>
                <p className="text-[10px] text-slate-600">
                    Auto-detect uses `antibody_chains` for heavy/light and infers antigen chains from the complex PDB.
                </p>
            </div>

            <div className="space-y-3">
                <div>
                    <label className="block text-xs text-slate-500 mb-1">Weights Directory</label>
                    <input
                        type="text"
                        value={settings.ppiflow_weights_dir}
                        onChange={(e) => updateSetting('ppiflow_weights_dir', e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300 font-mono"
                    />
                    <p className="text-[10px] text-slate-600 mt-1">
                        Host path mounted into the PPIFlow container (leave blank to use BMS_WEIGHTS/ppiflow).
                    </p>
                </div>
                <div>
                    <label className="block text-xs text-slate-500 mb-1">Checkpoint Override (optional)</label>
                    <input
                        type="text"
                        value={settings.ppiflow_checkpoint_path}
                        onChange={(e) => updateSetting('ppiflow_checkpoint_path', e.target.value)}
                        placeholder={defaultCheckpointPath}
                        className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300 font-mono"
                    />
                    <p className="text-[10px] text-slate-600 mt-1">
                        Auto path: {defaultCheckpointPath}
                    </p>
                </div>
                <div>
                    <label className="block text-xs text-slate-500 mb-1">Config Path</label>
                    <input
                        type="text"
                        value={settings.ppiflow_config}
                        onChange={(e) => updateSetting('ppiflow_config', e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300 font-mono"
                    />
                    <p className="text-[10px] text-slate-600 mt-1">
                        YAML config inside the container.
                    </p>
                </div>
            </div>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                {backboneStageEnabled && (
                    <div>
                        <label className="block text-xs text-slate-500 mb-1">
                            Backbone refine region
                        </label>
                        <select
                            value={settings.ppiflow_backbone_region_mode}
                            onChange={(e) => updateSetting('ppiflow_backbone_region_mode', e.target.value as PPIFlowRegionMode)}
                            className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300"
                        >
                            <option value="selected_cdrs">Selected CDRs</option>
                            <option value="all_cdrs">All CDRs</option>
                            <option value="framework_only">Framework Only</option>
                            <option value="all_antibody">Whole Antibody</option>
                        </select>
                        {settings.ppiflow_backbone_region_mode === 'selected_cdrs' && (
                            <>
                                <input
                                    type="text"
                                    value={settings.ppiflow_backbone_loop_scope}
                                    onChange={(e) => updateSetting('ppiflow_backbone_loop_scope', e.target.value.toUpperCase())}
                                    placeholder="H1,H2,H3"
                                    className="mt-2 w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300 font-mono"
                                />
                                <p className="text-[10px] text-slate-600 mt-1">
                                    Comma-separated loop IDs for partial flow after RFantibody. Blank inherits the workflow-selected loop set.
                                </p>
                            </>
                        )}
                        {settings.ppiflow_backbone_region_mode !== 'selected_cdrs' && (
                            <p className="text-[10px] text-slate-600 mt-1">
                                Partial flow follows this antibody region directly and does not depend on sequence-design loop settings.
                            </p>
                        )}
                    </div>
                )}

                {maturationStageEnabled && (
                    <div>
                        <label className="block text-xs text-slate-500 mb-1">
                            Maturation region
                        </label>
                        <select
                            value={settings.ppiflow_maturation_region_mode}
                            onChange={(e) => updateSetting('ppiflow_maturation_region_mode', e.target.value as PPIFlowRegionMode)}
                            className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300"
                        >
                            <option value="selected_cdrs">Selected CDRs</option>
                            <option value="all_cdrs">All CDRs</option>
                            <option value="framework_only">Framework Only</option>
                            <option value="all_antibody">Whole Antibody</option>
                        </select>
                        {settings.ppiflow_maturation_region_mode === 'selected_cdrs' && (
                            <>
                                <input
                                    type="text"
                                    value={settings.ppiflow_maturation_loop_scope}
                                    onChange={(e) => updateSetting('ppiflow_maturation_loop_scope', e.target.value.toUpperCase())}
                                    placeholder="H1,H2,H3"
                                    className="mt-2 w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300 font-mono"
                                />
                                <p className="text-[10px] text-slate-600 mt-1">
                                    Comma-separated loop IDs for partial flow after FA-MPNN. Blank inherits the workflow-selected loop set.
                                </p>
                            </>
                        )}
                        {settings.ppiflow_maturation_region_mode !== 'selected_cdrs' && (
                            <p className="text-[10px] text-slate-600 mt-1">
                                Partial flow follows this antibody region directly. Redesign settings remain independent.
                            </p>
                        )}
                    </div>
                )}
            </div>
            </div>
            )}
        </div>
    );
};

export const QualitySettingsPanel: React.FC<QualitySettingsPanelProps> = ({
    settings,
    onSettingsChange,
    structureValidator = 'boltz2',
    allowPostPpiFlowRetry = false,
    showRfantibodySettings = true,
    showStructureValidationSettings = true,
    showFampnnSettings = true,
    showPreValidationFiltering = true,
    showPostValidationFiltering = true,
}) => {
    const [isExpanded, setIsExpanded] = useState(false);
    const [showMsaRuntimeOverrides, setShowMsaRuntimeOverrides] = useState(false);

    const updateSetting = <K extends keyof QualitySettings>(key: K, value: QualitySettings[K]) => {
        onSettingsChange({ ...settings, [key]: value });
    };

    const msaEnabled = structureValidator === 'protenix' ? settings.protenix_use_msa : settings.boltz_use_msa;
    const showProtenixMsaProvider = structureValidator === 'protenix' && settings.protenix_use_msa;
    const showRemoteMsaHost = showProtenixMsaProvider && settings.protenix_msa_backend !== 'local';
    const showLocalMsaRuntime = msaEnabled && (structureValidator === 'boltz2' || settings.protenix_msa_backend !== 'colabfold_api');

    return (
        <div className="bg-slate-900/50 border border-slate-700/50 rounded-lg overflow-hidden">
            {/* Header */}
            <button
                onClick={() => setIsExpanded(!isExpanded)}
                className="w-full flex items-center justify-between p-4 hover:bg-slate-800/30 transition-colors"
            >
                <div className="flex items-center gap-3">
                    <div className="text-left">
                        <h3 className="text-sm font-medium text-slate-300">Quality Settings</h3>
                        <p className="text-xs text-slate-500">
                            Active workflow and model-specific controls only.
                        </p>
                    </div>
                </div>
                <span className={`text-slate-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`}>
                    ▼
                </span>
            </button>

            {/* Expanded Content */}
            {isExpanded && (
                <div className="px-4 pb-4 space-y-4">
                    {/* RFantibody Settings */}
                    {showRfantibodySettings && (
                    <div className="space-y-3">
                        <div className="flex items-center gap-2 text-sm font-medium text-accent-secondary">
                            Backbone Design (RFantibody)
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <label className="block text-xs text-slate-500 mb-1">
                                    Diffusion Steps <span className="text-slate-600">({settings.rfantibody_diffusion_steps})</span>
                                </label>
                                <input
                                    type="range"
                                    min={20}
                                    max={200}
                                    step={5}
                                    value={settings.rfantibody_diffusion_steps}
                                    onChange={(e) => updateSetting('rfantibody_diffusion_steps', parseInt(e.target.value))}
                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-pink-500"
                                />
                                <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                    <span>20 (fast)</span>
                                    <span>50 (default ceiling)</span>
                                    <span>200 (extended)</span>
                                </div>
                            </div>

                            <div>
                                <label className="block text-xs text-slate-500 mb-1">
                                    Guide Scale <span className="text-slate-600">({settings.rfantibody_guide_scale})</span>
                                </label>
                                <input
                                    type="range"
                                    min={1}
                                    max={50}
                                    step={1}
                                    value={settings.rfantibody_guide_scale}
                                    onChange={(e) => updateSetting('rfantibody_guide_scale', parseInt(e.target.value))}
                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-pink-500"
                                />
                                <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                    <span>1 (weak)</span>
                                    <span>25</span>
                                    <span>50 (strong)</span>
                                </div>
                            </div>
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <label className="block text-xs text-slate-500 mb-1">
                                    Noise Scale (CA) <span className="text-slate-600">({settings.rfantibody_noise_scale_ca.toFixed(1)})</span>
                                </label>
                                <input
                                    type="range"
                                    min={0.5}
                                    max={2.0}
                                    step={0.1}
                                    value={settings.rfantibody_noise_scale_ca}
                                    onChange={(e) => updateSetting('rfantibody_noise_scale_ca', parseFloat(e.target.value))}
                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-pink-500"
                                />
                                <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                    <span>0.5 (consistent)</span>
                                    <span>1.0</span>
                                    <span>2.0 (diverse)</span>
                                </div>
                            </div>

                            <div>
                                <label className="block text-xs text-slate-500 mb-1">
                                    Noise Scale (Frame) <span className="text-slate-600">({settings.rfantibody_noise_scale_frame.toFixed(1)})</span>
                                </label>
                                <input
                                    type="range"
                                    min={0.5}
                                    max={2.0}
                                    step={0.1}
                                    value={settings.rfantibody_noise_scale_frame}
                                    onChange={(e) => updateSetting('rfantibody_noise_scale_frame', parseFloat(e.target.value))}
                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-pink-500"
                                />
                                <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                    <span>0.5 (consistent)</span>
                                    <span>1.0</span>
                                    <span>2.0 (diverse)</span>
                                </div>
                            </div>
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <label className="block text-xs text-slate-500 mb-1">
                                    Checkpoint Override
                                </label>
                                <input
                                    type="text"
                                    value={settings.rfantibody_ckpt_override}
                                    onChange={(e) => updateSetting('rfantibody_ckpt_override', e.target.value)}
                                    placeholder="optional .pt override path"
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300 font-mono"
                                />
                                <p className="text-[10px] text-slate-600 mt-1">
                                    Advanced/dev override for the RFantibody checkpoint path.
                                </p>
                            </div>

                            <div className="rounded-lg border border-slate-700/50 bg-slate-900/30 p-3">
                                <label className="flex items-center justify-between text-sm text-slate-300">
                                    <span>Debug Repo Overlay</span>
                                    <input
                                        type="checkbox"
                                        checked={settings.rfantibody_debug_repo_overlay}
                                        onChange={(e) => updateSetting('rfantibody_debug_repo_overlay', e.target.checked)}
                                        className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-pink-500 focus:ring-pink-500"
                                    />
                                </label>
                                <p className="mt-2 text-[10px] text-slate-600">
                                    Advanced/dev mode. Use the local RFantibody repo overlay instead of the normal packaged code path.
                                </p>
                            </div>
                        </div>
                    </div>
                    )}

                    {showStructureValidationSettings && (
                        <>
                            <div className="space-y-3 pt-3 border-t border-slate-700/50">
                        {structureValidator === 'protenix' ? (
                            <>
                                <div className="flex items-center gap-2 text-sm font-medium text-cyan-300">
                                    Structure Validation (Protenix)
                                    <a
                                        href="https://github.com/bytedance/Protenix"
                                        target="_blank"
                                        rel="noreferrer"
                                        className="text-xs text-slate-400 underline hover:text-cyan-200"
                                    >
                                        GitHub
                                    </a>
                                </div>
                                <p className="text-xs text-slate-500">
                                    These controls cover the Protenix inference CLI, anchored-target template conditioning, generic template DB usage, and optional OOM retry behavior:
                                    <code className="ml-1">--model_name</code>, <code>--sample</code>, <code>--step</code>, <code>--cycle</code>,
                                    <code> --use_msa</code>, <code>--use_template</code>, <code>--enable_cache</code>, and <code>--enable_fusion</code>.
                                    Shared workflow MSA settings live in the separate MSA section below.
                                </p>

                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <label className="block text-xs text-slate-500 mb-1">Model Variant</label>
                                        <select
                                            value={normalizeProtenixModel(settings.protenix_model_weights)}
                                            onChange={(e) => updateSetting('protenix_model_weights', e.target.value)}
                                            className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-slate-300"
                                        >
                                            <option value="protenix_base_20250630_v1.0.0">Base 2025-06-30 v1.0.0</option>
                                            <option value="protenix_base_default_v1.0.0">Base Default v1.0.0</option>
                                            <option value="protenix_mini_esm_v0.5.0">Mini ESM v0.5.0</option>
                                            <option value="protenix_mini_default_v0.5.0">Mini Default v0.5.0</option>
                                        </select>
                                        <p className="mt-1 text-[10px] text-slate-600">
                                            Base + MSA is the highest-fidelity option. Mini ESM is the lighter fallback for faster smoke tests and memory pressure.
                                        </p>
                                    </div>

                                    <div>
                                        <label className="block text-xs text-slate-500 mb-1">Seeds</label>
                                        <input
                                            type="text"
                                            value={settings.protenix_seeds}
                                            onChange={(e) => updateSetting('protenix_seeds', e.target.value.replace(/[^0-9,]/g, ''))}
                                            placeholder="42"
                                            className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-slate-300"
                                        />
                                        <p className="mt-1 text-[10px] text-slate-600">
                                            Comma-separated random seeds. More seeds broaden search but also multiply runtime.
                                        </p>
                                    </div>
                                </div>

                                <div className="grid grid-cols-3 gap-4">
                                    <div>
                                        <label className="block text-xs text-slate-500 mb-1">
                                            Samples / Seed <span className="text-slate-600">({settings.protenix_n_sample})</span>
                                        </label>
                                        <input
                                            type="range"
                                            min={1}
                                            max={12}
                                            step={1}
                                            value={settings.protenix_n_sample}
                                            onChange={(e) => updateSetting('protenix_n_sample', parseInt(e.target.value))}
                                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                        />
                                        <div className="mt-1 flex justify-between text-[10px] text-slate-600">
                                            <span>1</span>
                                            <span>6</span>
                                            <span>12</span>
                                        </div>
                                    </div>
                                    <div>
                                        <label className="block text-xs text-slate-500 mb-1">
                                            Diffusion Steps <span className="text-slate-600">({settings.protenix_n_step})</span>
                                        </label>
                                        <input
                                            type="range"
                                            min={50}
                                            max={400}
                                            step={25}
                                            value={settings.protenix_n_step}
                                            onChange={(e) => updateSetting('protenix_n_step', parseInt(e.target.value))}
                                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                        />
                                        <div className="mt-1 flex justify-between text-[10px] text-slate-600">
                                            <span>50</span>
                                            <span>200</span>
                                            <span>400</span>
                                        </div>
                                    </div>
                                    <div>
                                        <label className="block text-xs text-slate-500 mb-1">
                                            Recycle Iterations <span className="text-slate-600">({settings.protenix_n_cycle})</span>
                                        </label>
                                        <input
                                            type="range"
                                            min={1}
                                            max={16}
                                            step={1}
                                            value={settings.protenix_n_cycle}
                                            onChange={(e) => updateSetting('protenix_n_cycle', parseInt(e.target.value))}
                                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                        />
                                        <div className="mt-1 flex justify-between text-[10px] text-slate-600">
                                            <span>1</span>
                                            <span>8</span>
                                            <span>16</span>
                                        </div>
                                    </div>
                                </div>

                                <div className="grid grid-cols-2 gap-4">
                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={settings.protenix_use_msa}
                                            onChange={(e) => {
                                                const useMsa = e.target.checked;
                                                updateSetting('protenix_use_msa', useMsa);
                                                if (!useMsa && !normalizeProtenixModel(settings.protenix_model_weights).includes('esm')) {
                                                    updateSetting('protenix_model_weights', 'protenix_mini_esm_v0.5.0');
                                                }
                                            }}
                                            className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-cyan-500 focus:ring-cyan-500"
                                        />
                                        <span className="text-sm text-slate-300">
                                            Use MSA <span className="text-xs text-slate-500">(higher fidelity, more memory)</span>
                                        </span>
                                    </label>

                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={settings.protenix_use_template}
                                            onChange={(e) => updateSetting('protenix_use_template', e.target.checked)}
                                            className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-cyan-500 focus:ring-cyan-500"
                                        />
                                        <span className="text-sm text-slate-300">
                                            Use Template DB <span className="text-xs text-slate-500">(requires local mmCIF cache)</span>
                                        </span>
                                    </label>

                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={settings.protenix_anchor_target}
                                            onChange={(e) => updateSetting('protenix_anchor_target', e.target.checked)}
                                            className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-cyan-500 focus:ring-cyan-500"
                                        />
                                        <span className="text-sm text-slate-300">
                                            Anchor Experimental Target <span className="text-xs text-slate-500">(task-local target templates)</span>
                                        </span>
                                    </label>

                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={settings.protenix_enable_cache}
                                            onChange={(e) => updateSetting('protenix_enable_cache', e.target.checked)}
                                            className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-cyan-500 focus:ring-cyan-500"
                                        />
                                        <span className="text-sm text-slate-300">
                                            Enable Cache <span className="text-xs text-slate-500">(recommended default)</span>
                                        </span>
                                    </label>

                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={settings.protenix_enable_fusion}
                                            onChange={(e) => updateSetting('protenix_enable_fusion', e.target.checked)}
                                            className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-cyan-500 focus:ring-cyan-500"
                                        />
                                        <span className="text-sm text-slate-300">
                                            Enable Fusion <span className="text-xs text-slate-500">(recommended default)</span>
                                        </span>
                                    </label>
                                </div>

                                <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-3 space-y-3">
                                    <div className="text-xs font-medium text-slate-400">OOM Retry Guardrail (default off)</div>
                                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                                        <label className="flex items-center gap-2 cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={settings.protenix_auto_oom_retry}
                                                onChange={(e) => updateSetting('protenix_auto_oom_retry', e.target.checked)}
                                                className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-cyan-500 focus:ring-cyan-500"
                                            />
                                            <span className="text-sm text-slate-300">
                                                Auto OOM Retry <span className="text-xs text-slate-500">(downshifts samples/MSA on retry)</span>
                                            </span>
                                        </label>

                                        <div>
                                            <label className="block text-xs text-slate-500 mb-1">Retry Attempts</label>
                                            <input
                                                type="number"
                                                min={0}
                                                max={3}
                                                value={settings.protenix_oom_retry_attempts}
                                                onChange={(e) => updateSetting('protenix_oom_retry_attempts', Math.max(0, Math.min(3, parseInt(e.target.value, 10) || 0)))}
                                                disabled={!settings.protenix_auto_oom_retry}
                                                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300 disabled:opacity-50"
                                            />
                                        </div>
                                    </div>
                                </div>

                                {settings.protenix_use_template && (
                                    <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                                        Template mode requires local mmCIF data under <code>.protenix_cache/mmcif</code>. Submission is rejected if that cache is missing.
                                    </div>
                                )}
                                {settings.protenix_anchor_target && (
                                    <div className="rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-100">
                                        Anchored Protenix keeps the current sequence-only co-fold path but stages a task-local template DB built from the experimental target chains. The binder remains free while the target is template-conditioned.
                                    </div>
                                )}
                            </>
                        ) : (
                            <>
                                <div className="flex items-center gap-2 text-sm font-medium text-accent">
                                    Structure Prediction (Boltz-2)
                                </div>

                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <label className="block text-xs text-slate-500 mb-1">
                                            Sampling Steps <span className="text-slate-600">({settings.boltz_sampling_steps})</span>
                                        </label>
                                        <input
                                            type="range"
                                            min={50}
                                            max={1000}
                                            step={50}
                                            value={settings.boltz_sampling_steps}
                                            onChange={(e) => updateSetting('boltz_sampling_steps', parseInt(e.target.value))}
                                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-accent"
                                        />
                                        <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                            <span>50</span>
                                            <span>500</span>
                                            <span>1000</span>
                                        </div>
                                    </div>

                                    <div>
                                        <label className="block text-xs text-slate-500 mb-1">
                                            Recycling Steps <span className="text-slate-600">({settings.boltz_recycling_steps})</span>
                                        </label>
                                        <input
                                            type="range"
                                            min={1}
                                            max={10}
                                            step={1}
                                            value={settings.boltz_recycling_steps}
                                            onChange={(e) => updateSetting('boltz_recycling_steps', parseInt(e.target.value))}
                                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-accent"
                                        />
                                        <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                            <span>1</span>
                                            <span>5</span>
                                            <span>10</span>
                                        </div>
                                    </div>
                                </div>

                                <div>
                                    <label className="block text-xs text-slate-500 mb-1">
                                        Diffusion Samples <span className="text-slate-600">({settings.boltz_num_samples})</span>
                                    </label>
                                    <input
                                        type="range"
                                        min={1}
                                        max={10}
                                        step={1}
                                        value={settings.boltz_num_samples}
                                        onChange={(e) => updateSetting('boltz_num_samples', parseInt(e.target.value))}
                                        className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-accent"
                                    />
                                    <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                        <span>1 (fastest)</span>
                                        <span>5</span>
                                        <span>10 (most diverse)</span>
                                    </div>
                                </div>

                                <div className="flex gap-4">
                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={settings.boltz_use_potentials}
                                            onChange={(e) => updateSetting('boltz_use_potentials', e.target.checked)}
                                            className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-accent focus:ring-accent"
                                        />
                                        <span className="text-sm text-slate-300">
                                            Boltz-2x <span className="text-xs text-slate-500">(physics potentials)</span>
                                        </span>
                                    </label>

                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={settings.boltz_use_msa}
                                            onChange={(e) => updateSetting('boltz_use_msa', e.target.checked)}
                                            className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-accent focus:ring-accent"
                                        />
                                        <span className="text-sm text-slate-300">
                                            Use MSA <span className="text-xs text-slate-500">(better accuracy)</span>
                                        </span>
                                    </label>

                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={settings.boltz_predict_affinity}
                                            onChange={(e) => updateSetting('boltz_predict_affinity', e.target.checked)}
                                            className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-accent focus:ring-accent"
                                        />
                                        <span className="text-sm text-slate-300">
                                            Predict Affinity <span className="text-xs text-slate-500">(log₁₀ IC50)</span>
                                        </span>
                                    </label>
                                </div>

                                <div className="flex gap-4">
                                    <label className="flex items-center gap-2 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={settings.boltz_anchor_target}
                                            onChange={(e) => updateSetting('boltz_anchor_target', e.target.checked)}
                                            className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-accent focus:ring-accent"
                                        />
                                        <span className="text-sm text-slate-300">
                                            Anchor Experimental Target <span className="text-xs text-slate-500">(target templates only)</span>
                                        </span>
                                    </label>

                                </div>

                                {settings.boltz_anchor_target && (
                                    <div className="rounded-lg border border-accent/30 bg-accent/10 px-3 py-2 text-xs text-slate-200">
                                        Anchored Boltz injects templates only on the target chains and leaves the binder flexible.
                                    </div>
                                )}
                            </>
                        )}
                            </div>
                            {msaEnabled && (
                                <div className="space-y-3 pt-3 border-t border-slate-700/50">
                            <div className="flex items-center gap-2 text-sm font-medium text-sky-400">
                                MSA Settings
                            </div>
                            <p className="text-xs text-slate-500">
                                {structureValidator === 'protenix'
                                    ? 'Shared MSA controls for the Protenix validator path. These affect MSA preparation and provider/runtime behavior, not the core Protenix diffusion sampler.'
                                    : 'Shared MSA controls for the Boltz-2 validator path. These affect the representative MSA generated upstream of validation.'}
                            </p>

                            <div>
                                <label className="block text-xs text-slate-500 mb-2">MSA Search Mode</label>
                                <div className="grid grid-cols-3 gap-2">
                                    {(['maximum', 'balanced', 'fast'] as const).map((msaPreset) => {
                                        const isActive = settings.msa_preset === msaPreset;
                                        return (
                                            <button
                                                key={msaPreset}
                                                type="button"
                                                onClick={() => updateSetting('msa_preset', msaPreset)}
                                                className={`rounded-lg border p-3 text-left transition-colors ${isActive
                                                    ? 'border-sky-500 bg-sky-500/10 text-sky-200'
                                                    : 'border-slate-700 bg-slate-900/60 text-slate-300 hover:border-slate-600'
                                                    }`}
                                            >
                                                <div className="text-sm font-medium">{MSA_PRESET_INFO[msaPreset].label}</div>
                                                <div className="mt-1 text-[10px] opacity-80">{MSA_PRESET_INFO[msaPreset].description}</div>
                                            </button>
                                        );
                                    })}
                                </div>
                            </div>

                            {showProtenixMsaProvider && (
                                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                                    <div>
                                        <label className="block text-xs text-slate-500 mb-1">MSA Provider</label>
                                        <select
                                            value={settings.protenix_msa_backend}
                                            onChange={(e) => updateSetting('protenix_msa_backend', e.target.value as QualitySettings['protenix_msa_backend'])}
                                            className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300"
                                        >
                                            <option value="auto">Auto (workflow heuristic)</option>
                                            <option value="local">Local MMseqs2 (recommended)</option>
                                            <option value="colabfold_api">ColabFold API</option>
                                        </select>
                                        <p className="mt-1 text-[10px] text-slate-600">
                                            {settings.protenix_msa_backend === 'auto'
                                                ? 'Auto picks local or ColabFold API based on job size.'
                                                : settings.protenix_msa_backend === 'colabfold_api'
                                                    ? 'Use the configured ColabFold-compatible endpoint for Protenix MSA prep.'
                                                    : 'Use the local MMseqs/ColabFold DB stack mounted in BMS.'}
                                        </p>
                                    </div>

                                    {showRemoteMsaHost && (
                                        <div>
                                            <label className="block text-xs text-slate-500 mb-1">ColabFold API Host</label>
                                            <input
                                                type="text"
                                                value={settings.colabfold_api_host}
                                                onChange={(e) => updateSetting('colabfold_api_host', e.target.value)}
                                                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300 font-mono"
                                            />
                                            <p className="mt-1 text-[10px] text-slate-600">
                                                Used when the provider resolves to `colabfold_api`.
                                            </p>
                                        </div>
                                    )}
                                </div>
                            )}

                            {showLocalMsaRuntime && (
                                <div className="rounded-lg border border-slate-800 overflow-hidden">
                                    <button
                                        type="button"
                                        onClick={() => setShowMsaRuntimeOverrides((prev) => !prev)}
                                        className="w-full flex items-center justify-between px-3 py-2 bg-slate-950/40 hover:bg-slate-900/60 transition-colors"
                                    >
                                        <span className="text-xs font-medium text-slate-300">
                                            {structureValidator === 'protenix' ? 'Local MSA Runtime Overrides' : 'MSA Runtime Overrides'}
                                        </span>
                                        <span className="text-[10px] text-slate-500">
                                            {showMsaRuntimeOverrides ? 'Hide' : 'Show'} advanced
                                        </span>
                                    </button>

                                    {showMsaRuntimeOverrides && (
                                        <div className="p-3 space-y-3 bg-slate-950/30">
                                            <p className="text-[10px] text-slate-500">
                                                These are workflow runtime overrides for the local MSA path. Leave them blank/default unless you are debugging scheduler or DB behavior.
                                            </p>

                                            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                                                <div>
                                                    <label className="block text-xs text-slate-500 mb-1">Threads Override</label>
                                                    <input
                                                        type="number"
                                                        min={1}
                                                        value={settings.msa_threads ?? ''}
                                                        onChange={(e) => updateSetting('msa_threads', e.target.value ? Math.max(1, parseInt(e.target.value, 10) || 1) : null)}
                                                        placeholder="task default"
                                                        className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300"
                                                    />
                                                </div>

                                                <div>
                                                    <label className="block text-xs text-slate-500 mb-1">Use GPU for Local MSA</label>
                                                    <label className="flex items-center gap-2 rounded border border-slate-700 bg-slate-900 px-3 py-2 cursor-pointer">
                                                        <input
                                                            type="checkbox"
                                                            checked={settings.msa_use_gpu}
                                                            onChange={(e) => updateSetting('msa_use_gpu', e.target.checked)}
                                                            className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-sky-500 focus:ring-sky-500"
                                                        />
                                                        <span className="text-sm text-slate-300">Enable GPU path</span>
                                                    </label>
                                                </div>
                                            </div>

                                            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                                                <div>
                                                    <label className="block text-xs text-slate-500 mb-1">Local DB Path Override</label>
                                                    <input
                                                        type="text"
                                                        value={settings.msa_local_db}
                                                        onChange={(e) => updateSetting('msa_local_db', e.target.value)}
                                                        placeholder="system default"
                                                        className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300 font-mono"
                                                    />
                                                </div>

                                                <div>
                                                    <label className="block text-xs text-slate-500 mb-1">MSA Cache Dir Override</label>
                                                    <input
                                                        type="text"
                                                        value={settings.msa_cache_dir}
                                                        onChange={(e) => updateSetting('msa_cache_dir', e.target.value)}
                                                        placeholder="system default"
                                                        className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300 font-mono"
                                                    />
                                                </div>
                                            </div>

                                            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                                                <div>
                                                    <label className="block text-xs text-slate-500 mb-1">GPU Mode</label>
                                                    <select
                                                        value={settings.msa_gpu_mode}
                                                        onChange={(e) => updateSetting('msa_gpu_mode', e.target.value)}
                                                        className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300"
                                                    >
                                                        <option value="auto">Auto</option>
                                                        <option value="opportunistic">Opportunistic</option>
                                                        <option value="required">Required</option>
                                                        <option value="cpu">CPU Only</option>
                                                    </select>
                                                </div>

                                                <div>
                                                    <label className="block text-xs text-slate-500 mb-1">GPU Threshold (%)</label>
                                                    <input
                                                        type="number"
                                                        min={0}
                                                        max={100}
                                                        value={settings.msa_gpu_threshold}
                                                        onChange={(e) => updateSetting('msa_gpu_threshold', Math.max(0, Math.min(100, parseInt(e.target.value, 10) || 0)))}
                                                        className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300"
                                                    />
                                                </div>
                                            </div>

                                            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                                                <div>
                                                    <label className="block text-xs text-slate-500 mb-1">Preferred GPUs</label>
                                                    <input
                                                        type="text"
                                                        value={settings.msa_preferred_gpus}
                                                        onChange={(e) => updateSetting('msa_preferred_gpus', e.target.value.replace(/[^0-9,]/g, ''))}
                                                        placeholder="0,1"
                                                        className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300"
                                                    />
                                                </div>

                                                <div>
                                                    <label className="block text-xs text-slate-500 mb-1">Excluded GPUs</label>
                                                    <input
                                                        type="text"
                                                        value={settings.msa_excluded_gpus}
                                                        onChange={(e) => updateSetting('msa_excluded_gpus', e.target.value.replace(/[^0-9,]/g, ''))}
                                                        placeholder="2,3"
                                                        className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300"
                                                    />
                                                </div>
                                            </div>

                                            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                                                <div>
                                                    <label className="block text-xs text-slate-500 mb-1">GPU Server Mode</label>
                                                    <select
                                                        value={settings.msa_gpu_server_mode}
                                                        onChange={(e) => updateSetting('msa_gpu_server_mode', e.target.value)}
                                                        className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300"
                                                    >
                                                        <option value="auto">Auto</option>
                                                        <option value="required">Required</option>
                                                        <option value="persistent">Persistent</option>
                                                        <option value="off">Off</option>
                                                    </select>
                                                </div>

                                                <div>
                                                    <label className="block text-xs text-slate-500 mb-1">GPU Server Wait Timeout (s)</label>
                                                    <input
                                                        type="number"
                                                        min={-1}
                                                        value={settings.msa_gpu_server_wait_timeout}
                                                        onChange={(e) => {
                                                            const parsed = parseInt(e.target.value, 10);
                                                            updateSetting('msa_gpu_server_wait_timeout', Number.isFinite(parsed) ? Math.max(-1, parsed) : 120);
                                                        }}
                                                        className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300"
                                                    />
                                                </div>

                                                <div>
                                                    <label className="block text-xs text-slate-500 mb-1">GPU Server Startup Wait (s)</label>
                                                    <input
                                                        type="number"
                                                        min={0}
                                                        step={0.1}
                                                        value={settings.msa_gpu_server_startup_wait}
                                                        onChange={(e) => updateSetting('msa_gpu_server_startup_wait', Math.max(0, parseFloat(e.target.value) || 0))}
                                                        className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300"
                                                    />
                                                </div>
                                            </div>

                                            <div>
                                                <label className="block text-xs text-slate-500 mb-1">GPU Server DB Load Mode</label>
                                                <input
                                                    type="number"
                                                    min={0}
                                                    value={settings.msa_gpu_server_db_load_mode}
                                                    onChange={(e) => updateSetting('msa_gpu_server_db_load_mode', Math.max(0, Math.min(3, parseInt(e.target.value, 10) || 0)))}
                                                    className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-300"
                                                />
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}
                                </div>
                            )}
                        </>
                    )}

                    {/* FAMPNN Settings */}
                    {showFampnnSettings && (
                        <div className="space-y-3 pt-3 border-t border-slate-700/50">
                        <div className="flex items-center gap-2 text-sm font-medium text-blue-400">
                            Sequence Design (FAMPNN)
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <label className="block text-xs text-slate-500 mb-1">Checkpoint</label>
                                <select
                                    value={settings.fampnn_checkpoint}
                                    onChange={(e) => updateSetting('fampnn_checkpoint', e.target.value)}
                                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-slate-300"
                                >
                                    <option value="">Default: FAMPNN (0.0A)</option>
                                    {FAMPNN_CHECKPOINT_OPTIONS.map((option) => (
                                        <option key={option.value} value={option.value}>{option.label}</option>
                                    ))}
                                </select>
                                <p className="mt-1 text-[10px] text-slate-600">
                                    Defaults to FAMPNN (0.0A). Pick a different checkpoint or override path if needed.
                                </p>
                            </div>

                            <div>
                                <label className="block text-xs text-slate-500 mb-1">Checkpoint Path Override</label>
                                <input
                                    type="text"
                                    value={settings.fampnn_checkpoint_path}
                                    onChange={(e) => updateSetting('fampnn_checkpoint_path', e.target.value)}
                                    placeholder="/app/fampnn/weights/custom.pt"
                                    className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-slate-300 font-mono"
                                />
                                <p className="mt-1 text-[10px] text-slate-600">
                                    Optional manual path. If set, this overrides the checkpoint dropdown.
                                </p>
                            </div>
                        </div>

                        <div className="rounded-lg border border-slate-700/50 bg-slate-950/40 px-3 py-2">
                            <div className="text-[11px] font-medium text-slate-300">Checkpoint guidance</div>
                            <div className="mt-1 space-y-1 text-[10px] text-slate-500">
                                {FAMPNN_CHECKPOINT_OPTIONS.map((option) => (
                                    <div key={option.value}>
                                        <code className="text-slate-300">{option.value}</code>: {option.description}
                                    </div>
                                ))}
                            </div>
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <label className="block text-xs text-slate-500 mb-1">
                                    Temperature <span className="text-slate-600">({settings.fampnn_temperature.toFixed(4)})</span>
                                </label>
                                <input
                                    type="range"
                                    min={-4}
                                    max={0}
                                    step={0.1}
                                    value={Math.log10(settings.fampnn_temperature)}
                                    onChange={(e) => updateSetting('fampnn_temperature', Math.pow(10, parseFloat(e.target.value)))}
                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
                                />
                                <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                    <span>0.0001</span>
                                    <span>0.01</span>
                                    <span>1.0</span>
                                </div>
                                <div className="mt-2 flex flex-wrap gap-2">
                                    {FAMPNN_TEMPERATURE_PRESETS.map((preset) => {
                                        const isActive = Math.abs(settings.fampnn_temperature - preset.value) < 1e-9;
                                        return (
                                            <button
                                                key={preset.label}
                                                type="button"
                                                onClick={() => updateSetting('fampnn_temperature', preset.value)}
                                                className={`rounded border px-2 py-1 text-[10px] transition-colors ${isActive
                                                    ? 'border-blue-500 bg-blue-500/15 text-blue-300'
                                                    : 'border-slate-700 bg-slate-900/60 text-slate-400 hover:border-slate-600'
                                                    }`}
                                            >
                                                {preset.label} {preset.value}
                                            </button>
                                        );
                                    })}
                                </div>
                                <p className="mt-2 text-[10px] text-slate-600">
                                    Temperature is always controlled explicitly here and will not be reset by other controls.
                                </p>
                            </div>

                            <div>
                                <label className="block text-xs text-slate-500 mb-1">
                                    Denoising Steps <span className="text-slate-600">({settings.fampnn_num_steps})</span>
                                </label>
                                <input
                                    type="range"
                                    min={50}
                                    max={500}
                                    step={50}
                                    value={settings.fampnn_num_steps}
                                    onChange={(e) => updateSetting('fampnn_num_steps', parseInt(e.target.value))}
                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
                                />
                                <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                    <span>50</span>
                                    <span>250</span>
                                    <span>500</span>
                                </div>
                            </div>
                        </div>

                        <div>
                            <label className="block text-xs text-slate-500 mb-1">
                                PSCE Threshold <span className="text-slate-600">({settings.fampnn_psce_threshold.toFixed(2)})</span>
                            </label>
                            <input
                                type="range"
                                min={0.1}
                                max={0.5}
                                step={0.05}
                                value={settings.fampnn_psce_threshold}
                                onChange={(e) => updateSetting('fampnn_psce_threshold', parseFloat(e.target.value))}
                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
                            />
                            <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                <span>0.1 (strict)</span>
                                <span>0.3</span>
                                <span>0.5 (permissive)</span>
                            </div>
                        </div>

                        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                            <label className="flex items-start gap-3 rounded-lg border border-slate-700/60 bg-slate-950/40 px-3 py-3 text-sm text-slate-300">
                                <input
                                    type="checkbox"
                                    checked={settings.lock_target_chains}
                                    onChange={(e) => updateSetting('lock_target_chains', e.target.checked)}
                                    className="mt-0.5 h-4 w-4 rounded border-slate-600 bg-slate-800 text-blue-500 focus:ring-blue-500"
                                />
                                <span>
                                    <span className="font-medium text-slate-200">Lock target chains</span>
                                    <span className="mt-1 block text-[10px] text-slate-500">
                                        Keeps all non-antibody protein chains sequence-fixed during FAMPNN and maturation redesign so the antigen is never mutated.
                                    </span>
                                </span>
                            </label>

                            <label className="flex items-start gap-3 rounded-lg border border-slate-700/60 bg-slate-950/40 px-3 py-3 text-sm text-slate-300">
                                <input
                                    type="checkbox"
                                    checked={settings.lock_antibody_framework}
                                    onChange={(e) => updateSetting('lock_antibody_framework', e.target.checked)}
                                    className="mt-0.5 h-4 w-4 rounded border-slate-600 bg-slate-800 text-blue-500 focus:ring-blue-500"
                                />
                                <span>
                                    <span className="font-medium text-slate-200">Lock framework outside active CDRs</span>
                                    <span className="mt-1 block text-[10px] text-slate-500">
                                        Applies to CDR-focused redesign modes. Disable only if you explicitly want framework drift during FAMPNN/PPIFlow redesign.
                                    </span>
                                </span>
                            </label>
                        </div>
                        </div>
                    )}

                    {/* PPIFlow Maturation */}
                    <div className="space-y-3 pt-3 border-t border-slate-700/50">
                        <PPIFlowSettingsFields
                            settings={settings}
                            onSettingsChange={onSettingsChange}
                            allowPostPpiFlowRetry={allowPostPpiFlowRetry}
                        />
                    </div>

                    {/* Pre-validation Filtering (Compute Savings) */}
                    {showPreValidationFiltering && (
                    <div className="space-y-3 pt-3 border-t border-slate-700/50">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-sm font-medium text-green-400">
                                Pre-Validation Filtering
                                <span className="text-xs text-slate-500 font-normal">(saves compute)</span>
                            </div>
                            <label className="flex items-center gap-2 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={settings.fampnn_max_psce !== null}
                                    onChange={(e) => updateSetting('fampnn_max_psce', e.target.checked ? 2.0 : null)}
                                    className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-green-600 focus:ring-green-500"
                                />
                                <span className="text-sm text-slate-300">Enable</span>
                            </label>
                        </div>

                        {(settings.fampnn_max_psce !== null || settings.fampnn_max_residue_psce !== null) && (
                            <div className="space-y-4">
                                {/* Max Avg PSCE */}
                                <div>
                                    <div className="flex items-center justify-between mb-1">
                                        <label className="block text-xs text-slate-500">
                                            Max Avg PSCE <span className="text-slate-600">({settings.fampnn_max_psce?.toFixed(1) ?? 'off'})</span>
                                        </label>
                                        <label className="flex items-center gap-1 cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={settings.fampnn_max_psce !== null}
                                                onChange={(e) => updateSetting('fampnn_max_psce', e.target.checked ? 2.5 : null)}
                                                className="w-3 h-3 rounded border-slate-600 bg-slate-800 text-green-600"
                                            />
                                            <span className="text-[10px] text-slate-500">Enable</span>
                                        </label>
                                    </div>
                                    {settings.fampnn_max_psce !== null && (
                                        <input
                                            type="range"
                                            min={1.0}
                                            max={4.0}
                                            step={0.5}
                                            value={settings.fampnn_max_psce}
                                            onChange={(e) => updateSetting('fampnn_max_psce', parseFloat(e.target.value))}
                                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-green-500"
                                        />
                                    )}
                                </div>

                                {/* Max Residue PSCE */}
                                <div>
                                    <div className="flex items-center justify-between mb-1">
                                        <label className="block text-xs text-slate-500">
                                            Max Residue PSCE <span className="text-slate-600">({settings.fampnn_max_residue_psce?.toFixed(1) ?? 'off'})</span>
                                        </label>
                                        <label className="flex items-center gap-1 cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={settings.fampnn_max_residue_psce !== null}
                                                onChange={(e) => updateSetting('fampnn_max_residue_psce', e.target.checked ? 5.0 : null)}
                                                className="w-3 h-3 rounded border-slate-600 bg-slate-800 text-green-600"
                                            />
                                            <span className="text-[10px] text-slate-500">Enable</span>
                                        </label>
                                    </div>
                                    {settings.fampnn_max_residue_psce !== null && (
                                        <input
                                            type="range"
                                            min={2.0}
                                            max={8.0}
                                            step={0.5}
                                            value={settings.fampnn_max_residue_psce}
                                            onChange={(e) => updateSetting('fampnn_max_residue_psce', parseFloat(e.target.value))}
                                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-green-500"
                                        />
                                    )}
                                    <p className="text-[10px] text-slate-600 mt-1">
                                        Catches individual bad residues even if avg is OK
                                    </p>
                                </div>
                            </div>
                        )}
                    </div>
                    )}

                    {/* Post-Boltz Validation Filtering */}
                    {showPostValidationFiltering && (
                    <div className="space-y-3 pt-3 border-t border-slate-700/50">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-sm font-medium text-orange-400">
                                Post-Validation Filtering
                                <span className="text-xs text-slate-500 font-normal">(after {structureValidator === 'protenix' ? 'Protenix' : 'Boltz-2'})</span>
                            </div>
                            <label className="flex items-center gap-2 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={settings.boltz_max_binder_rmsd !== null || settings.boltz_min_ptm_interface !== null}
                                    onChange={(e) => {
                                        if (e.target.checked) {
                                            updateSetting('boltz_max_binder_rmsd', 2.0);
                                            updateSetting('boltz_min_ptm_interface', 0.5);
                                        } else {
                                            updateSetting('boltz_max_binder_rmsd', null);
                                            updateSetting('boltz_min_ptm_interface', null);
                                        }
                                    }}
                                    className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-orange-600 focus:ring-orange-500"
                                />
                                <span className="text-sm text-slate-300">Enable</span>
                            </label>
                        </div>

                        {(settings.boltz_max_binder_rmsd !== null || settings.boltz_min_ptm_interface !== null) && (
                            <div className="space-y-4">
                                <p className="text-xs text-slate-500">
                                    Filters designs after structure validation based on self-consistency (RMSD) and interface confidence (iPTM).
                                </p>

                                <div className="grid grid-cols-2 gap-4">
                                    {/* Max Binder RMSD */}
                                    <div>
                                        <div className="flex items-center justify-between mb-1">
                                            <label className="block text-xs text-slate-500">
                                                Max RMSD (Å) <span className="text-slate-600">({settings.boltz_max_binder_rmsd?.toFixed(1) ?? 'off'})</span>
                                            </label>
                                            <label className="flex items-center gap-1 cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={settings.boltz_max_binder_rmsd !== null}
                                                    onChange={(e) => updateSetting('boltz_max_binder_rmsd', e.target.checked ? 2.0 : null)}
                                                    className="w-3 h-3 rounded border-slate-600 bg-slate-800 text-orange-600"
                                                />
                                                <span className="text-[10px] text-slate-500">Enable</span>
                                            </label>
                                        </div>
                                        {settings.boltz_max_binder_rmsd !== null && (
                                            <>
                                                <input
                                                    type="range"
                                                    min={0.5}
                                                    max={5.0}
                                                    step={0.5}
                                                    value={settings.boltz_max_binder_rmsd}
                                                    onChange={(e) => updateSetting('boltz_max_binder_rmsd', parseFloat(e.target.value))}
                                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-orange-500"
                                                />
                                                <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                                    <span>0.5 (strict)</span>
                                                    <span>2.0</span>
                                                    <span>5.0 (permissive)</span>
                                                </div>
                                            </>
                                        )}
                                    </div>

                                    {/* Min iPTM Interface */}
                                    <div>
                                        <div className="flex items-center justify-between mb-1">
                                            <label className="block text-xs text-slate-500">
                                                Min iPTM <span className="text-slate-600">({settings.boltz_min_ptm_interface?.toFixed(2) ?? 'off'})</span>
                                            </label>
                                            <label className="flex items-center gap-1 cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={settings.boltz_min_ptm_interface !== null}
                                                    onChange={(e) => updateSetting('boltz_min_ptm_interface', e.target.checked ? 0.5 : null)}
                                                    className="w-3 h-3 rounded border-slate-600 bg-slate-800 text-orange-600"
                                                />
                                                <span className="text-[10px] text-slate-500">Enable</span>
                                            </label>
                                        </div>
                                        {settings.boltz_min_ptm_interface !== null && (
                                            <>
                                                <input
                                                    type="range"
                                                    min={0.3}
                                                    max={0.8}
                                                    step={0.05}
                                                    value={settings.boltz_min_ptm_interface}
                                                    onChange={(e) => updateSetting('boltz_min_ptm_interface', parseFloat(e.target.value))}
                                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-orange-500"
                                                />
                                                <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                                    <span>0.30 (permissive)</span>
                                                    <span>0.55</span>
                                                    <span>0.80 (strict)</span>
                                                </div>
                                            </>
                                        )}
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>
                    )}

                    {/* ThermoMPNN Stability Scoring */}
                    <div className="space-y-3 pt-3 border-t border-slate-700/50">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-sm font-medium text-amber-400">
                                ThermoMPNN
                                <span className="text-xs text-slate-500 font-normal">(stability scoring)</span>
                            </div>
                            <label className="flex items-center gap-2 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={settings.run_thermompnn}
                                    onChange={(e) => updateSetting('run_thermompnn', e.target.checked)}
                                    className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-amber-600 focus:ring-amber-500"
                                />
                                <span className="text-sm text-slate-300">Enable</span>
                            </label>
                        </div>

                        {settings.run_thermompnn && (
                            <div className="space-y-3">
                                <p className="text-xs text-slate-500">
                                    Scores sequence stability before structure validation. Lower ddG = more stable.
                                </p>

                                <div>
                                    <div className="flex items-center justify-between mb-1">
                                        <label className="block text-xs text-slate-500">
                                            Max ΔΔG (kcal/mol) <span className="text-slate-600">({settings.thermompnn_max_ddg?.toFixed(1) ?? 'scoring only'})</span>
                                        </label>
                                        <label className="flex items-center gap-1 cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={settings.thermompnn_max_ddg !== null}
                                                onChange={(e) => updateSetting('thermompnn_max_ddg', e.target.checked ? 5.0 : null)}
                                                className="w-3 h-3 rounded border-slate-600 bg-slate-800 text-amber-600"
                                            />
                                            <span className="text-[10px] text-slate-500">Filter</span>
                                        </label>
                                    </div>
                                    {settings.thermompnn_max_ddg !== null && (
                                        <>
                                            <input
                                                type="range"
                                                min={0}
                                                max={10}
                                                step={0.5}
                                                value={settings.thermompnn_max_ddg}
                                                onChange={(e) => updateSetting('thermompnn_max_ddg', parseFloat(e.target.value))}
                                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-amber-500"
                                            />
                                            <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                                <span>0 (very stable)</span>
                                                <span>5</span>
                                                <span>10 (permissive)</span>
                                            </div>
                                        </>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>

                    {/* AF2 Backprop CDR Refinement */}
                    <div className="space-y-3 pt-3 border-t border-slate-700/50">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-sm font-medium text-cyan-400">
                                AF2 Backprop
                                <span className="text-xs text-slate-500 font-normal">(CDR refinement)</span>
                            </div>
                            <label className="flex items-center gap-2 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={settings.run_af2_backprop}
                                    onChange={(e) => updateSetting('run_af2_backprop', e.target.checked)}
                                    className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-cyan-600 focus:ring-cyan-500"
                                />
                                <span className="text-sm text-slate-300">Enable</span>
                            </label>
                        </div>

                        {settings.run_af2_backprop && (
                            <div className="space-y-4">
                                <p className="text-xs text-slate-500">
                                    Uses AlphaFold-Multimer gradient descent to optimize CDR sequences for binding confidence.
                                </p>

                                {/* Optimization Stages */}
                                <div>
                                    <div className="text-xs font-medium text-cyan-400/70 mb-2">Optimization Stages (design_3stage)</div>
                                    <div className="grid grid-cols-3 gap-3">
                                        <div>
                                            <div className="flex items-center justify-between mb-1">
                                                <label className="text-xs text-slate-500">Soft</label>
                                                <span className="text-xs text-cyan-400">{settings.af2_backprop_soft_iters}</span>
                                            </div>
                                            <input
                                                type="range"
                                                min={10}
                                                max={200}
                                                step={10}
                                                value={settings.af2_backprop_soft_iters}
                                                onChange={(e) => updateSetting('af2_backprop_soft_iters', parseInt(e.target.value))}
                                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                            />
                                            <div className="text-[10px] text-slate-600">Continuous logits</div>
                                        </div>

                                        <div>
                                            <div className="flex items-center justify-between mb-1">
                                                <label className="text-xs text-slate-500">Temp</label>
                                                <span className="text-xs text-cyan-400">{settings.af2_backprop_temp_iters}</span>
                                            </div>
                                            <input
                                                type="range"
                                                min={10}
                                                max={200}
                                                step={10}
                                                value={settings.af2_backprop_temp_iters}
                                                onChange={(e) => updateSetting('af2_backprop_temp_iters', parseInt(e.target.value))}
                                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                            />
                                            <div className="text-[10px] text-slate-600">Annealing</div>
                                        </div>

                                        <div>
                                            <div className="flex items-center justify-between mb-1">
                                                <label className="text-xs text-slate-500">Hard</label>
                                                <span className="text-xs text-cyan-400">{settings.af2_backprop_hard_iters}</span>
                                            </div>
                                            <input
                                                type="range"
                                                min={5}
                                                max={50}
                                                step={5}
                                                value={settings.af2_backprop_hard_iters}
                                                onChange={(e) => updateSetting('af2_backprop_hard_iters', parseInt(e.target.value))}
                                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                            />
                                            <div className="text-[10px] text-slate-600">Discrete</div>
                                        </div>
                                    </div>
                                </div>

                                {/* Model Settings */}
                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <div className="flex items-center justify-between mb-1">
                                            <label className="text-xs text-slate-500">Recycles</label>
                                            <span className="text-xs text-cyan-400">{settings.af2_backprop_num_recycles}</span>
                                        </div>
                                        <input
                                            type="range"
                                            min={1}
                                            max={10}
                                            step={1}
                                            value={settings.af2_backprop_num_recycles}
                                            onChange={(e) => updateSetting('af2_backprop_num_recycles', parseInt(e.target.value))}
                                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                        />
                                    </div>

                                    <div>
                                        <div className="flex items-center justify-between mb-1">
                                            <label className="text-xs text-slate-500">Learning Rate</label>
                                            <span className="text-xs text-cyan-400">{settings.af2_backprop_learning_rate.toFixed(3)}</span>
                                        </div>
                                        <input
                                            type="range"
                                            min={0.01}
                                            max={0.2}
                                            step={0.01}
                                            value={settings.af2_backprop_learning_rate}
                                            onChange={(e) => updateSetting('af2_backprop_learning_rate', parseFloat(e.target.value))}
                                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                        />
                                    </div>
                                </div>

                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <div className="flex items-center justify-between mb-1">
                                            <label className="text-xs text-slate-500">Models</label>
                                            <span className="text-xs text-cyan-400">{settings.af2_backprop_num_models}</span>
                                        </div>
                                        <input
                                            type="range"
                                            min={1}
                                            max={5}
                                            step={1}
                                            value={settings.af2_backprop_num_models}
                                            onChange={(e) => updateSetting('af2_backprop_num_models', parseInt(e.target.value))}
                                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                        />
                                    </div>

                                    <label className="flex items-center gap-2 cursor-pointer pt-3">
                                        <input
                                            type="checkbox"
                                            checked={settings.af2_backprop_use_multimer}
                                            onChange={(e) => updateSetting('af2_backprop_use_multimer', e.target.checked)}
                                            className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-cyan-600 focus:ring-cyan-500"
                                        />
                                        <span className="text-xs text-slate-300">Use Multimer</span>
                                    </label>
                                </div>

                                {/* Loss Weights */}
                                <div>
                                    <div className="text-xs font-medium text-cyan-400/70 mb-2">Loss Weights</div>
                                    <div className="grid grid-cols-3 gap-3">
                                        <div>
                                            <div className="flex items-center justify-between mb-1">
                                                <label className="text-xs text-slate-500">pLDDT</label>
                                                <span className="text-xs text-cyan-400">{settings.af2_backprop_loss_plddt.toFixed(2)}</span>
                                            </div>
                                            <input
                                                type="range"
                                                min={0}
                                                max={1}
                                                step={0.05}
                                                value={settings.af2_backprop_loss_plddt}
                                                onChange={(e) => updateSetting('af2_backprop_loss_plddt', parseFloat(e.target.value))}
                                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                            />
                                        </div>

                                        <div>
                                            <div className="flex items-center justify-between mb-1">
                                                <label className="text-xs text-slate-500">PAE</label>
                                                <span className="text-xs text-cyan-400">{settings.af2_backprop_loss_pae.toFixed(2)}</span>
                                            </div>
                                            <input
                                                type="range"
                                                min={0}
                                                max={1}
                                                step={0.05}
                                                value={settings.af2_backprop_loss_pae}
                                                onChange={(e) => updateSetting('af2_backprop_loss_pae', parseFloat(e.target.value))}
                                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                            />
                                        </div>

                                        <div>
                                            <div className="flex items-center justify-between mb-1">
                                                <label className="text-xs text-slate-500">Contact</label>
                                                <span className="text-xs text-cyan-400">{settings.af2_backprop_loss_contact.toFixed(2)}</span>
                                            </div>
                                            <input
                                                type="range"
                                                min={0}
                                                max={1}
                                                step={0.05}
                                                value={settings.af2_backprop_loss_contact}
                                                onChange={(e) => updateSetting('af2_backprop_loss_contact', parseFloat(e.target.value))}
                                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                            />
                                        </div>
                                    </div>
                                    <div className="text-[10px] text-slate-600 mt-1">Confidence • Alignment Error • Interface Contacts</div>
                                </div>
                            </div>
                        )}
                    </div>


                    {/* Info Banner */}
                    {structureValidator !== 'protenix' && settings.boltz_use_potentials && (
                        <div className="p-3 bg-accent/10 border border-accent/30 rounded-lg">
                            <div className="flex items-start gap-2">
                                <div>
                                    <div className="text-sm font-medium text-accent">Boltz-2x Mode Active</div>
                                    <p className="text-xs text-slate-400">
                                        Physics-based potentials will improve structural quality but increase runtime.
                                    </p>
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export { PRESETS };
export default QualitySettingsPanel;
