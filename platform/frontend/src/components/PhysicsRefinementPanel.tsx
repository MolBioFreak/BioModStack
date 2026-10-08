/**
 * Physics Refinement Panel
 *
 * Provides UI controls for OpenMM physics-based structure refinement.
 * Used in antibody, binder, and mutagenesis design workflows.
 */
import React, { useState } from 'react';

// ============================================================================
// Types and Defaults
// ============================================================================

export interface PhysicsRefinementSettings {
    enabled: boolean;
    computeTier: 'fast' | 'standard' | 'full';
    cdrOnly: boolean;
    restraintMode: 'none' | 'framework' | 'backbone';
    mmgbsaMode: 'off' | 'interface' | 'stability' | 'both';
    forceField: 'amber14sb' | 'charmm36m';
    topNPercentage: number;
    // Advanced settings
    maxIterations: number;
    tolerance: number;  // kJ/mol/nm
    restraintStrength: number;  // kcal/mol/Å²
    implicitSolvent: 'gbsa' | 'vacuum' | 'obc2';
    platform: 'auto' | 'cuda' | 'cpu';
}

// Compute tier presets
const TIER_PRESETS = {
    fast: { maxIterations: 100, tolerance: 50.0 },
    standard: { maxIterations: 500, tolerance: 10.0 },
    full: { maxIterations: 1000, tolerance: 1.0 }
};

// ============================================================================
// Tooltip Component
// ============================================================================

const Tooltip: React.FC<{ text: string; children: React.ReactNode }> = ({ text, children }) => {
    const [show, setShow] = useState(false);
    return (
        <span className="relative inline-block">
            <span
                onMouseEnter={() => setShow(true)}
                onMouseLeave={() => setShow(false)}
                className="cursor-help"
            >
                {children}
            </span>
            {show && (
                <span className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 text-[10px] bg-slate-900 border border-slate-600 rounded shadow-lg text-slate-300 whitespace-nowrap max-w-xs">
                    {text}
                </span>
            )}
        </span>
    );
};

// ============================================================================
// Main Component
// ============================================================================

interface PhysicsRefinementPanelProps {
    settings: PhysicsRefinementSettings;
    onSettingsChange: (settings: PhysicsRefinementSettings) => void;
    isAntibody?: boolean;  // Show CDR-specific options
    showAdvanced?: boolean;  // Show advanced settings by default
}

