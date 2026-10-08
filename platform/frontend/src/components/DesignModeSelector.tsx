import React from 'react';

interface DesignModeSelectorProps {
    mode: 'cdr_only' | 'cdr_selective' | 'framework_allowed' | 'full_design';
    onModeChange: (mode: 'cdr_only' | 'cdr_selective' | 'framework_allowed' | 'full_design') => void;
    selectedLoops: Set<string>;
    onLoopsChange: (loops: Set<string>) => void;
    protectTetrad: boolean;
    onProtectTetradChange: (protect: boolean) => void;
    frameworkType: 'standard-fv' | 'nanobody' | 'custom' | 'sabdab';
}

const DESIGN_MODES = [
    {
        id: 'cdr_only' as const,
        name: 'CDR Only',
        desc: 'Design CDR loops only (recommended)',
        color: 'emerald',
        warning: null
    },
    {
        id: 'cdr_selective' as const,
        name: 'CDR Selective',
        desc: 'Choose specific CDR loops',
        color: 'blue',
        warning: null
    },
    {
        id: 'framework_allowed' as const,
        name: 'Framework + CDRs',
        desc: 'Design framework regions too',
        color: 'amber',
        warning: 'VHH tetrad protected'
    },
    {
        id: 'full_design' as const,
        name: 'Full Design',
        desc: 'Everything designable (expert)',
        color: 'rose',
        warning: 'May destabilize structure!'
    }
];

const CDR_LOOPS = {
    heavy: ['H1', 'H2', 'H3'],
    light: ['L1', 'L2', 'L3']
};

