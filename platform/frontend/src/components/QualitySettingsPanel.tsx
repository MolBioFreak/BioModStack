import React, { useState } from 'react';

export interface QualitySettings {
    // RFantibody settings (backbone diffusion)
    rfantibody_diffusion_steps: number;
    rfantibody_noise_scale_ca: number;
    rfantibody_noise_scale_frame: number;
    rfantibody_guide_scale: number;

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

    // FAMPNN settings (sequence design)
    fampnn_temperature: number;
    fampnn_num_steps: number;
    fampnn_psce_threshold: number;

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
}

export type QualityPreset = 'speed' | 'balanced' | 'quality' | 'maximum';

const PRESETS: Record<QualityPreset, QualitySettings> = {
    speed: {
        // RFantibody: Fast screening
        rfantibody_diffusion_steps: 20,
        rfantibody_noise_scale_ca: 1.0,
        rfantibody_noise_scale_frame: 1.0,
        rfantibody_guide_scale: 10,
        // Boltz-2
        boltz_sampling_steps: 50,
        boltz_recycling_steps: 1,
        boltz_num_samples: 1,
        boltz_use_potentials: false,
        boltz_use_msa: true,
        boltz_step_scale: null,
        boltz_predict_affinity: false,
        boltz_diffusion_samples_affinity: 5,
        // FAMPNN
        fampnn_temperature: 0.2,
        fampnn_num_steps: 50,
        fampnn_psce_threshold: 0.4,
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
    },
    balanced: {
        // RFantibody: Default quality
        rfantibody_diffusion_steps: 50,
        rfantibody_noise_scale_ca: 1.0,
        rfantibody_noise_scale_frame: 1.0,
        rfantibody_guide_scale: 10,
        // Boltz-2
        boltz_sampling_steps: 200,
        boltz_recycling_steps: 3,
        boltz_num_samples: 1,
        boltz_use_potentials: false,
        boltz_use_msa: true,
        boltz_step_scale: null,
        boltz_predict_affinity: false,
        boltz_diffusion_samples_affinity: 5,
        // FAMPNN
        fampnn_temperature: 0.1,
        fampnn_num_steps: 100,
        fampnn_psce_threshold: 0.3,
        // Pre-Boltz filter: moderate filtering to save compute
        fampnn_max_psce: 2.5,
        fampnn_max_residue_psce: 5.0,
        // ThermoMPNN: enabled for balanced mode
        run_thermompnn: false,
        thermompnn_max_ddg: 5.0,  // kcal/mol, higher = more destabilizing
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
    },
    quality: {
        // RFantibody: Higher quality designs
        rfantibody_diffusion_steps: 100,
        rfantibody_noise_scale_ca: 0.8,
        rfantibody_noise_scale_frame: 0.8,
        rfantibody_guide_scale: 15,
        // Boltz-2
        boltz_sampling_steps: 500,
        boltz_recycling_steps: 5,
        boltz_num_samples: 3,
        boltz_use_potentials: true,
        boltz_use_msa: true,
        boltz_step_scale: null,
        boltz_predict_affinity: false,
        boltz_diffusion_samples_affinity: 5,
        // FAMPNN
        fampnn_temperature: 0.01,
        fampnn_num_steps: 200,
        fampnn_psce_threshold: 0.2,
        // Pre-Boltz filter: stricter filtering for quality runs
        fampnn_max_psce: 2.0,
        fampnn_max_residue_psce: 4.0,
        // ThermoMPNN: stricter for quality runs
        run_thermompnn: false,
        thermompnn_max_ddg: 3.0,
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
    },
    maximum: {
        // RFantibody: Best possible quality
        rfantibody_diffusion_steps: 200,
        rfantibody_noise_scale_ca: 0.7,
        rfantibody_noise_scale_frame: 0.7,
        rfantibody_guide_scale: 20,
        // Boltz-2
        boltz_sampling_steps: 1000,
        boltz_recycling_steps: 10,
        boltz_num_samples: 5,
        boltz_use_potentials: true,
        boltz_use_msa: true,
        boltz_step_scale: null,
        boltz_predict_affinity: true,
        boltz_diffusion_samples_affinity: 10,
        // FAMPNN
        fampnn_temperature: 0.0001,
        fampnn_num_steps: 500,
        fampnn_psce_threshold: 0.15,
        // Pre-Boltz filter: strictest filtering for maximum quality
        fampnn_max_psce: 1.5,
        fampnn_max_residue_psce: 3.0,
        // ThermoMPNN: strictest for maximum quality
        run_thermompnn: false,
        thermompnn_max_ddg: 2.0,
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
    },
};

const PRESET_INFO: Record<QualityPreset, { name: string; desc: string; time: string; color: string }> = {
    speed: { name: '⚡ Speed', desc: 'Fast screening', time: '~5 min', color: 'cyan' },
    balanced: { name: '⚖️ Balanced', desc: 'Default settings', time: '~15 min', color: 'blue' },
    quality: { name: '✨ Quality', desc: 'Higher accuracy', time: '~45 min', color: 'purple' },
    maximum: { name: '🔬 Maximum', desc: 'Best possible', time: '~2+ hrs', color: 'rose' },
};

interface QualitySettingsPanelProps {
    settings: QualitySettings;
    onSettingsChange: (settings: QualitySettings) => void;
    preset: QualityPreset;
    onPresetChange: (preset: QualityPreset) => void;
}

