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

    // FAMPNN settings (sequence design)
    fampnn_temperature: number;
    fampnn_num_steps: number;
    fampnn_psce_threshold: number;
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
        boltz_use_msa: false,
        boltz_step_scale: null,
        // FAMPNN
        fampnn_temperature: 0.2,
        fampnn_num_steps: 50,
        fampnn_psce_threshold: 0.4,
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
        boltz_use_msa: false,
        boltz_step_scale: null,
        // FAMPNN
        fampnn_temperature: 0.1,
        fampnn_num_steps: 100,
        fampnn_psce_threshold: 0.3,
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
        // FAMPNN
        fampnn_temperature: 0.01,
        fampnn_num_steps: 200,
        fampnn_psce_threshold: 0.2,
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
        // FAMPNN
        fampnn_temperature: 0.0001,
        fampnn_num_steps: 500,
        fampnn_psce_threshold: 0.15,
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
