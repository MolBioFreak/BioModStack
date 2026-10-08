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

// Type indicator for chain (no emojis)
const getChainTypeIndicator = (type: string): { label: string; color: string } => {
    switch (type.toLowerCase()) {
        case 'protein': return { label: 'PRO', color: 'bg-emerald-500/30 text-emerald-300' };
        case 'dna': return { label: 'DNA', color: 'bg-blue-500/30 text-blue-300' };
        case 'rna': return { label: 'RNA', color: 'bg-purple-500/30 text-purple-300' };
        case 'ligand': return { label: 'LIG', color: 'bg-amber-500/30 text-amber-300' };
        default: return { label: '•', color: 'bg-slate-500/30 text-slate-300' };
    }
};

// Color for pTM quality
const getPtmColor = (ptm: number | null | undefined): string => {
    if (ptm == null) return 'text-slate-400';
    if (ptm >= 0.8) return 'text-emerald-400';
    if (ptm >= 0.6) return 'text-amber-400';
    return 'text-red-400';
};

const normalizeChainsPtm = (raw: Design['chains_ptm']): Record<string, number> => {
    if (!raw) return {};
    const normalized: Record<string, number> = {};
    if (Array.isArray(raw)) {
        raw.forEach((value, idx) => {
            const num = Number(value);
            if (Number.isFinite(num)) normalized[String(idx)] = num;
        });
        return normalized;
    }
    Object.entries(raw).forEach(([idx, value]) => {
        const num = Number(value);
        if (Number.isFinite(num)) normalized[idx] = num;
    });
    return normalized;
};

const normalizePairChainsIptm = (raw: Design['pair_chains_iptm']): Record<string, Record<string, number>> => {
    if (!raw) return {};
    const normalized: Record<string, Record<string, number>> = {};

    if (Array.isArray(raw)) {
        raw.forEach((row, rowIdx) => {
            const rowKey = String(rowIdx);
            normalized[rowKey] = {};
            if (Array.isArray(row)) {
                row.forEach((value, colIdx) => {
                    const num = Number(value);
                    if (Number.isFinite(num)) normalized[rowKey][String(colIdx)] = num;
                });
            }
        });
        return normalized;
    }

    Object.entries(raw).forEach(([rowKey, rowValue]) => {
        normalized[rowKey] = {};
        if (rowValue && typeof rowValue === 'object') {
            Object.entries(rowValue as Record<string, unknown>).forEach(([colKey, value]) => {
                const num = Number(value);
                if (Number.isFinite(num)) normalized[rowKey][colKey] = num;
            });
        }
    });
    return normalized;
};

export function ChainDetailsPanel({ design, chainMetrics, isLoading }: ChainDetailsPanelProps) {
    const [expanded, setExpanded] = useState(true);
    const chainsPtm = useMemo(() => normalizeChainsPtm(design.chains_ptm), [design.chains_ptm]);
    const pairChainsIptm = useMemo(() => normalizePairChainsIptm(design.pair_chains_iptm), [design.pair_chains_iptm]);

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
        if (Object.keys(chainsPtm).length > 0) {
            Object.entries(chainsPtm).forEach(([idx, ptm]) => {
                const letter = indexToLetter(idx);
                const metric = chainMetrics?.[letter];
                result.push({
                    id: idx,
                    letter,
                    type: metric?.type || 'protein',
                    length: metric?.length || 0,
                    ptm,
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
    }, [chainsPtm, chainMetrics]);

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
                                <div className="flex items-center gap-2 mb-1">
                                    {(() => {
                                        const indicator = getChainTypeIndicator(chain.type);
                                        return (
                                            <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold leading-none ${indicator.color}`}>
                                                {indicator.label}
                                            </span>
                                        );
                                    })()}
                                    <span className="font-mono font-semibold text-white text-sm">
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
                    {Object.keys(pairChainsIptm).length > 1 && (
                        <div className="mt-4 pt-3 border-t border-slate-700/30">
                            <div className="flex items-center justify-between mb-2">
                                <div className="text-xs font-medium text-slate-300">Inter-chain iPTM Matrix</div>
                                <a
                                    href="https://doi.org/10.1038/s41586-021-03819-2"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-[10px] text-cyan-400 hover:text-cyan-300 hover:underline"
                                >
                                    Jumper et al., Nature 2021
                                </a>
                            </div>
                            <p className="text-[10px] text-slate-500 mb-2">
                                Interface pTM (iPTM) measures predicted structural accuracy at chain-chain interfaces.
                                Values &gt;0.8 indicate high-confidence interactions; 0.5-0.8 suggest moderate confidence.
                            </p>
                            <div className="overflow-x-auto">
                                <table className="text-xs font-mono">
                                    <thead>
                                        <tr>
                                            <th className="px-2 py-1"></th>
                                            {Object.keys(pairChainsIptm).map(idx => (
                                                <th key={idx} className="px-2 py-1 text-slate-400">
                                                    {indexToLetter(idx)}
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {Object.entries(pairChainsIptm).map(([rowIdx, contacts]) => (
                                            <tr key={rowIdx}>
                                                <td className="px-2 py-1 text-slate-400 font-semibold">
                                                    {indexToLetter(rowIdx)}
                                                </td>
                                                {Object.entries(contacts).map(([colIdx, iptm]) => {
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
                            <div className="text-[10px] text-slate-500 mt-2">
                                Diagonal values represent intra-chain confidence; off-diagonal values indicate inter-chain contact prediction quality.
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

export default ChainDetailsPanel;
