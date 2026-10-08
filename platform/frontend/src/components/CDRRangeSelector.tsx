/**
 * CDRRangeSelector Component
 * Interactive residue grid for manually defining CDR regions on a framework structure
 * 
 * Features:
 * - Click to toggle single residue
 * - Shift+click for range selection
 * - "Define as CDR" dropdown to assign selected residues as H1, H2, H3, L1, L2, L3
 * - Visual highlighting of defined CDR regions with distinct colors
 * - Edit/delete existing CDR definitions
 */

import React, { useState } from 'react';
import type { Chain, Residue } from '../utils/pdbUtils';

// CDR Definition
export interface CDRDefinition {
    id: string;           // 'H1', 'H2', 'H3', 'L1', 'L2', 'L3', or custom
    name: string;         // Display name
    residues: Set<string>;// Set of residue IDs like 'A27', 'A28'...
    color: string;        // Display color
}

// CDR color presets
const CDR_COLORS: Record<string, { bg: string; border: string; text: string }> = {
    'H1': { bg: 'bg-blue-500/30', border: 'border-blue-500', text: 'text-blue-400' },
    'H2': { bg: 'bg-cyan-500/30', border: 'border-cyan-500', text: 'text-cyan-400' },
    'H3': { bg: 'bg-indigo-500/30', border: 'border-indigo-500', text: 'text-indigo-400' },
    'L1': { bg: 'bg-emerald-500/30', border: 'border-emerald-500', text: 'text-emerald-400' },
    'L2': { bg: 'bg-teal-500/30', border: 'border-teal-500', text: 'text-teal-400' },
    'L3': { bg: 'bg-green-500/30', border: 'border-green-500', text: 'text-green-400' },
    'custom': { bg: 'bg-amber-500/30', border: 'border-amber-500', text: 'text-amber-400' },
};

const CDR_OPTIONS = [
    { id: 'H1', name: 'CDR-H1', description: 'Heavy chain CDR1' },
    { id: 'H2', name: 'CDR-H2', description: 'Heavy chain CDR2' },
    { id: 'H3', name: 'CDR-H3', description: 'Heavy chain CDR3' },
    { id: 'L1', name: 'CDR-L1', description: 'Light chain CDR1' },
    { id: 'L2', name: 'CDR-L2', description: 'Light chain CDR2' },
    { id: 'L3', name: 'CDR-L3', description: 'Light chain CDR3' },
];

interface CDRRangeSelectorProps {
    chains: Chain[];
    cdrDefinitions: CDRDefinition[];
    onDefinitionsChange: (definitions: CDRDefinition[]) => void;
    activeChain?: string;
}

