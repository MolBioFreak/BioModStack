/**
 * FrameworkEditor - Interactive residue-level protection UI for antibody framework
 * 
 * Allows users to select which framework residues should be protected during design.
 * Provides presets for common protection patterns (tetrad, disulfides, FR contacts).
 */

import { useState, useMemo } from 'react';

// IMGT framework region definitions
const FR_REGIONS = {
    FR1: { start: 1, end: 26, label: 'FR1' },
    CDR1: { start: 27, end: 38, label: 'CDR1', isCDR: true },
    FR2: { start: 39, end: 55, label: 'FR2' },
    CDR2: { start: 56, end: 65, label: 'CDR2', isCDR: true },
    FR3: { start: 66, end: 104, label: 'FR3' },
    CDR3: { start: 105, end: 117, label: 'CDR3', isCDR: true },
    FR4: { start: 118, end: 128, label: 'FR4' },
};

// Key positions from Zavrtanik et al. 2018
const PROTECTION_PRESETS = {
    tetrad: {
        name: 'VHH Tetrad (FR2)',
        description: 'Positions 37, 44, 45, 47 - critical for VHH solubility',
        positions: [37, 44, 45, 47],
        color: 'purple'
    },
    fr2_contacts: {
        name: 'FR2 Contacts',
        description: 'Positions 37, 42, 44, 45, 47 - antigen contact sites',
        positions: [37, 42, 44, 45, 47],
        color: 'blue'
    },
    de_loop: {
        name: 'DE Loop',
        description: 'Positions 72-75 - antigen binding in nanobodies',
        positions: [72, 73, 74, 75],
        color: 'teal'
    },
    fr3_contacts: {
        name: 'FR3 Contacts',
        description: 'Positions 82-87 - framework contact hotspots',
        positions: [82, 83, 84, 85, 86, 87],
        color: 'indigo'
    },
    fr4_contacts: {
        name: 'FR4 Contacts',
        description: 'Positions 101-103 - C-terminal contacts',
        positions: [101, 102, 103],
        color: 'violet'
    },
    disulfides: {
        name: 'Disulfide Cysteines',
        description: 'Positions 23, 104 - conserved structural cysteines',
        positions: [23, 104],
        color: 'amber'
    },
    all_contacts: {
        name: 'All FR Contacts',
        description: 'All Zavrtanik 2018 framework hotspots',
        positions: [37, 42, 44, 45, 47, 72, 73, 74, 75, 82, 83, 84, 85, 86, 87, 101, 102, 103],
        color: 'emerald'
    }
};

export interface FrameworkEditorState {
    protectedPositions: number[];
    protectTetrad: boolean;
    protectDisulfides: boolean;
    protectFrContacts: boolean;
}

interface FrameworkEditorProps {
    state: FrameworkEditorState;
    onChange: (state: FrameworkEditorState) => void;
    frameworkType: 'standard-fv' | 'nanobody' | 'sabdab' | 'custom';
    compact?: boolean;
}

