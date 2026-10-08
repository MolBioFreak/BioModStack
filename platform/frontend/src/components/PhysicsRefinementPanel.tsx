/**
 * Physics Refinement Panel
 * 
 * Provides UI controls for OpenMM physics-based structure refinement.
 * Used in antibody, binder, and mutagenesis design workflows.
 */
import React from 'react';

export interface PhysicsRefinementSettings {
    enabled: boolean;
    computeTier: 'fast' | 'standard' | 'full';
    cdrOnly: boolean;
    restraintMode: 'none' | 'framework' | 'backbone';
    mmgbsaMode: 'off' | 'interface' | 'stability' | 'both';
    forceField: 'amber14sb' | 'charmm36m';
    topNPercentage: number;
}

export const DEFAULT_SETTINGS: PhysicsRefinementSettings = {
    enabled: false,
    computeTier: 'fast',
    cdrOnly: true,
    restraintMode: 'framework',
    mmgbsaMode: 'off',
    forceField: 'amber14sb',
    topNPercentage: 10
};

interface PhysicsRefinementPanelProps {
    settings: PhysicsRefinementSettings;
    onSettingsChange: (settings: PhysicsRefinementSettings) => void;
    isAntibody?: boolean;  // Show CDR-specific options
}

export const PhysicsRefinementPanel: React.FC<PhysicsRefinementPanelProps> = ({
    settings,
    onSettingsChange,
    isAntibody = true
}) => {
    const updateSetting = <K extends keyof PhysicsRefinementSettings>(
        key: K,
        value: PhysicsRefinementSettings[K]
    ) => {
        onSettingsChange({ ...settings, [key]: value });
    };

    return (
        <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4">
            <div className="flex items-center justify-between mb-4">
                <div>
                    <h4 className="text-sm font-medium text-slate-300 flex items-center gap-2">
                        ⚛️ Physics Refinement (OpenMM)
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
                        <label className="block text-xs font-medium text-slate-400 mb-2">
                            Compute Tier
                        </label>
                        <div className="flex gap-2">
                            {(['fast', 'standard', 'full'] as const).map((tier) => (
                                <button
                                    key={tier}
                                    onClick={() => updateSetting('computeTier', tier)}
                                    className={`flex-1 px-3 py-2 rounded-lg font-medium text-sm transition-all ${settings.computeTier === tier
                                            ? tier === 'fast'
                                                ? 'bg-green-600/20 text-green-400 border border-green-500/50'
                                                : tier === 'standard'
                                                    ? 'bg-blue-600/20 text-blue-400 border border-blue-500/50'
                                                    : 'bg-purple-600/20 text-purple-400 border border-purple-500/50'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700 border border-transparent'
                                        }`}
                                >
                                    <div className="text-sm">{tier.charAt(0).toUpperCase() + tier.slice(1)}</div>
                                    <div className="text-[10px] opacity-75">
                                        {tier === 'fast' && '100 iter'}
                                        {tier === 'standard' && '500 iter'}
                                        {tier === 'full' && '+MM-GBSA'}
                                    </div>
                                </button>
                            ))}
                        </div>
                        <p className="mt-1 text-[10px] text-slate-500">
                            Fast: Quick clash resolution | Standard: Thorough minimization | Full: + binding affinity
                        </p>
                    </div>

                    {/* CDR-Only Mode (Antibody workflows) */}
                    {isAntibody && (
                        <div className="flex items-center justify-between">
                            <div>
                                <label className="text-xs font-medium text-slate-400">CDR-Only Mode</label>
                                <p className="text-[10px] text-slate-500">Only minimize CDR loops, preserve framework</p>
                            </div>
                            <button
                                onClick={() => updateSetting('cdrOnly', !settings.cdrOnly)}
                                className={`w-12 h-6 rounded-full transition-colors relative ${settings.cdrOnly ? 'bg-cyan-500' : 'bg-slate-600'
                                    }`}
                            >
                                <span className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${settings.cdrOnly ? 'left-7' : 'left-1'
                                    }`} />
                            </button>
                        </div>
                    )}

                    {/* Restraint Mode */}
                    <div>
                        <label className="block text-xs font-medium text-slate-400 mb-2">
                            Restraint Mode
                        </label>
                        <div className="flex gap-2">
                            {(['none', 'framework', 'backbone'] as const).map((mode) => (
                                <button
                                    key={mode}
                                    onClick={() => updateSetting('restraintMode', mode)}
                                    className={`flex-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${settings.restraintMode === mode
                                            ? 'bg-slate-600 text-white'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                        }`}
                                >
                                    {mode.charAt(0).toUpperCase() + mode.slice(1)}
                                </button>
                            ))}
                        </div>
                        <p className="mt-1 text-[10px] text-slate-500">
                            {settings.restraintMode === 'none' && 'No restraints - full structure relaxation'}
                            {settings.restraintMode === 'framework' && 'Restrain framework regions, relax CDRs'}
                            {settings.restraintMode === 'backbone' && 'Restrain backbone, relax sidechains only'}
                        </p>
                    </div>

                    {/* MM-GBSA Mode (shows with full tier) */}
                    {(settings.computeTier === 'full' || settings.mmgbsaMode !== 'off') && (
                        <div>
                            <label className="block text-xs font-medium text-slate-400 mb-2">
                                MM-GBSA Scoring
                            </label>
                            <div className="flex gap-2">
                                {(['off', 'interface', 'stability', 'both'] as const).map((mode) => (
                                    <button
                                        key={mode}
                                        onClick={() => updateSetting('mmgbsaMode', mode)}
                                        className={`flex-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${settings.mmgbsaMode === mode
                                                ? mode === 'off'
                                                    ? 'bg-slate-600 text-white'
                                                    : 'bg-amber-600/20 text-amber-400 border border-amber-500/50'
                                                : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                            }`}
                                    >
                                        {mode.charAt(0).toUpperCase() + mode.slice(1)}
                                    </button>
                                ))}
                            </div>
                            <p className="mt-1 text-[10px] text-slate-500">
                                Interface: Binding ΔG | Stability: Folding ΔG | Both: Full analysis
                            </p>
                        </div>
                    )}

                    {/* Top-N Gating (for expensive MM-GBSA) */}
                    {settings.mmgbsaMode !== 'off' && (
                        <div>
                            <label className="block text-xs font-medium text-slate-400 mb-2">
                                Top-N Gating: {settings.topNPercentage}%
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
                                Only run MM-GBSA on top {settings.topNPercentage}% of designs by iPTM
                            </p>
                        </div>
                    )}

                    {/* Force Field (Advanced) */}
                    <div className="pt-2 border-t border-slate-700/50">
                        <label className="block text-xs font-medium text-slate-400 mb-2">
                            Force Field
                        </label>
                        <div className="flex gap-2">
                            {(['amber14sb', 'charmm36m'] as const).map((ff) => (
                                <button
                                    key={ff}
                                    onClick={() => updateSetting('forceField', ff)}
                                    className={`px-4 py-1.5 rounded-lg text-xs font-medium transition-all ${settings.forceField === ff
                                            ? 'bg-slate-600 text-white'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                        }`}
                                >
                                    {ff.toUpperCase()}
                                </button>
                            ))}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default PhysicsRefinementPanel;