export const QualitySettingsPanel: React.FC<QualitySettingsPanelProps> = ({
    settings,
    onSettingsChange,
    preset,
    onPresetChange,
}) => {
    const [isExpanded, setIsExpanded] = useState(false);

    const handlePresetSelect = (newPreset: QualityPreset) => {
        onPresetChange(newPreset);
        onSettingsChange(PRESETS[newPreset]);
    };

    const updateSetting = <K extends keyof QualitySettings>(key: K, value: QualitySettings[K]) => {
        onSettingsChange({ ...settings, [key]: value });
    };

    // Color classes for Tailwind
    const colorClasses: Record<string, { selected: string; unselected: string }> = {
        cyan: {
            selected: 'bg-cyan-600/20 border-cyan-500 text-cyan-400',
            unselected: 'bg-slate-800 border-slate-700 text-slate-400 hover:border-cyan-600/50',
        },
        blue: {
            selected: 'bg-blue-600/20 border-blue-500 text-blue-400',
            unselected: 'bg-slate-800 border-slate-700 text-slate-400 hover:border-blue-600/50',
        },
        purple: {
            selected: 'bg-purple-600/20 border-purple-500 text-purple-400',
            unselected: 'bg-slate-800 border-slate-700 text-slate-400 hover:border-purple-600/50',
        },
        rose: {
            selected: 'bg-rose-600/20 border-rose-500 text-rose-400',
            unselected: 'bg-slate-800 border-slate-700 text-slate-400 hover:border-rose-600/50',
        },
    };

    return (
        <div className="bg-slate-900/50 border border-slate-700/50 rounded-lg overflow-hidden">
            {/* Header */}
            <button
                onClick={() => setIsExpanded(!isExpanded)}
                className="w-full flex items-center justify-between p-4 hover:bg-slate-800/30 transition-colors"
            >
                <div className="flex items-center gap-3">
                    <span className="text-lg">🎚️</span>
                    <div className="text-left">
                        <h3 className="text-sm font-medium text-slate-300">Quality Settings</h3>
                        <p className="text-xs text-slate-500">
                            {PRESET_INFO[preset].name} mode • {PRESET_INFO[preset].time}
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
                    {/* Preset Selector */}
                    <div>
                        <label className="block text-xs font-medium text-slate-500 mb-2">Quick Presets</label>
                        <div className="grid grid-cols-4 gap-2">
                            {(Object.keys(PRESETS) as QualityPreset[]).map((p) => {
                                const info = PRESET_INFO[p];
                                const colors = colorClasses[info.color];
                                return (
                                    <button
                                        key={p}
                                        onClick={() => handlePresetSelect(p)}
                                        className={`p-2 rounded-lg border transition-all text-center ${preset === p ? colors.selected : colors.unselected
                                            }`}
                                    >
                                        <div className="text-sm font-medium">{info.name}</div>
                                        <div className="text-xs opacity-75">{info.time}</div>
                                    </button>
                                );
                            })}
                        </div>
                    </div>

                    {/* RFantibody Settings */}
                    <div className="space-y-3">
                        <div className="flex items-center gap-2 text-sm font-medium text-pink-400">
                            <span>💉</span>
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
                                    step={10}
                                    value={settings.rfantibody_diffusion_steps}
                                    onChange={(e) => updateSetting('rfantibody_diffusion_steps', parseInt(e.target.value))}
                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-pink-500"
                                />
                                <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                    <span>20 (fast)</span>
                                    <span>100</span>
                                    <span>200 (quality)</span>
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
                    </div>

                    {/* Boltz-2 Settings */}
                    <div className="space-y-3 pt-3 border-t border-slate-700/50">
                        <div className="flex items-center gap-2 text-sm font-medium text-purple-400">
                            <span>🧬</span>
                            Structure Prediction (Boltz-2)
                        </div>

                        {/* Sampling Steps */}
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
                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-purple-500"
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
                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-purple-500"
                                />
                                <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                    <span>1</span>
                                    <span>5</span>
                                    <span>10</span>
                                </div>
                            </div>
                        </div>

                        {/* Diffusion Samples */}
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
                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-purple-500"
                            />
                            <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                                <span>1 (fastest)</span>
                                <span>5</span>
                                <span>10 (most diverse)</span>
                            </div>
                        </div>

                        {/* Toggles */}
                        <div className="flex gap-4">
                            <label className="flex items-center gap-2 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={settings.boltz_use_potentials}
                                    onChange={(e) => updateSetting('boltz_use_potentials', e.target.checked)}
                                    className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-purple-600 focus:ring-purple-500"
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
                                    className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-purple-600 focus:ring-purple-500"
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
                                    className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-purple-600 focus:ring-purple-500"
                                />
                                <span className="text-sm text-slate-300">
                                    Predict Affinity <span className="text-xs text-slate-500">(log₁₀ IC50)</span>
                                </span>
                            </label>
                        </div>
                    </div>

                    {/* FAMPNN Settings */}
                    <div className="space-y-3 pt-3 border-t border-slate-700/50">
                        <div className="flex items-center gap-2 text-sm font-medium text-blue-400">
                            <span>🔬</span>
                            Sequence Design (FAMPNN)
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
                    </div>

                    {/* Pre-Boltz Filtering (Compute Savings) */}
                    <div className="space-y-3 pt-3 border-t border-slate-700/50">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-sm font-medium text-green-400">
                                Pre-Boltz Filtering
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
                                    Scores sequence stability before Boltz-2 validation. Lower ddG = more stable.
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
                                🧪 AF2 Backprop
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
                    {settings.boltz_use_potentials && (
                        <div className="p-3 bg-purple-500/10 border border-purple-500/30 rounded-lg">
                            <div className="flex items-start gap-2">
                                <span className="text-purple-400">✨</span>
                                <div>
                                    <div className="text-sm font-medium text-purple-400">Boltz-2x Mode Active</div>
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