export function FrameworkEditor({
    state,
    onChange,
    frameworkType,
    compact = false
}: FrameworkEditorProps) {
    const [showAdvanced, setShowAdvanced] = useState(false);

    const isVHH = frameworkType === 'nanobody' || frameworkType === 'sabdab';

    // Compute all protected positions including presets
    const allProtectedPositions = useMemo(() => {
        const positions = new Set(state.protectedPositions);

        if (state.protectTetrad) {
            PROTECTION_PRESETS.tetrad.positions.forEach(p => positions.add(p));
        }
        if (state.protectDisulfides) {
            PROTECTION_PRESETS.disulfides.positions.forEach(p => positions.add(p));
        }
        if (state.protectFrContacts) {
            PROTECTION_PRESETS.all_contacts.positions.forEach(p => positions.add(p));
        }

        return Array.from(positions).sort((a, b) => a - b);
    }, [state]);

    const clearAll = () => {
        onChange({
            ...state,
            protectedPositions: [],
            protectTetrad: false,
            protectDisulfides: false,
            protectFrContacts: false
        });
    };

    // Compact view for inline use
    if (compact) {
        return (
            <div className="space-y-2">
                <div className="flex items-center justify-between">
                    <span className="text-sm text-slate-400">Framework Protection</span>
                    <span className="text-xs text-slate-500">
                        {allProtectedPositions.length} positions protected
                    </span>
                </div>
                <div className="flex flex-wrap gap-1">
                    {/* Quick toggles */}
                    <button
                        onClick={() => onChange({ ...state, protectTetrad: !state.protectTetrad })}
                        className={`px-2 py-1 text-xs rounded ${state.protectTetrad
                            ? 'bg-purple-500/20 text-purple-400 border border-purple-500/50'
                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                            }`}
                    >
                        Tetrad
                    </button>
                    <button
                        onClick={() => onChange({ ...state, protectDisulfides: !state.protectDisulfides })}
                        className={`px-2 py-1 text-xs rounded ${state.protectDisulfides
                            ? 'bg-amber-500/20 text-amber-400 border border-amber-500/50'
                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                            }`}
                    >
                        Disulfides
                    </button>
                    {isVHH && (
                        <button
                            onClick={() => onChange({ ...state, protectFrContacts: !state.protectFrContacts })}
                            className={`px-2 py-1 text-xs rounded ${state.protectFrContacts
                                ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/50'
                                : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                }`}
                        >
                            FR Contacts
                        </button>
                    )}
                    <button
                        onClick={() => setShowAdvanced(!showAdvanced)}
                        className="px-2 py-1 text-xs bg-slate-800 text-slate-400 rounded hover:bg-slate-700"
                    >
                        {showAdvanced ? 'Hide' : 'Advanced...'}
                    </button>
                </div>

                {showAdvanced && (
                    <div className="mt-2 p-2 bg-slate-900/50 rounded border border-slate-700">
                        <FrameworkEditorFull state={state} onChange={onChange} isVHH={isVHH} />
                    </div>
                )}
            </div>
        );
    }

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <label className="text-sm font-medium text-slate-400">
                    Framework Protection
                </label>
                <div className="flex items-center gap-2">
                    <span className="text-xs text-slate-500">
                        {allProtectedPositions.length} positions protected
                    </span>
                    <button
                        onClick={clearAll}
                        className="text-xs text-red-400 hover:text-red-300"
                    >
                        Clear All
                    </button>
                </div>
            </div>

            <FrameworkEditorFull state={state} onChange={onChange} isVHH={isVHH} />
        </div>
    );
}