export const PhysicsRefinementPanel: React.FC<PhysicsRefinementPanelProps> = ({
    settings,
    onSettingsChange,
    isAntibody = true,
    showAdvanced: initialShowAdvanced = false
}) => {
    const [showAdvanced, setShowAdvanced] = useState(initialShowAdvanced);

    const updateSetting = <K extends keyof PhysicsRefinementSettings>(
        key: K,
        value: PhysicsRefinementSettings[K]
    ) => {
        onSettingsChange({ ...settings, [key]: value });
    };

    const handleTierChange = (tier: 'fast' | 'standard' | 'full') => {
        const preset = TIER_PRESETS[tier];
        onSettingsChange({
            ...settings,
            computeTier: tier,
            maxIterations: preset.maxIterations,
            tolerance: preset.tolerance,
            mmgbsaMode: tier === 'full' && settings.mmgbsaMode === 'off' ? 'interface' : settings.mmgbsaMode
        });
    };

    return (
        <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4">
            <div className="flex items-center justify-between mb-4">
                <div>
                    <h4 className="text-sm font-medium text-slate-300 flex items-center gap-2">
                        <Tooltip text="OpenMM 8.4 physics engine: energy minimization, clash resolution, and binding affinity scoring">
                            ⚛️ Physics Refinement (OpenMM)
                        </Tooltip>
                        {settings.enabled && (
                            <span className="px-2 py-0.5 bg-cyan-500/20 text-cyan-400 text-xs rounded-full">
                                {settings.computeTier.toUpperCase()}
                            </span>
                        )}
                    </h4>
                    <p className="text-xs text-slate-500">
                        Energy minimization and binding affinity scoring
                    </p>
                </div>
                <button
                    onClick={() => updateSetting('enabled', !settings.enabled)}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${settings.enabled
                        ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30'
                        : 'bg-slate-700/50 text-slate-400 hover:bg-slate-600/50'
                        }`}
                >
                    {settings.enabled ? '⚛️ Enabled' : '+ Enable'}
                </button>
            </div>

            {settings.enabled && (
                <div className="space-y-4 animate-in fade-in slide-in-from-top-2 duration-200">
                    {/* Compute Tier */}
                    <div>
                        <label className="block text-xs font-medium text-slate-400 mb-2 flex items-center gap-1">
                            <Tooltip text="Controls the depth of energy minimization. Higher tiers are more accurate but slower.">
                                Compute Tier ⓘ
                            </Tooltip>
                        </label>
                        <div className="flex gap-2">
                            {(['fast', 'standard', 'full'] as const).map((tier) => (
                                <button
                                    key={tier}
                                    onClick={() => handleTierChange(tier)}
                                    className={`flex-1 px-3 py-2 rounded-lg font-medium text-sm transition-all ${settings.computeTier === tier
                                        ? tier === 'fast'
                                            ? 'bg-green-600/20 text-green-400 border border-green-500/50'
                                            : tier === 'standard'
                                                ? 'bg-blue-600/20 text-blue-400 border border-blue-500/50'
                                                : 'bg-accent/20 text-accent border border-accent/50'
                                        : 'bg-slate-800 text-slate-400 hover:bg-slate-700 border border-transparent'
                                        }`}
                                >
                                    <div className="text-sm">{tier.charAt(0).toUpperCase() + tier.slice(1)}</div>
                                    <div className="text-[10px] opacity-75">
                                        {tier === 'fast' && '100 iter, ~10s'}
                                        {tier === 'standard' && '500 iter, ~30s'}
                                        {tier === 'full' && '1000 iter + ΔG'}
                                    </div>
                                </button>
                            ))}
                        </div>
                        <p className="mt-1 text-[10px] text-slate-500">
                            Fast: Clash resolution | Standard: Thorough | Full: + MM-GBSA binding ΔG
                        </p>
                    </div>

                    {/* CDR-Only Mode (Antibody workflows) */}
                    {isAntibody && (
                        <div className="flex items-center justify-between">
                            <div>
                                <label className="text-xs font-medium text-slate-400 flex items-center gap-1">
                                    <Tooltip text="Only minimize CDR loops while keeping framework regions fixed. Preserves validated backbone geometry.">
                                        CDR-Only Mode ⓘ
                                    </Tooltip>
                                </label>
                                <p className="text-[10px] text-slate-500">Preserve framework, refine CDR loops only</p>
                            </div>
                            <button
                                onClick={() => updateSetting('cdrOnly', !settings.cdrOnly)}
                                className={`w-12 h-6 rounded-full transition-colors relative ${settings.cdrOnly ? 'bg-cyan-500' : 'bg-slate-600'}`}
                            >
                                <span className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${settings.cdrOnly ? 'left-7' : 'left-1'}`} />
                            </button>
                        </div>
                    )}

                    {/* Restraint Mode */}
                    <div>
                        <label className="block text-xs font-medium text-slate-400 mb-2 flex items-center gap-1">
                            <Tooltip text="Apply harmonic restraints to specific atoms. Prevents large structural deviations during minimization.">
                                Restraint Mode ⓘ
                            </Tooltip>
                        </label>
                        <div className="flex gap-2">
                            {([
                                { id: 'none', label: 'None', desc: 'Full relaxation' },
                                { id: 'framework', label: 'Framework', desc: 'Fix conserved regions' },
                                { id: 'backbone', label: 'Backbone', desc: 'Fix Cα atoms' }
                            ] as const).map(({ id, label, desc }) => (
                                <Tooltip key={id} text={desc}>
                                    <button
                                        onClick={() => updateSetting('restraintMode', id)}
                                        className={`flex-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${settings.restraintMode === id
                                            ? 'bg-slate-600 text-white'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                            }`}
                                    >
                                        {label}
                                    </button>
                                </Tooltip>
                            ))}
                        </div>
                    </div>

                    {/* Force Field */}
                    <div>
                        <label className="block text-xs font-medium text-slate-400 mb-2 flex items-center gap-1">
                            <Tooltip text="AMBER14SB: Fast, well-validated for proteins. CHARMM36m: Better for membrane proteins and disordered loops.">
                                Force Field ⓘ
                            </Tooltip>
                        </label>
                        <div className="flex gap-2">
                            {([
                                { id: 'amber14sb', label: 'AMBER14SB', desc: 'Standard, well-validated' },
                                { id: 'charmm36m', label: 'CHARMM36m', desc: 'Better for flexible loops' }
                            ] as const).map(({ id, label, desc }) => (
                                <Tooltip key={id} text={desc}>
                                    <button
                                        onClick={() => updateSetting('forceField', id)}
                                        className={`px-4 py-1.5 rounded-lg text-xs font-medium transition-all ${settings.forceField === id
                                            ? 'bg-slate-600 text-white'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                            }`}
                                    >
                                        {label}
                                    </button>
                                </Tooltip>
                            ))}
                        </div>
                    </div>

                    {/* MM-GBSA Mode */}
                    {(settings.computeTier === 'full' || settings.mmgbsaMode !== 'off') && (
                        <div className="pt-3 border-t border-slate-700/50">
                            <label className="block text-xs font-medium text-slate-400 mb-2 flex items-center gap-1">
                                <Tooltip text="MM-GBSA: Molecular Mechanics + Generalized Born Surface Area. Estimates binding free energy (ΔG). More negative = stronger binding.">
                                    MM-GBSA Binding Scoring ⓘ
                                </Tooltip>
                            </label>
                            <div className="flex gap-2">
                                {([
                                    { id: 'off', label: 'Off', desc: 'Skip binding energy calculation' },
                                    { id: 'interface', label: 'Interface', desc: 'Binding ΔG only' },
                                    { id: 'stability', label: 'Stability', desc: 'Folding ΔG only' },
                                    { id: 'both', label: 'Both', desc: 'Full thermodynamic analysis' }
                                ] as const).map(({ id, label, desc }) => (
                                    <Tooltip key={id} text={desc}>
                                        <button
                                            onClick={() => updateSetting('mmgbsaMode', id)}
                                            className={`flex-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${settings.mmgbsaMode === id
                                                ? id === 'off'
                                                    ? 'bg-slate-600 text-white'
                                                    : 'bg-amber-600/20 text-amber-400 border border-amber-500/50'
                                                : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                                }`}
                                        >
                                            {label}
                                        </button>
                                    </Tooltip>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Top-N Gating */}
                    {settings.mmgbsaMode !== 'off' && (
                        <div>
                            <label className="block text-xs font-medium text-slate-400 mb-2 flex items-center gap-1">
                                <Tooltip text="MM-GBSA is computationally expensive (~30s/structure). Only run on top designs by AI confidence (iPTM) to save time.">
                                    Top-N Gating: {settings.topNPercentage}% ⓘ
                                </Tooltip>
                            </label>
                            <input
                                type="range"
                                value={settings.topNPercentage}
                                onChange={(e) => updateSetting('topNPercentage', parseInt(e.target.value))}
                                min={5}
                                max={100}
                                step={5}
                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-amber-500"
                            />
                            <p className="mt-1 text-[10px] text-slate-500">
                                Run MM-GBSA on top {settings.topNPercentage}% of designs by iPTM score
                            </p>
                        </div>
                    )}

                    {/* Advanced Settings Toggle */}
                    <button
                        onClick={() => setShowAdvanced(!showAdvanced)}
                        className="w-full text-left text-xs text-slate-500 hover:text-slate-400 flex items-center gap-2 pt-2"
                    >
                        <span>{showAdvanced ? '▼' : '▶'}</span>
                        Advanced Settings
                    </button>

                    {showAdvanced && (
                        <div className="space-y-4 p-3 bg-slate-800/50 rounded-lg border border-slate-700/50">
                            {/* Max Iterations */}
                            <div>
                                <label className="block text-xs font-medium text-slate-400 mb-2 flex items-center gap-1">
                                    <Tooltip text="Maximum number of L-BFGS minimization steps. More iterations = lower energy but slower.">
                                        Max Iterations ⓘ
                                    </Tooltip>
                                </label>
                                <input
                                    type="number"
                                    value={settings.maxIterations}
                                    onChange={(e) => updateSetting('maxIterations', parseInt(e.target.value) || 500)}
                                    min={50}
                                    max={5000}
                                    step={50}
                                    className="w-32 bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-white"
                                />
                            </div>

                            {/* Tolerance */}
                            <div>
                                <label className="block text-xs font-medium text-slate-400 mb-2 flex items-center gap-1">
                                    <Tooltip text="Energy convergence threshold (kJ/mol/nm). Lower values = tighter convergence. Typical: 1-50.">
                                        Tolerance (kJ/mol/nm) ⓘ
                                    </Tooltip>
                                </label>
                                <input
                                    type="number"
                                    value={settings.tolerance}
                                    onChange={(e) => updateSetting('tolerance', parseFloat(e.target.value) || 10.0)}
                                    min={0.1}
                                    max={100}
                                    step={0.1}
                                    className="w-32 bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-white"
                                />
                            </div>

                            {/* Restraint Strength */}
                            <div>
                                <label className="block text-xs font-medium text-slate-400 mb-2 flex items-center gap-1">
                                    <Tooltip text="Strength of harmonic restraints (kcal/mol/Å²). Higher = stiffer. Typical: 0.5-5.0.">
                                        Restraint Strength ⓘ
                                    </Tooltip>
                                </label>
                                <input
                                    type="number"
                                    value={settings.restraintStrength}
                                    onChange={(e) => updateSetting('restraintStrength', parseFloat(e.target.value) || 1.0)}
                                    min={0.1}
                                    max={10}
                                    step={0.1}
                                    className="w-32 bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-white"
                                />
                            </div>

                            {/* Implicit Solvent */}
                            <div>
                                <label className="block text-xs font-medium text-slate-400 mb-2 flex items-center gap-1">
                                    <Tooltip text="Solvent model. GBSA: Generalized Born (faster). OBC2: Onufriev-Bashford-Case variant. Vacuum: No solvent effects.">
                                        Implicit Solvent ⓘ
                                    </Tooltip>
                                </label>
                                <div className="flex gap-2">
                                    {(['gbsa', 'obc2', 'vacuum'] as const).map((solvent) => (
                                        <button
                                            key={solvent}
                                            onClick={() => updateSetting('implicitSolvent', solvent)}
                                            className={`px-3 py-1 rounded text-xs ${settings.implicitSolvent === solvent
                                                ? 'bg-slate-600 text-white'
                                                : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                                }`}
                                        >
                                            {solvent.toUpperCase()}
                                        </button>
                                    ))}
                                </div>
                            </div>

                            {/* Platform */}
                            <div>
                                <label className="block text-xs font-medium text-slate-400 mb-2 flex items-center gap-1">
                                    <Tooltip text="OpenMM compute platform. Auto: Best available. CUDA: GPU (fastest). CPU: Fallback.">
                                        Compute Platform ⓘ
                                    </Tooltip>
                                </label>
                                <div className="flex gap-2">
                                    {(['auto', 'cuda', 'cpu'] as const).map((platform) => (
                                        <button
                                            key={platform}
                                            onClick={() => updateSetting('platform', platform)}
                                            className={`px-3 py-1 rounded text-xs ${settings.platform === platform
                                                ? 'bg-slate-600 text-white'
                                                : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                                }`}
                                        >
                                            {platform.toUpperCase()}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default PhysicsRefinementPanel;
