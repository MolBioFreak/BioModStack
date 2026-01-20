/**
 * EpitopeSelector Component
 * Interactive grid for selecting epitope residues on antigen chains
 * 
 * Features:
 * - Click to toggle single residue
 * - Shift+click for range selection
 * - Ctrl+click for add/remove individual residues
 * - Color-coded by chain
 */

import { useState, useCallback, useMemo } from 'react';
import type { Chain, Residue } from '../utils/pdbUtils';

// Chain colors for visual distinction
const CHAIN_COLORS: Record<string, { bg: string; border: string; text: string }> = {
    'A': { bg: 'bg-blue-500/20', border: 'border-blue-500', text: 'text-blue-400' },
    'B': { bg: 'bg-emerald-500/20', border: 'border-emerald-500', text: 'text-emerald-400' },
    'C': { bg: 'bg-amber-500/20', border: 'border-amber-500', text: 'text-amber-400' },
    'D': { bg: 'bg-purple-500/20', border: 'border-purple-500', text: 'text-purple-400' },
    'E': { bg: 'bg-rose-500/20', border: 'border-rose-500', text: 'text-rose-400' },
    'F': { bg: 'bg-cyan-500/20', border: 'border-cyan-500', text: 'text-cyan-400' },
};

const DEFAULT_CHAIN_COLOR = { bg: 'bg-slate-500/20', border: 'border-slate-500', text: 'text-slate-400' };

interface EpitopeSelectorProps {
    chains: Chain[];
    selectedResidues: Set<string>;  // Set of "A45", "B100", etc.
    onSelectionChange: (residues: Set<string>) => void;
    activeChain?: string;  // Optional: limit to single chain
}

