/**
 * ChainDetailsPanel - Displays per-chain metrics for multi-chain complexes.
 * 
 * Shows chain type icons, length, pTM scores, and optionally an inter-chain
 * iPTM heatmap for visualizing chain-chain interactions.
 */
import { useState, useMemo } from 'react';
import type { ChainMetric, Design } from '../lib/api';


interface ChainDetailsPanelProps {
    design: Design;
    chainMetrics: Record<string, ChainMetric> | null | undefined;
    isLoading?: boolean;
}

// Map chain index (0,1,2) to chain letter (A,B,C)
const indexToLetter = (idx: string): string => {
    const num = parseInt(idx, 10);
    if (isNaN(num)) return idx;
    return String.fromCharCode(65 + num);
};

// Type icon for chain
const getChainTypeIcon = (type: string): string => {
    switch (type.toLowerCase()) {
        case 'protein': return '🧬';
        case 'dna': return '🔗';
        case 'rna': return '📜';
        case 'ligand': return '💊';
        default: return '•';
    }
};

// Color for pTM quality
const getPtmColor = (ptm: number | null | undefined): string => {
    if (ptm == null) return 'text-slate-400';
    if (ptm >= 0.8) return 'text-emerald-400';
    if (ptm >= 0.6) return 'text-amber-400';
    return 'text-red-400';
};

export function ChainDetailsPanel({ design, chainMetrics, isLoading }: ChainDetailsPanelProps) {
    const [expanded, setExpanded] = useState(true);

    // Build chain list from chains_ptm and/or chainMetrics
    const chains = useMemo(() => {
        const result: Array<{
            id: string;
            letter: string;
            type: string;
            length: number;
            ptm: number | null;
            avgPlddt: number | null;
        }> = [];

        // Use chains_ptm as primary source (from Boltz confidence JSON)
        if (design.chains_ptm) {
            Object.entries(design.chains_ptm).forEach(([idx, ptm]) => {
                const letter = indexToLetter(idx);
                const metric = chainMetrics?.[letter];
                result.push({
                    id: idx,
                    letter,
                    type: metric?.type || 'protein',
                    length: metric?.length || 0,
                    ptm: ptm as number,
                    avgPlddt: metric?.avg_plddt ?? null,
                });
            });
        } else if (chainMetrics) {
            // Fallback to chainMetrics only
            Object.entries(chainMetrics).forEach(([letter, metric]) => {
                result.push({
                    id: letter,
                    letter,
                    type: metric.type,
                    length: metric.length,
                    ptm: null,
                    avgPlddt: metric.avg_plddt,
                });
            });
        }

        return result;
    }, [design.chains_ptm, chainMetrics]);

    // Only show for multi-chain complexes
    if (chains.length <= 1) return null;

    return (
        <div className="bg-slate-800/50 rounded-lg border border-slate-700/50 mt-3">
            {/* Header */}
            <button
                onClick={() => setExpanded(!expanded)}
                className="w-full flex items-center justify-between px-3 py-2 text-sm font-medium text-slate-300 hover:text-white"
            >
                <span className="flex items-center gap-2">
                    <span className="text-xs">{expanded ? '▼' : '▶'}</span>
                    Chain Details ({chains.length} chains)
                </span>
                {isLoading && <span className="text-xs text-slate-500">Loading...</span>}
            </button>

            {/* Body */}
            {expanded && (
                <div className="px-3 pb-3 space-y-2">
                    {/* Chain summary grid */}
                    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
                        {chains.map((chain) => (
                            <div
                                key={chain.id}
                                className="bg-slate-900/50 rounded-md p-2 border border-slate-700/30"
                            >
                                <div className="flex items-center gap-1.5 mb-1">
                                    <span className="text-lg">{getChainTypeIcon(chain.type)}</span>
                                    <span className="font-mono font-semibold text-white">
                                        Chain {chain.letter}
                                    </span>
                                </div>
                                <div className="text-xs text-slate-400 space-y-0.5">
                                    <div className="flex justify-between">
                                        <span>Type:</span>
                                        <span className="capitalize">{chain.type}</span>
                                    </div>
                                    {chain.length > 0 && (
                                        <div className="flex justify-between">
                                            <span>Length:</span>
                                            <span>{chain.length} {chain.type === 'protein' ? 'aa' : 'bp'}</span>
                                        </div>
                                    )}
                                    {chain.ptm != null && (
                                        <div className="flex justify-between">
                                            <span>pTM:</span>
                                            <span className={getPtmColor(chain.ptm)}>
                                                {chain.ptm.toFixed(3)}
                                            </span>
                                        </div>
                                    )}
                                    {chain.avgPlddt != null && (
                                        <div className="flex justify-between">
                                            <span>pLDDT:</span>
                                            <span className={chain.avgPlddt >= 80 ? 'text-emerald-400' : chain.avgPlddt >= 60 ? 'text-amber-400' : 'text-red-400'}>
                                                {chain.avgPlddt.toFixed(1)}
                                            </span>
                                        </div>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>

                    {/* Inter-chain iPTM matrix (if available) */}
                    {design.pair_chains_iptm && Object.keys(design.pair_chains_iptm).length > 1 && (
                        <div className="mt-3">
                            <div className="text-xs text-slate-400 mb-1">Inter-chain iPTM (contact strength)</div>
                            <div className="overflow-x-auto">
                                <table className="text-xs font-mono">
                                    <thead>
                                        <tr>
                                            <th className="px-2 py-1"></th>
                                            {Object.keys(design.pair_chains_iptm).map(idx => (
                                                <th key={idx} className="px-2 py-1 text-slate-400">
                                                    {indexToLetter(idx)}
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {Object.entries(design.pair_chains_iptm).map(([rowIdx, contacts]) => (
                                            <tr key={rowIdx}>
                                                <td className="px-2 py-1 text-slate-400 font-semibold">
                                                    {indexToLetter(rowIdx)}
                                                </td>
                                                {Object.entries(contacts as Record<string, number>).map(([colIdx, iptm]) => {
                                                    const isOnDiagonal = rowIdx === colIdx;
                                                    const bgOpacity = isOnDiagonal ? 0 : Math.min(iptm * 0.8, 0.6);
                                                    return (
                                                        <td
                                                            key={colIdx}
                                                            className="px-2 py-1 text-center"
                                                            style={{
                                                                backgroundColor: isOnDiagonal
                                                                    ? 'transparent'
                                                                    : `rgba(52, 211, 153, ${bgOpacity})`,
                                                            }}
                                                        >
                                                            <span className={isOnDiagonal ? 'text-slate-600' : getPtmColor(iptm)}>
                                                                {iptm.toFixed(2)}
                                                            </span>
                                                        </td>
                                                    );
                                                })}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            <div className="text-[10px] text-slate-500 mt-1">
                                Higher values indicate stronger predicted interaction between chains
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

export default ChainDetailsPanel;
