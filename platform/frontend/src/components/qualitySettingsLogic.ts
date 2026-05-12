import type { QualitySettings, PPIFlowStageMode, PPIFlowTuningProfile } from './QualitySettingsPanel';

const PRE_SEQUENCE_PPIFLOW_STAGE_MODES = new Set<PPIFlowStageMode>(['post_rfantibody', 'post_ppiflow']);
const POST_SEQUENCE_PPIFLOW_STAGE_MODES = new Set<PPIFlowStageMode>(['post_fampnn']);

export const getPpiFlowOptimizationScenario = (stageMode: PPIFlowStageMode): 'pre_sequence' | 'post_sequence' | null => {
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

export const PRESETS: Record<'speed' | 'balanced' | 'quality' | 'maximum', QualitySettings> = {
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
        caliby_model_name: 'soluble_caliby_v1',
        caliby_temperature: 0.1,
        caliby_batch_size: 4,
        caliby_num_workers: 8,
        caliby_clean_num_workers: 2,
        caliby_omit_aas: 'C',
        caliby_run_self_consistency_eval: false,
        caliby_self_consistency_num_models: 5,
        caliby_self_consistency_num_recycles: 3,
        caliby_self_consistency_use_multimer: false,
        enable_caliby_filter: false,
        caliby_max_potts_energy: null,
        caliby_min_sc_plddt: null,
        caliby_max_sc_rmsd: null,
        caliby_fixed_pos_override_seq: '',
        caliby_pos_restrict_aatype: '',
        caliby_symmetry_pos: '',
        caliby_sampling_overrides_json: '',
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
        caliby_model_name: 'soluble_caliby_v1',
        caliby_temperature: 0.1,
        caliby_batch_size: 4,
        caliby_num_workers: 8,
        caliby_clean_num_workers: 2,
        caliby_omit_aas: 'C',
        caliby_run_self_consistency_eval: false,
        caliby_self_consistency_num_models: 5,
        caliby_self_consistency_num_recycles: 3,
        caliby_self_consistency_use_multimer: false,
        enable_caliby_filter: false,
        caliby_max_potts_energy: null,
        caliby_min_sc_plddt: null,
        caliby_max_sc_rmsd: null,
        caliby_fixed_pos_override_seq: '',
        caliby_pos_restrict_aatype: '',
        caliby_symmetry_pos: '',
        caliby_sampling_overrides_json: '',
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
        caliby_model_name: 'soluble_caliby_v1',
        caliby_temperature: 0.08,
        caliby_batch_size: 4,
        caliby_num_workers: 8,
        caliby_clean_num_workers: 2,
        caliby_omit_aas: 'C',
        caliby_run_self_consistency_eval: false,
        caliby_self_consistency_num_models: 5,
        caliby_self_consistency_num_recycles: 3,
        caliby_self_consistency_use_multimer: false,
        enable_caliby_filter: false,
        caliby_max_potts_energy: null,
        caliby_min_sc_plddt: null,
        caliby_max_sc_rmsd: null,
        caliby_fixed_pos_override_seq: '',
        caliby_pos_restrict_aatype: '',
        caliby_symmetry_pos: '',
        caliby_sampling_overrides_json: '',
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
        caliby_model_name: 'soluble_caliby_v1',
        caliby_temperature: 0.06,
        caliby_batch_size: 4,
        caliby_num_workers: 8,
        caliby_clean_num_workers: 2,
        caliby_omit_aas: 'C',
        caliby_run_self_consistency_eval: false,
        caliby_self_consistency_num_models: 5,
        caliby_self_consistency_num_recycles: 3,
        caliby_self_consistency_use_multimer: false,
        enable_caliby_filter: false,
        caliby_max_potts_energy: null,
        caliby_min_sc_plddt: null,
        caliby_max_sc_rmsd: null,
        caliby_fixed_pos_override_seq: '',
        caliby_pos_restrict_aatype: '',
        caliby_symmetry_pos: '',
        caliby_sampling_overrides_json: '',
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