// Full editor component
function FrameworkEditorFull({
    state,
    onChange,
    isVHH
}: {
    state: FrameworkEditorState;
    onChange: (state: FrameworkEditorState) => void;
    isVHH: boolean;
}) {
    const [manualPosition, setManualPosition] = useState('');

    const allProtectedPositions = useMemo(() => {
        const positions = new Set(state.protectedPositions);
        if (state.protectTetrad) {
            PROTECTION_PRESETS.tetrad.positions.forEach(p => positions.add(p));
        }
        if (state.protectDisulfides) {
            PROTECTION_PRESETS.disulfides.positions.forEach(p => positions.add(p));
        }
        if (state.protectFrContacts) {
            PROTECTION_PRESETS.all_contacts.positions.forEach(p => positions.add(p));
        }
        return Array.from(positions).sort((a, b) => a - b);
    }, [state]);

    const togglePosition = (pos: number) => {
        const currentPositions = new Set(state.protectedPositions);
        if (currentPositions.has(pos)) {
            currentPositions.delete(pos);
        } else {
            currentPositions.add(pos);
        }
        onChange({
            ...state,
            protectedPositions: Array.from(currentPositions).sort((a, b) => a - b)
        });
    };

    const addManualPosition = () => {
        const pos = parseInt(manualPosition);
        if (!isNaN(pos) && pos >= 1 && pos <= 128 && !state.protectedPositions.includes(pos)) {
            onChange({
                ...state,
                protectedPositions: [...state.protectedPositions, pos].sort((a, b) => a - b)
            });
            setManualPosition('');
        }
    };

    // Quick preset buttons
    const presetButtons = isVHH
        ? ['tetrad', 'de_loop', 'fr3_contacts', 'disulfides']
        : ['disulfides'];

    return (
        <div className="space-y-3">
            {/* Preset Buttons */}
            <div>
                <div className="text-xs text-slate-500 mb-2">Quick Presets</div>
                <div className="flex flex-wrap gap-2">
                    {presetButtons.map(key => {
                        const preset = PROTECTION_PRESETS[key as keyof typeof PROTECTION_PRESETS];
                        const isActive = preset.positions.every(p => allProtectedPositions.includes(p));
                        return (
                            <button
                                key={key}
                                onClick={() => {
                                    const positions = new Set(state.protectedPositions);
                                    if (isActive) {
                                        preset.positions.forEach(p => positions.delete(p));
                                    } else {
                                        preset.positions.forEach(p => positions.add(p));
                                    }
                                    onChange({
                                        ...state,
                                        protectedPositions: Array.from(positions).sort((a, b) => a - b)
                                    });
                                }}
                                title={preset.description}
                                className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${isActive
                                    ? `bg-${preset.color}-500/20 border-${preset.color}-500/50 text-${preset.color}-400`
                                    : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
                                    }`}
                            >
                                {preset.name}
                            </button>
                        );
                    })}
                </div>
            </div>

            {/* Manual Position Entry */}
            <div>
                <div className="text-xs text-slate-500 mb-2">Add Position (IMGT)</div>
                <div className="flex gap-2">
                    <input
                        type="number"
                        value={manualPosition}
                        onChange={e => setManualPosition(e.target.value)}
                        placeholder="1-128"
                        min={1}
                        max={128}
                        className="w-20 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-sm text-white"
                    />
                    <button
                        onClick={addManualPosition}
                        disabled={!manualPosition}
                        className="px-3 py-1 bg-blue-600 hover:bg-blue-500 text-white text-xs rounded disabled:opacity-50"
                    >
                        Add
                    </button>
                </div>
            </div>

            {/* Currently Protected Positions */}
            {allProtectedPositions.length > 0 && (
                <div>
                    <div className="text-xs text-slate-500 mb-2">Protected Positions</div>
                    <div className="flex flex-wrap gap-1 max-h-24 overflow-y-auto">
                        {allProtectedPositions.map(pos => {
                            // Determine which region this position belongs to
                            let region = 'FR';
                            for (const [name, def] of Object.entries(FR_REGIONS)) {
                                if (pos >= def.start && pos <= def.end) {
                                    region = name;
                                    break;
                                }
                            }
                            const isCDR = region.startsWith('CDR');

                            return (
                                <button
                                    key={pos}
                                    onClick={() => togglePosition(pos)}
                                    className={`px-2 py-0.5 text-xs rounded transition-colors ${isCDR
                                        ? 'bg-rose-500/20 text-rose-400 hover:bg-rose-500/30'
                                        : 'bg-blue-500/20 text-blue-400 hover:bg-blue-500/30'
                                        }`}
                                    title={`${region} - Click to remove`}
                                >
                                    {pos}
                                </button>
                            );
                        })}
                    </div>
                    <p className="text-xs text-slate-500 mt-1">Click position to remove</p>
                </div>
            )}

            {/* Framework Region Visualization */}
            <div>
                <div className="text-xs text-slate-500 mb-2">Framework Regions</div>
                <div className="flex gap-0.5 text-xs font-mono">
                    {Object.entries(FR_REGIONS).map(([name, def]) => {
                        const protectedInRegion = allProtectedPositions.filter(
                            p => p >= def.start && p <= def.end
                        ).length;
                        const totalInRegion = def.end - def.start + 1;
                        const pct = Math.round((protectedInRegion / totalInRegion) * 100);

                        return (
                            <div
                                key={name}
                                className={`px-2 py-1 rounded text-center ${'isCDR' in def && def.isCDR
                                    ? 'bg-rose-500/10 text-rose-400'
                                    : 'bg-blue-500/10 text-blue-400'
                                    }`}
                                style={{ minWidth: `${Math.max(totalInRegion, 20)}px` }}
                                title={`${name}: positions ${def.start}-${def.end} (${protectedInRegion}/${totalInRegion} protected)`}
                            >
                                <div className="text-[10px] opacity-75">{name}</div>
                                {protectedInRegion > 0 && (
                                    <div className="text-[10px] font-bold">{pct}%</div>
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}

export default FrameworkEditor;
