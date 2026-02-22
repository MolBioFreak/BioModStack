import { useState } from 'react';

// MSA Quality Settings Interface
export interface MSASettings {
    use_msa: boolean;
    msa_preset: 'maximum' | 'balanced' | 'fast';
    msa_use_expand?: boolean;
    msa_use_env?: boolean;
    msa_num_iterations?: number;
    msa_evalue?: number;
    msa_min_seq_id?: number;
    msa_min_coverage?: number;
    msa_taxon_list?: string;
    msa_min_depth_warning?: number;
    msa_min_depth_fail?: number;
    msa_force_refresh?: boolean;
    msa_allow_empty_fallback?: boolean;
}

// Default settings per workflow type
export const MSA_DEFAULTS: Record<string, MSASettings> = {
    structure_prediction: {
        use_msa: true,
        msa_preset: 'fast',
        msa_min_depth_warning: 100,
        msa_min_depth_fail: 0,
    },
    antibody_denovo: {
        use_msa: true,
        msa_preset: 'fast',
        msa_min_depth_warning: 50,
        msa_min_depth_fail: 0,
    },
    mutagenesis: {
        use_msa: true,
        msa_preset: 'fast',
        msa_min_depth_warning: 50,
        msa_min_depth_fail: 0,
    },
    bindcraft: {
        use_msa: true,
        msa_preset: 'fast',
        msa_min_depth_warning: 100,
        msa_min_depth_fail: 0,
    },
};

// Preset descriptions
const PRESET_INFO = {
    maximum: {
        label: 'Maximum',
        description: 'Full ColabFold with expansion (~30s)',
        icon: '🧬',
        details: 'EnvDB ✓ | Expansion ✓ | 3 iterations'
    },
    balanced: {
        label: 'Balanced',
        description: 'EnvDB without expansion (~15s)',
        icon: '⚖️',
        details: 'EnvDB ✓ | Expansion ✗ | 2 iterations'
    },
    fast: {
        label: 'Fast',
        description: 'UniRef30 only (~5s)',
        icon: '⚡',
        details: 'EnvDB ✗ | Expansion ✗ | 1 iteration'
    }
};

interface MSASettingsPanelProps {
    settings: MSASettings;
    onChange: (settings: MSASettings) => void;
    workflowType?: string;
    collapsed?: boolean;
    showAdvanced?: boolean;
    className?: string;
}