export function EpitopeSelector({
    chains,
    selectedResidues,
    onSelectionChange,
    activeChain
}: EpitopeSelectorProps) {
    const [lastClickedResidue, setLastClickedResidue] = useState<string | null>(null);

    // Filter chains if activeChain is specified
    const displayChains = useMemo(() =>
        activeChain ? chains.filter(c => c.id === activeChain) : chains,
        [chains, activeChain]
    );

    // Get all residues flat list for range selection
    const allResidues = useMemo(() =>
        displayChains.flatMap(chain => chain.residues),
        [displayChains]
    );

    // Create residue key
    const getResKey = (r: Residue) => `${r.chainId}${r.resNum}`;

    // Handle residue click
    const handleResidueClick = useCallback((residue: Residue, event: React.MouseEvent) => {
        const key = getResKey(residue);
        const newSelection = new Set(selectedResidues);

        if (event.shiftKey && lastClickedResidue) {
            // Range selection: select all between last clicked and current
            const lastIdx = allResidues.findIndex(r => getResKey(r) === lastClickedResidue);
            const currIdx = allResidues.findIndex(r => getResKey(r) === key);

            if (lastIdx !== -1 && currIdx !== -1) {
                const start = Math.min(lastIdx, currIdx);
                const end = Math.max(lastIdx, currIdx);

                for (let i = start; i <= end; i++) {
                    newSelection.add(getResKey(allResidues[i]));
                }
            }
        } else if (event.ctrlKey || event.metaKey) {
            // Toggle single residue (add or remove)
            if (newSelection.has(key)) {
                newSelection.delete(key);
            } else {
                newSelection.add(key);
            }
        } else {
            // Regular click: toggle single residue
            if (newSelection.has(key)) {
                newSelection.delete(key);
            } else {
                newSelection.add(key);
            }
        }

        setLastClickedResidue(key);
        onSelectionChange(newSelection);
    }, [selectedResidues, lastClickedResidue, allResidues, onSelectionChange]);

    // Get chain color
    const getChainColors = (chainId: string) => CHAIN_COLORS[chainId] || DEFAULT_CHAIN_COLOR;

    // Clear selection
    const handleClearSelection = () => {
        onSelectionChange(new Set());
        setLastClickedResidue(null);
    };

    if (displayChains.length === 0) {
        return (
            <div className="p-8 border border-dashed border-slate-700 rounded-lg text-center text-slate-500 text-sm">
                Upload a PDB file to select epitope residues
            </div>
        );
    }

    return (
        <div className="space-y-4">
            {/* Selection Controls */}
            <div className="flex justify-between items-center">
                <div className="flex items-center gap-4">
                    <span className="text-sm text-slate-400">
                        <span className="text-emerald-400 font-bold">{selectedResidues.size}</span> residues selected
                    </span>
                    <div className="text-xs text-slate-500">
                        Click: toggle • Shift+Click: range • Ctrl+Click: add/remove
                    </div>
                </div>
                {selectedResidues.size > 0 && (
                    <button
                        onClick={handleClearSelection}
                        className="text-xs text-red-400 hover:text-red-300 px-2 py-1 rounded hover:bg-red-500/10 transition-colors"
                    >
                        Clear Selection
                    </button>
                )}
            </div>

            {/* Chain Legend */}
            <div className="flex flex-wrap gap-2">
                {displayChains.map(chain => {
                    const colors = getChainColors(chain.id);
                    const chainSelectedCount = chain.residues.filter(r => selectedResidues.has(getResKey(r))).length;
                    return (
                        <div
                            key={chain.id}
                            className={`px-3 py-1.5 rounded-lg border ${colors.bg} ${colors.border} ${colors.text} text-sm flex items-center gap-2`}
                        >
                            <span className="font-bold">Chain {chain.id}</span>
                            <span className="text-xs opacity-70">({chain.length} aa)</span>
                            {chainSelectedCount > 0 && (
                                <span className="bg-white/20 px-1.5 py-0.5 rounded text-xs">
                                    {chainSelectedCount} sel
                                </span>
                            )}
                        </div>
                    );
                })}
            </div>

            {/* Residue Grid per Chain */}
            {displayChains.map(chain => {
                const colors = getChainColors(chain.id);
                const firstResNum = chain.residues[0]?.resNum ?? 1;
                return (
                    <div key={chain.id} className="space-y-2">
                        <div className={`text-xs font-medium ${colors.text} flex items-center gap-2`}>
                            Chain {chain.id}
                            <span className="text-slate-500 font-normal">
                                (residues {firstResNum}–{chain.residues[chain.residues.length - 1]?.resNum ?? firstResNum})
                            </span>
                        </div>
                        <div className="flex flex-wrap gap-x-0.5 gap-y-4 font-mono text-sm leading-none bg-slate-900/50 pt-5 pb-3 px-3 rounded-lg border border-slate-800 max-h-[400px] overflow-y-auto">
                            {chain.residues.map((residue) => {
                                const key = getResKey(residue);
                                const isSelected = selectedResidues.has(key);

                                return (
                                    <div key={key} className="relative group">
                                        {/* Position Marker - shows PDB residue number for every AA */}
                                        <div className="absolute -top-3.5 left-1/2 -translate-x-1/2 text-[8px] text-slate-600 select-none whitespace-nowrap">
                                            {residue.resNum}
                                        </div>

                                        <button
                                            onClick={(e) => handleResidueClick(residue, e)}
                                            className={`w-5 h-5 flex items-center justify-center rounded text-[10px] transition-all border ${isSelected
                                                ? `${colors.bg} ${colors.border} ${colors.text} scale-110 shadow-lg ring-1 ring-current/50`
                                                : 'bg-slate-800 border-transparent text-slate-400 hover:bg-slate-700 hover:border-slate-600'
                                                }`}
                                            title={`${chain.id}${residue.resNum} (${residue.resName})`}
                                        >
                                            {residue.aa}
                                        </button>

                                        {/* Selection indicator */}
                                        {isSelected && (
                                            <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-0.5 h-0.5 rounded-full bg-current" />
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                );
            })}

            {/* Selected Residues Summary */}
            {selectedResidues.size > 0 && (
                <div className="bg-slate-900/50 rounded-lg p-3 border border-slate-800">
                    <div className="text-xs text-slate-400 mb-2">Selected Epitope Residues:</div>
                    <div className="flex flex-wrap gap-1">
                        {Array.from(selectedResidues).sort().map(key => {
                            const chainId = key[0];
                            const colors = getChainColors(chainId);
                            return (
                                <span
                                    key={key}
                                    className={`px-2 py-0.5 rounded text-xs ${colors.bg} ${colors.text} border ${colors.border}`}
                                >
                                    {key}
                                </span>
                            );
                        })}
                    </div>
                </div>
            )}
        </div>
    );
}

export default EpitopeSelector;