export function CDRRangeSelector({
    chains,
    cdrDefinitions,
    onDefinitionsChange,
    activeChain
}: CDRRangeSelectorProps) {
    // Selection state for pending CDR assignment
    const [selectedResidues, setSelectedResidues] = useState<Set<string>>(new Set());
    const [lastClickedResidue, setLastClickedResidue] = useState<string | null>(null);
    const [showDefineDropdown, setShowDefineDropdown] = useState(false);

    // Get the active chain's residues
    const displayChain = chains.find(c => c.id === activeChain) || chains[0];

    // Create residue key
    const getResKey = (r: Residue) => `${r.chainId}${r.resNum}${r.iCode || ''}`;
    const getResLabel = (r: Residue) => `${r.resNum}${r.iCode || ''}`;

    const parseResidueKey = (resKey: string): { chain: string; num: number; iCode: string } => {
        const match = resKey.match(/^([A-Za-z0-9])(\d+)([A-Za-z]?)$/);
        return {
            chain: match?.[1] || '',
            num: match?.[2] ? parseInt(match[2], 10) : Number.MAX_SAFE_INTEGER,
            iCode: match?.[3] || '',
        };
    };

    // Check if residue belongs to any CDR
    const getCDRForResidue = (resKey: string): CDRDefinition | null => {
        for (const cdr of cdrDefinitions) {
            if (cdr.residues.has(resKey)) return cdr;
        }
        return null;
    };

    // Handle residue click
    const handleResidueClick = (residue: Residue, event: React.MouseEvent) => {
        const resKey = getResKey(residue);
        const newSelection = new Set(selectedResidues);

        if (event.shiftKey && lastClickedResidue && displayChain) {
            // Range selection
            const residues = displayChain.residues;
            const lastIdx = residues.findIndex(r => getResKey(r) === lastClickedResidue);
            const currentIdx = residues.findIndex(r => getResKey(r) === resKey);

            if (lastIdx !== -1 && currentIdx !== -1) {
                const start = Math.min(lastIdx, currentIdx);
                const end = Math.max(lastIdx, currentIdx);
                for (let i = start; i <= end; i++) {
                    newSelection.add(getResKey(residues[i]));
                }
            }
        } else if (event.ctrlKey || event.metaKey) {
            // Toggle individual
            if (newSelection.has(resKey)) {
                newSelection.delete(resKey);
            } else {
                newSelection.add(resKey);
            }
        } else {
            // Single selection - toggle
            if (newSelection.has(resKey) && newSelection.size === 1) {
                newSelection.clear();
            } else {
                newSelection.clear();
                newSelection.add(resKey);
            }
        }

        setSelectedResidues(newSelection);
        setLastClickedResidue(resKey);
    };

    // Define selected residues as a CDR
    const handleDefineCDR = (cdrId: string) => {
        if (selectedResidues.size === 0) return;

        const cdrOption = CDR_OPTIONS.find(o => o.id === cdrId);
        const colors = CDR_COLORS[cdrId] || CDR_COLORS['custom'];

        // Remove this CDR from existing definitions (replace)
        const newDefs = cdrDefinitions.filter(d => d.id !== cdrId);

        // Add new definition
        newDefs.push({
            id: cdrId,
            name: cdrOption?.name || `CDR-${cdrId}`,
            residues: new Set(selectedResidues),
            color: colors.bg
        });

        // Sort by CDR order
        newDefs.sort((a, b) => {
            const order = ['H1', 'H2', 'H3', 'L1', 'L2', 'L3'];
            return order.indexOf(a.id) - order.indexOf(b.id);
        });

        onDefinitionsChange(newDefs);
        setSelectedResidues(new Set());
        setShowDefineDropdown(false);
    };

    // Delete a CDR definition
    const handleDeleteCDR = (cdrId: string) => {
        const newDefs = cdrDefinitions.filter(d => d.id !== cdrId);
        onDefinitionsChange(newDefs);
    };

    // Clear selection
    const handleClearSelection = () => {
        setSelectedResidues(new Set());
    };

    // Get already defined CDR IDs
    const definedCDRs = new Set(cdrDefinitions.map(d => d.id));

    if (!displayChain) {
        return (
            <div className="text-sm text-slate-500 italic py-4">
                Load a framework PDB to define CDR regions
            </div>
        );
    }

    return (
        <div className="space-y-4">
            {/* Header with selection info */}
            <div className="flex items-center justify-between">
                <div className="text-sm text-slate-400">
                    <span className="font-medium">Chain {displayChain.id}</span>
                    <span className="text-slate-500"> · {displayChain.length} residues</span>
                    {selectedResidues.size > 0 && (
                        <span className="ml-2 text-blue-400">
                            ({selectedResidues.size} selected)
                        </span>
                    )}
                </div>
                <div className="flex gap-2">
                    {selectedResidues.size > 0 && (
                        <>
                            <button
                                onClick={handleClearSelection}
                                className="text-xs px-2 py-1 text-slate-400 hover:text-slate-200"
                            >
                                Clear
                            </button>
                            <div className="relative">
                                <button
                                    onClick={() => setShowDefineDropdown(!showDefineDropdown)}
                                    className="text-xs px-3 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded"
                                >
                                    Define as CDR ▾
                                </button>
                                {showDefineDropdown && (
                                    <div className="absolute right-0 mt-1 w-48 bg-slate-800 border border-slate-600 rounded-lg shadow-xl z-20">
                                        {CDR_OPTIONS.map(opt => {
                                            const isDefined = definedCDRs.has(opt.id);
                                            const colors = CDR_COLORS[opt.id];
                                            return (
                                                <button
                                                    key={opt.id}
                                                    onClick={() => handleDefineCDR(opt.id)}
                                                    className={`w-full text-left px-3 py-2 text-sm hover:bg-slate-700 flex items-center justify-between ${colors.text}`}
                                                >
                                                    <span>{opt.name}</span>
                                                    {isDefined && (
                                                        <span className="text-xs text-slate-500">(replace)</span>
                                                    )}
                                                </button>
                                            );
                                        })}
                                    </div>
                                )}
                            </div>
                        </>
                    )}
                </div>
            </div>

            {/* Residue Grid */}
            <div className="bg-slate-900/50 rounded-lg p-3 max-h-48 overflow-y-auto">
                <div className="flex flex-wrap gap-1">
                    {displayChain.residues.map((residue) => {
                        const resKey = getResKey(residue);
                        const isSelected = selectedResidues.has(resKey);
                        const cdr = getCDRForResidue(resKey);
                        const cdrColors = cdr ? (CDR_COLORS[cdr.id] || CDR_COLORS['custom']) : null;

                        return (
                            <button
                                key={resKey}
                                onClick={(e) => handleResidueClick(residue, e)}
                                title={`${residue.resName} ${residue.resNum}${residue.iCode || ''} (${residue.chainId})`}
                                className={`
                                    w-8 h-6 text-[10px] font-mono rounded transition-all
                                    ${isSelected
                                        ? 'bg-blue-500 text-white ring-2 ring-blue-400 scale-110 z-10'
                                        : cdr
                                            ? `${cdrColors?.bg} ${cdrColors?.border} border ${cdrColors?.text}`
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                    }
                                `}
                            >
                                {getResLabel(residue)}
                            </button>
                        );
                    })}
                </div>
            </div>

            {/* Instructions */}
            <p className="text-xs text-slate-500">
                Click to select residues. Shift+click for range. Select residues then "Define as CDR".
            </p>

            {/* Defined CDRs List */}
            {cdrDefinitions.length > 0 && (
                <div className="space-y-2">
                    <div className="text-xs text-slate-500 uppercase tracking-wider">Defined CDRs</div>
                    <div className="flex flex-wrap gap-2">
                        {cdrDefinitions.map(cdr => {
                            const colors = CDR_COLORS[cdr.id] || CDR_COLORS['custom'];
                            const residueList = Array.from(cdr.residues).sort((a, b) => {
                                const parsedA = parseResidueKey(a);
                                const parsedB = parseResidueKey(b);
                                if (parsedA.chain !== parsedB.chain) {
                                    return parsedA.chain.localeCompare(parsedB.chain);
                                }
                                if (parsedA.num !== parsedB.num) {
                                    return parsedA.num - parsedB.num;
                                }
                                return parsedA.iCode.localeCompare(parsedB.iCode);
                            });
                            const range = residueList.length > 0
                                ? `${residueList[0]}-${residueList[residueList.length - 1]}`
                                : '';

                            return (
                                <div
                                    key={cdr.id}
                                    className={`flex items-center gap-2 px-3 py-1.5 rounded-lg ${colors.bg} border ${colors.border}`}
                                >
                                    <span className={`text-sm font-medium ${colors.text}`}>
                                        {cdr.name}
                                    </span>
                                    <span className="text-xs text-slate-400">
                                        {range} ({cdr.residues.size} AA)
                                    </span>
                                    <button
                                        onClick={() => handleDeleteCDR(cdr.id)}
                                        className="text-slate-500 hover:text-red-400 text-xs ml-1"
                                    >
                                        ✕
                                    </button>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}
        </div>
    );
}

export default CDRRangeSelector;