export function MSASettingsPanel({
    settings,
    onChange,
    workflowType: _workflowType = 'structure_prediction',  // Reserved for workflow-specific defaults
    collapsed: initialCollapsed = true,
    showAdvanced: showAdvancedProp = false,
    className = ''
}: MSASettingsPanelProps) {
    const [isCollapsed, setIsCollapsed] = useState(initialCollapsed);
    const [showAdvanced, setShowAdvanced] = useState(showAdvancedProp);

    // Helper to update settings
    const update = (partial: Partial<MSASettings>) => {
        onChange({ ...settings, ...partial });
    };

    return (
        <div className={`msa-settings-panel ${className}`}>
            {/* Header with collapse toggle */}
            <div
                className="flex items-center justify-between cursor-pointer p-3 bg-slate-800/50 rounded-t-lg border border-slate-700"
                onClick={() => setIsCollapsed(!isCollapsed)}
            >
                <div className="flex items-center gap-2">
                    <span className="text-lg">🧬</span>
                    <span className="font-medium text-slate-200">MSA Settings</span>
                    {settings.use_msa && (
                        <span className="text-xs px-2 py-0.5 bg-blue-600/30 text-blue-300 rounded">
                            {settings.msa_preset}
                        </span>
                    )}
                </div>
                <div className="flex items-center gap-3">
                    {/* Use MSA toggle */}
                    <label className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                        <input
                            type="checkbox"
                            checked={settings.use_msa}
                            onChange={(e) => update({ use_msa: e.target.checked })}
                            className="w-4 h-4 rounded border-slate-600 bg-slate-700"
                        />
                        <span className="text-sm text-slate-300">Enable</span>
                    </label>
                    <span className="text-slate-400">{isCollapsed ? '▼' : '▲'}</span>
                </div>
            </div>

            {/* Collapsible content */}
            {!isCollapsed && (
                <div className="p-4 bg-slate-800/30 border border-t-0 border-slate-700 rounded-b-lg space-y-4">
                    {/* Preset cards */}
                    {settings.use_msa && (
                        <>
                            <div className="grid grid-cols-3 gap-3">
                                {(['maximum', 'balanced', 'fast'] as const).map(preset => {
                                    const info = PRESET_INFO[preset];
                                    const isActive = settings.msa_preset === preset;
                                    return (
                                        <button
                                            key={preset}
                                            type="button"
                                            onClick={() => update({ msa_preset: preset })}
                                            className={`p-3 rounded-lg border-2 transition-all text-left ${isActive
                                                ? 'border-blue-500 bg-blue-600/20'
                                                : 'border-slate-600 bg-slate-700/30 hover:border-slate-500'
                                                }`}
                                        >
                                            <div className="flex items-center gap-2 mb-1">
                                                <span>{info.icon}</span>
                                                <span className={`font-medium ${isActive ? 'text-blue-300' : 'text-slate-200'}`}>
                                                    {info.label}
                                                </span>
                                            </div>
                                            <p className="text-xs text-slate-400">{info.description}</p>
                                            <p className="text-xs text-slate-500 mt-1">{info.details}</p>
                                        </button>
                                    );
                                })}
                            </div>

                            {/* Advanced options toggle */}
                            <button
                                type="button"
                                onClick={() => setShowAdvanced(!showAdvanced)}
                                className="flex items-center gap-2 text-sm text-slate-400 hover:text-slate-300"
                            >
                                <span>{showAdvanced ? '▼' : '▶'}</span>
                                <span>Advanced Options</span>
                            </button>

                            {/* Advanced options */}
                            {showAdvanced && (
                                <div className="space-y-4 pl-4 border-l-2 border-slate-700">
                                    {/* Expansion toggle (only relevant for maximum preset) */}
                                    <div className="flex items-center gap-3">
                                        <label className="flex items-center gap-2">
                                            <input
                                                type="checkbox"
                                                checked={settings.msa_use_expand ?? settings.msa_preset === 'maximum'}
                                                onChange={(e) => update({ msa_use_expand: e.target.checked })}
                                                className="w-4 h-4 rounded border-slate-600 bg-slate-700"
                                            />
                                            <span className="text-sm text-slate-300">Alignment Expansion</span>
                                        </label>
                                        <span className="text-xs text-slate-500">Uses _aln database for deeper coverage</span>
                                    </div>

                                    {/* Environmental DB toggle */}
                                    <div className="flex items-center gap-3">
                                        <label className="flex items-center gap-2">
                                            <input
                                                type="checkbox"
                                                checked={settings.msa_use_env ?? settings.msa_preset !== 'fast'}
                                                onChange={(e) => update({ msa_use_env: e.target.checked })}
                                                className="w-4 h-4 rounded border-slate-600 bg-slate-700"
                                            />
                                            <span className="text-sm text-slate-300">Environmental Database</span>
                                        </label>
                                        <span className="text-xs text-slate-500">Search colabfold_envdb for more sequences</span>
                                    </div>

                                    {/* Number of iterations */}
                                    <div className="flex items-center gap-3">
                                        <label className="text-sm text-slate-300 w-32">Iterations:</label>
                                        <input
                                            type="number"
                                            min={1}
                                            max={5}
                                            value={settings.msa_num_iterations ?? (settings.msa_preset === 'maximum' ? 3 : settings.msa_preset === 'balanced' ? 2 : 1)}
                                            onChange={(e) => update({ msa_num_iterations: parseInt(e.target.value) })}
                                            className="w-20 px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm"
                                        />
                                    </div>

                                    {/* E-value threshold */}
                                    <div className="flex items-center gap-3">
                                        <label className="text-sm text-slate-300 w-32">E-value:</label>
                                        <input
                                            type="number"
                                            step="0.001"
                                            min={0}
                                            max={1}
                                            value={settings.msa_evalue ?? 0.001}
                                            onChange={(e) => update({ msa_evalue: parseFloat(e.target.value) })}
                                            className="w-24 px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm"
                                        />
                                    </div>

                                    {/* Taxonomy filter */}
                                    <div className="flex items-center gap-3">
                                        <label className="text-sm text-slate-300 w-32">Taxonomy:</label>
                                        <input
                                            type="text"
                                            placeholder="e.g., 9606,10090"
                                            value={settings.msa_taxon_list ?? ''}
                                            onChange={(e) => update({ msa_taxon_list: e.target.value })}
                                            className="flex-1 px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm"
                                        />
                                        <span className="text-xs text-slate-500">NCBI IDs</span>
                                    </div>

                                    {/* Min Seq ID */}
                                    <div className="flex items-center gap-3">
                                        <label className="text-sm text-slate-300 w-32">Min Seq ID:</label>
                                        <input
                                            type="number"
                                            step="0.1"
                                            min={0}
                                            max={1}
                                            placeholder="0.0-1.0"
                                            value={settings.msa_min_seq_id ?? ''}
                                            onChange={(e) => update({ msa_min_seq_id: e.target.value ? parseFloat(e.target.value) : undefined })}
                                            className="w-24 px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm"
                                        />
                                    </div>

                                    {/* Min Coverage */}
                                    <div className="flex items-center gap-3">
                                        <label className="text-sm text-slate-300 w-32">Min Coverage:</label>
                                        <input
                                            type="number"
                                            step="0.1"
                                            min={0}
                                            max={1}
                                            placeholder="0.0-1.0"
                                            value={settings.msa_min_coverage ?? ''}
                                            onChange={(e) => update({ msa_min_coverage: e.target.value ? parseFloat(e.target.value) : undefined })}
                                            className="w-24 px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm"
                                        />
                                    </div>

                                    {/* Depth thresholds */}
                                    <div className="flex items-center gap-6">
                                        <div className="flex items-center gap-2">
                                            <label className="text-sm text-slate-300">Warn if depth &lt;</label>
                                            <input
                                                type="number"
                                                min={0}
                                                value={settings.msa_min_depth_warning ?? 100}
                                                onChange={(e) => update({ msa_min_depth_warning: parseInt(e.target.value) })}
                                                className="w-20 px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm"
                                            />
                                        </div>
                                        <div className="flex items-center gap-2">
                                            <label className="text-sm text-slate-300">Fail if depth &lt;</label>
                                            <input
                                                type="number"
                                                min={0}
                                                value={settings.msa_min_depth_fail ?? 0}
                                                onChange={(e) => update({ msa_min_depth_fail: parseInt(e.target.value) })}
                                                className="w-20 px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm"
                                            />
                                            <span className="text-xs text-slate-500">(0 = no fail)</span>
                                        </div>
                                    </div>

                                    {/* Force refresh */}
                                    <div className="flex items-center gap-3">
                                        <label className="flex items-center gap-2">
                                            <input
                                                type="checkbox"
                                                checked={settings.msa_force_refresh ?? false}
                                                onChange={(e) => update({ msa_force_refresh: e.target.checked })}
                                                className="w-4 h-4 rounded border-slate-600 bg-slate-700"
                                            />
                                            <span className="text-sm text-slate-300">Force Refresh</span>
                                        </label>
                                        <span className="text-xs text-slate-500">Ignore cached MSA</span>
                                    </div>

                                    {/* Empty fallback override */}
                                    <div className="flex items-center gap-3">
                                        <label className="flex items-center gap-2">
                                            <input
                                                type="checkbox"
                                                checked={settings.msa_allow_empty_fallback ?? false}
                                                onChange={(e) => update({ msa_allow_empty_fallback: e.target.checked })}
                                                className="w-4 h-4 rounded border-slate-600 bg-slate-700"
                                            />
                                            <span className="text-sm text-slate-300">Allow Empty MSA Fallback</span>
                                        </label>
                                        <span className="text-xs text-slate-500">Continue with `msa: empty` if chain MSA generation fails</span>
                                    </div>
                                </div>
                            )}
                        </>
                    )}

                    {/* Single-sequence mode info */}
                    {!settings.use_msa && (
                        <div className="p-3 bg-amber-900/20 border border-amber-700/50 rounded-lg">
                            <p className="text-sm text-amber-300">
                                <strong>Single-sequence mode:</strong> No MSA will be generated.
                                Structure prediction quality may be reduced for proteins with distant homologs.
                            </p>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

// Helper to extract MSA params for job submission
export function extractMSAParams(settings: MSASettings): Record<string, any> {
    if (!settings.use_msa) {
        return { boltz_use_msa: false };
    }

    const params: Record<string, any> = {
        boltz_use_msa: true,
        msa_preset: settings.msa_preset,
    };

    // Only include explicit overrides
    if (settings.msa_use_expand !== undefined) {
        params.msa_use_expand = settings.msa_use_expand;
    }
    if (settings.msa_use_env !== undefined) {
        params.msa_use_env = settings.msa_use_env;
    }
    if (settings.msa_num_iterations !== undefined) {
        params.msa_num_iterations = settings.msa_num_iterations;
    }
    if (settings.msa_evalue !== undefined) {
        params.msa_evalue = settings.msa_evalue;
    }
    if (settings.msa_taxon_list) {
        params.msa_taxon_list = settings.msa_taxon_list;
    }
    if (settings.msa_min_seq_id !== undefined) {
        params.msa_min_seq_id = settings.msa_min_seq_id;
    }
    if (settings.msa_min_coverage !== undefined) {
        params.msa_min_coverage = settings.msa_min_coverage;
    }
    if (settings.msa_min_depth_warning !== undefined) {
        params.msa_min_depth_warning = settings.msa_min_depth_warning;
    }
    if (settings.msa_min_depth_fail !== undefined) {
        params.msa_min_depth_fail = settings.msa_min_depth_fail;
    }
    if (settings.msa_force_refresh) {
        params.msa_force_refresh = true;
    }
    if (settings.msa_allow_empty_fallback) {
        params.msa_allow_empty_fallback = true;
    }

    return params;
}