export const DesignModeSelector: React.FC<DesignModeSelectorProps> = ({
    mode,
    onModeChange,
    selectedLoops,
    onLoopsChange,
    protectTetrad,
    onProtectTetradChange,
    frameworkType
}) => {
    const isVHH = frameworkType === 'nanobody' || frameworkType === 'sabdab';
    const availableLoops = isVHH ? CDR_LOOPS.heavy : [...CDR_LOOPS.heavy, ...CDR_LOOPS.light];

    const toggleLoop = (loop: string) => {
        const newLoops = new Set(selectedLoops);
        if (newLoops.has(loop)) {
            newLoops.delete(loop);
        } else {
            newLoops.add(loop);
        }
        onLoopsChange(newLoops);
    };

    const selectAllLoops = () => {
        onLoopsChange(new Set(availableLoops));
    };

    const clearAllLoops = () => {
        onLoopsChange(new Set());
    };

    // Color classes must be complete strings for Tailwind purging
    const colorClasses: Record<string, { selected: string; unselected: string }> = {
        emerald: {
            selected: 'bg-emerald-600/20 border-emerald-500 text-emerald-400',
            unselected: 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
        },
        blue: {
            selected: 'bg-blue-600/20 border-blue-500 text-blue-400',
            unselected: 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
        },
        amber: {
            selected: 'bg-amber-600/20 border-amber-500 text-amber-400',
            unselected: 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
        },
        rose: {
            selected: 'bg-rose-600/20 border-rose-500 text-rose-400',
            unselected: 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
        }
    };

    return (
        <div className="space-y-4">
            {/* Mode Selector */}
            <div>
                <label className="block text-sm font-medium text-slate-400 mb-2">
                    Sequence Design Mode
                </label>
                <div className="grid grid-cols-2 gap-2">
                    {DESIGN_MODES.map((m) => (
                        <button
                            key={m.id}
                            onClick={() => onModeChange(m.id)}
                            className={`p-3 rounded-lg border transition-all text-left ${mode === m.id
                                ? colorClasses[m.color].selected
                                : colorClasses[m.color].unselected
                                }`}
                        >
                            <div className="text-sm font-medium">{m.name}</div>
                            <div className="text-xs opacity-75">{m.desc}</div>
                            {m.warning && mode === m.id && (
                                <div className="text-xs mt-1 opacity-90">⚠️ {m.warning}</div>
                            )}
                        </button>
                    ))}
                </div>
            </div>

            {/* CDR Loop Selection (for cdr_selective mode) */}
            {mode === 'cdr_selective' && (
                <div className="bg-slate-900/50 border border-slate-700/50 rounded-lg p-4">
                    <div className="flex items-center justify-between mb-3">
                        <label className="text-sm font-medium text-slate-400">
                            Select CDR Loops to Design
                        </label>
                        <div className="flex gap-2">
                            <button
                                onClick={selectAllLoops}
                                className="text-xs px-2 py-1 bg-blue-500/20 text-blue-400 rounded hover:bg-blue-500/30"
                            >
                                Select All
                            </button>
                            <button
                                onClick={clearAllLoops}
                                className="text-xs px-2 py-1 bg-slate-700/50 text-slate-400 rounded hover:bg-slate-600/50"
                            >
                                Clear
                            </button>
                        </div>
                    </div>

                    {/* Heavy Chain CDRs */}
                    <div className="mb-3">
                        <div className="text-xs text-slate-500 mb-2">Heavy Chain</div>
                        <div className="flex gap-2">
                            {CDR_LOOPS.heavy.map((loop) => (
                                <button
                                    key={loop}
                                    onClick={() => toggleLoop(loop)}
                                    className={`px-4 py-2 rounded-lg font-medium transition-all ${selectedLoops.has(loop)
                                        ? 'bg-blue-600 text-white'
                                        : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                        }`}
                                >
                                    {loop}
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Light Chain CDRs (not for VHH) */}
                    {!isVHH && (
                        <div>
                            <div className="text-xs text-slate-500 mb-2">Light Chain</div>
                            <div className="flex gap-2">
                                {CDR_LOOPS.light.map((loop) => (
                                    <button
                                        key={loop}
                                        onClick={() => toggleLoop(loop)}
                                        className={`px-4 py-2 rounded-lg font-medium transition-all ${selectedLoops.has(loop)
                                            ? 'bg-accent text-white'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                            }`}
                                    >
                                        {loop}
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}

                    {selectedLoops.size === 0 && (
                        <div className="mt-3 text-xs text-amber-400">
                            ⚠️ Select at least one CDR loop to design
                        </div>
                    )}
                </div>
            )}

            {/* VHH Tetrad Protection Toggle */}
            {(mode === 'framework_allowed' || mode === 'full_design') && isVHH && (
                <div className="bg-slate-900/50 border border-slate-700/50 rounded-lg p-4">
                    <label className="flex items-center gap-3 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={protectTetrad}
                            onChange={(e) => onProtectTetradChange(e.target.checked)}
                            className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-amber-600 focus:ring-amber-500"
                        />
                        <div>
                            <span className="text-sm text-slate-300 font-medium">
                                Protect VHH Tetrad
                            </span>
                            <span className="text-xs text-slate-500 ml-2">
                                (FR2 positions 37, 44, 45, 47)
                            </span>
                        </div>
                    </label>
                    <p className="mt-2 text-xs text-slate-500">
                        These FR2 substitutions (V→F/Y, G→E, L→R, W→G) are critical for VHH solubility and stability.
                        Disabling protection may produce insoluble antibodies.
                    </p>
                    {!protectTetrad && (
                        <div className="mt-2 text-xs text-rose-400">
                            ⚠️ Warning: VHH tetrad will be designable. Use with caution!
                        </div>
                    )}
                </div>
            )}

            {/* Mode Summary */}
            <div className="text-xs text-slate-500">
                {mode === 'cdr_only' && (
                    <span>All CDR loops ({isVHH ? 'H1, H2, H3' : 'H1-H3, L1-L3'}) will be designed. Framework regions fixed.</span>
                )}
                {mode === 'cdr_selective' && (
                    <span>
                        Designing: {selectedLoops.size > 0 ? Array.from(selectedLoops).sort().join(', ') : 'None selected'}
                    </span>
                )}
                {mode === 'framework_allowed' && (
                    <span>CDRs and framework regions designable. {protectTetrad && isVHH ? 'VHH tetrad protected.' : ''}</span>
                )}
                {mode === 'full_design' && (
                    <span className="text-rose-400">All residues designable including conserved framework positions.</span>
                )}
            </div>
        </div>
    );
};

export default DesignModeSelector;
