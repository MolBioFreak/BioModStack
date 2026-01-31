import { useState, useCallback, useEffect, useRef } from 'react';
import MolstarViewer from './MolstarViewer';
import ChainDetailsPanel from './ChainDetailsPanel';
import type { Design, Job, StructureAnalysis, ChainMetric } from '../lib/api';

interface Selection {
    chain_id?: string;
    start_residue_number?: number;
    end_residue_number?: number;
    color?: { r: number; g: number; b: number };
    focus?: boolean;
}

interface Props {
    selectedDesignId: string | null;
    setSelectedDesignId: (id: string) => void;
    designs: Design[];
    selectedDesign: Design | null | undefined;
    colorMode: 'default' | 'plddt' | 'cdr';
    setColorMode: (mode: 'default' | 'plddt' | 'cdr') => void;
    structureFormat: 'pdb' | 'cif';
    antibodySelections?: Selection[];
    structureAnalysis: StructureAnalysis | null | undefined;
    activeJob: Job | null | undefined;
    getMetricColor: (field: string, value: number | null) => string;
}

type OverlayView = 'metrics' | 'plddt' | 'pae';

export default function StructureViewerPane({
    selectedDesignId,
    setSelectedDesignId,
    designs,
    selectedDesign,
    colorMode,
    setColorMode,
    structureFormat,
    antibodySelections,
    structureAnalysis,
    activeJob,
    getMetricColor,
}: Props) {
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [overlayView, setOverlayView] = useState<OverlayView>('metrics');
    const [plddtProfile, setPlddtProfile] = useState<number[]>([]);
    const [paeMatrix, setPaeMatrix] = useState<number[][] | null>(null);
    const [chainMetrics, setChainMetrics] = useState<Record<string, { length: number; plddt: number[]; avg_plddt: number }>>({});
    const [selectedChain, setSelectedChain] = useState<string | null>(null);  // null = all chains
    const [chainBoundaries, setChainBoundaries] = useState<{ id: string; start: number; end: number }[]>([]);
    const containerRef = useRef<HTMLDivElement>(null);
    const canvasRef = useRef<HTMLCanvasElement>(null);

    // Fetch all structure metrics in parallel when design changes
    // (Consolidated from 3 separate useEffects to reduce network round-trips)
    useEffect(() => {
        if (!selectedDesignId) return;

        const fetchAllMetrics = async () => {
            try {
                // Parallel fetch all three endpoints
                const [residueRes, paeRes, chainRes] = await Promise.all([
                    fetch(`/api/designs/${selectedDesignId}/residue-metrics`).catch(() => null),
                    fetch(`/api/designs/${selectedDesignId}/pae`).catch(() => null),
                    fetch(`/api/designs/${selectedDesignId}/chain-metrics`).catch(() => null),
                ]);

                // Process residue pLDDT
                if (residueRes?.ok) {
                    const data = await residueRes.json();
                    setPlddtProfile(data.plddt || []);
                } else {
                    setPlddtProfile([]);
                }

                // Process PAE matrix
                if (paeRes?.ok) {
                    const data = await paeRes.json();
                    setPaeMatrix(data.pae_matrix || null);
                } else {
                    setPaeMatrix(null);
                }

                // Process chain metrics
                if (chainRes?.ok) {
                    const data = await chainRes.json();
                    setChainMetrics(data);

                    // Compute chain boundaries for PAE overlay
                    const chainIds = Object.keys(data).sort();
                    let offset = 0;
                    const boundaries: { id: string; start: number; end: number }[] = [];
                    for (const chainId of chainIds) {
                        const length = data[chainId]?.length || 0;
                        boundaries.push({ id: chainId, start: offset, end: offset + length });
                        offset += length;
                    }
                    setChainBoundaries(boundaries);
                } else {
                    setChainMetrics({});
                    setChainBoundaries([]);
                }
            } catch (err) {
                console.error('Failed to fetch structure metrics:', err);
            }
        };

        fetchAllMetrics();
    }, [selectedDesignId]);

    // Draw PAE heatmap on canvas
    useEffect(() => {
        if (overlayView !== 'pae' || !paeMatrix || !canvasRef.current) return;

        const canvas = canvasRef.current;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        const size = Math.min(paeMatrix.length, 250);
        canvas.width = size;
        canvas.height = size;

        const scale = paeMatrix.length / size;

        for (let y = 0; y < size; y++) {
            for (let x = 0; x < size; x++) {
                const srcY = Math.floor(y * scale);
                const srcX = Math.floor(x * scale);
                const value = paeMatrix[srcY]?.[srcX] ?? 0;

                // Color: green (low) -> white (mid) -> red (high)
                const maxVal = 30;
                const norm = Math.min(value / maxVal, 1);
                const r = norm < 0.5 ? Math.round(norm * 2 * 255) : 255;
                const g = norm < 0.5 ? 255 : Math.round((1 - (norm - 0.5) * 2) * 255);
                const b = Math.round((1 - norm) * 100);

                ctx.fillStyle = `rgb(${r},${g},${b})`;
                ctx.fillRect(x, y, 1, 1);
            }
        }
    }, [overlayView, paeMatrix]);

    // Toggle fullscreen using native browser API
    const toggleFullscreen = useCallback(() => {
        if (!containerRef.current) return;

        if (!document.fullscreenElement) {
            containerRef.current.requestFullscreen().catch(err => {
                console.error('Failed to enter fullscreen:', err);
            });
        } else {
            document.exitFullscreen();
        }
    }, []);

    // Listen to fullscreen changes
    useEffect(() => {
        const handleFullscreenChange = () => {
            setIsFullscreen(!!document.fullscreenElement);
        };
        document.addEventListener('fullscreenchange', handleFullscreenChange);
        return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
    }, []);

    // Toggleable Analytics Panel for fullscreen
    const FullscreenOverlay = () => (
        <div className="w-80 bg-slate-900/80 backdrop-blur-sm rounded-lg border border-slate-700/50 overflow-hidden">
            {/* Tab Header */}
            <div className="flex border-b border-slate-700/50">
                {[
                    { id: 'metrics', label: 'Metrics' },
                    { id: 'plddt', label: 'pLDDT' },
                    { id: 'pae', label: 'PAE' },
                ].map(tab => (
                    <button
                        key={tab.id}
                        onClick={() => setOverlayView(tab.id as OverlayView)}
                        className={`flex-1 px-3 py-2 text-xs font-medium transition-colors ${overlayView === tab.id
                            ? 'bg-blue-500/20 text-blue-400 border-b-2 border-blue-400'
                            : 'text-slate-400 hover:text-white hover:bg-slate-800/50'
                            }`}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* Content */}
            <div className="p-3">
                {overlayView === 'metrics' && (
                    <div className="space-y-3">
                        {/* Design Title */}
                        {selectedDesign && (
                            <div>
                                <h3 className="font-medium text-white/90 truncate text-sm">{selectedDesign.name}</h3>
                                <div className="text-xs text-slate-400/80">
                                    {activeJob?.model_id} • {new Date(selectedDesign.created_at).toLocaleDateString()}
                                </div>
                            </div>
                        )}

                        {/* Key Metrics Grid */}
                        {selectedDesign && (
                            <div className="grid grid-cols-2 gap-2">
                                <div className="bg-slate-800/40 rounded p-2 text-center">
                                    <div className={`text-lg font-bold ${getMetricColor('plddt_overall', selectedDesign.plddt_overall ?? null)}`}>
                                        {selectedDesign.plddt_overall?.toFixed(1) ?? '—'}
                                    </div>
                                    <div className="text-[10px] text-slate-500">pLDDT</div>
                                </div>
                                <div className="bg-slate-800/40 rounded p-2 text-center">
                                    <div className={`text-lg font-bold ${getMetricColor('pae_overall', selectedDesign.pae_overall ?? null)}`}>
                                        {selectedDesign.pae_overall?.toFixed(2) ?? '—'}
                                    </div>
                                    <div className="text-[10px] text-slate-500">PAE</div>
                                </div>
                                <div className="bg-slate-800/40 rounded p-2 text-center">
                                    <div className="text-lg font-bold text-violet-400">
                                        {selectedDesign.ptm?.toFixed(3) ?? '—'}
                                    </div>
                                    <div className="text-[10px] text-slate-500">pTM</div>
                                </div>
                                <div className="bg-slate-800/40 rounded p-2 text-center">
                                    <div className="text-lg font-bold text-amber-400">
                                        {selectedDesign.iptm?.toFixed(3) ?? '—'}
                                    </div>
                                    <div className="text-[10px] text-slate-500">iPTM</div>
                                </div>
                            </div>
                        )}

                        {/* Structure Analysis */}
                        {structureAnalysis && (
                            <div className="text-xs text-slate-400 space-y-1">
                                <div className="flex justify-between">
                                    <span>Residues</span>
                                    <span className="text-white font-mono">{structureAnalysis.residue_count}</span>
                                </div>
                                <div className="flex justify-between">
                                    <span>Chains</span>
                                    <span className="text-white font-mono">{structureAnalysis.chain_ids?.length ?? 0}</span>
                                </div>
                                {structureAnalysis.secondary_structure && (
                                    <div className="flex justify-between">
                                        <span>Secondary</span>
                                        <span>
                                            <span className="text-pink-400">α{structureAnalysis.secondary_structure.helix?.toFixed(0)}%</span>
                                            {' '}
                                            <span className="text-yellow-400">β{structureAnalysis.secondary_structure.sheet?.toFixed(0)}%</span>
                                        </span>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                )}

                {overlayView === 'plddt' && (
                    <div>
                        {/* Chain Filter Toggle */}
                        <div className="flex items-center gap-1 mb-2 flex-wrap">
                            <span className="text-xs text-slate-400 mr-1">Chain:</span>
                            <button
                                onClick={() => setSelectedChain(null)}
                                className={`px-2 py-0.5 text-[10px] rounded transition-colors ${selectedChain === null
                                    ? 'bg-blue-500/30 text-blue-400 border border-blue-500/40'
                                    : 'bg-slate-700/50 text-slate-400 hover:bg-slate-600/50'
                                    }`}
                            >
                                All
                            </button>
                            {Object.keys(chainMetrics).sort().map((chainId, idx) => (
                                <button
                                    key={chainId}
                                    onClick={() => setSelectedChain(chainId)}
                                    className={`px-2 py-0.5 text-[10px] rounded transition-colors ${selectedChain === chainId
                                        ? 'bg-blue-500/30 text-blue-400 border border-blue-500/40'
                                        : 'bg-slate-700/50 text-slate-400 hover:bg-slate-600/50'
                                        }`}
                                    style={{ borderLeft: `2px solid ${['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'][idx % 5]}` }}
                                >
                                    {chainId} ({chainMetrics[chainId]?.length || 0})
                                </button>
                            ))}
                        </div>

                        {/* Chart */}
                        {plddtProfile.length > 0 ? (
                            <div className="h-36 relative bg-slate-800/40 rounded overflow-hidden">
                                <svg viewBox={`0 0 ${plddtProfile.length} 100`} className="w-full h-full" preserveAspectRatio="none">
                                    {/* Grid lines */}
                                    <line x1="0" y1="10" x2={plddtProfile.length} y2="10" stroke="#334155" strokeWidth="0.5" />
                                    <line x1="0" y1="30" x2={plddtProfile.length} y2="30" stroke="#334155" strokeWidth="0.5" />
                                    <line x1="0" y1="50" x2={plddtProfile.length} y2="50" stroke="#334155" strokeWidth="0.5" />

                                    {/* Chain boundary lines */}
                                    {chainBoundaries.map((chain, idx) => (
                                        chain.start > 0 && (
                                            <line
                                                key={chain.id}
                                                x1={chain.start}
                                                y1="0"
                                                x2={chain.start}
                                                y2="100"
                                                stroke={['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'][idx % 5]}
                                                strokeWidth="1"
                                                strokeDasharray="2,2"
                                            />
                                        )
                                    ))}

                                    {/* Area fill - highlight selected chain or show all */}
                                    {selectedChain === null ? (
                                        <path
                                            d={`M0,100 ${plddtProfile.map((v, i) => `L${i},${100 - v}`).join(' ')} L${plddtProfile.length - 1},100 Z`}
                                            fill="url(#plddtGradient)"
                                            opacity="0.3"
                                        />
                                    ) : (
                                        chainBoundaries.filter(c => c.id === selectedChain).map(chain => (
                                            <path
                                                key={chain.id}
                                                d={`M${chain.start},100 ${plddtProfile.slice(chain.start, chain.end).map((v, i) => `L${chain.start + i},${100 - v}`).join(' ')} L${chain.end - 1},100 Z`}
                                                fill="url(#plddtGradient)"
                                                opacity="0.5"
                                            />
                                        ))
                                    )}

                                    {/* Line - dim non-selected chains */}
                                    <polyline
                                        points={plddtProfile.map((v, i) => `${i},${100 - v}`).join(' ')}
                                        fill="none"
                                        stroke="#3b82f6"
                                        strokeWidth="1"
                                        opacity={selectedChain === null ? 1 : 0.2}
                                    />

                                    {/* Highlighted chain line */}
                                    {selectedChain && chainBoundaries.filter(c => c.id === selectedChain).map((chain) => (
                                        <polyline
                                            key={chain.id}
                                            points={plddtProfile.slice(chain.start, chain.end).map((v, i) => `${chain.start + i},${100 - v}`).join(' ')}
                                            fill="none"
                                            stroke={['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'][Object.keys(chainMetrics).sort().indexOf(selectedChain) % 5]}
                                            strokeWidth="1.5"
                                        />
                                    ))}

                                    <defs>
                                        <linearGradient id="plddtGradient" x1="0%" y1="0%" x2="0%" y2="100%">
                                            <stop offset="0%" stopColor="#3b82f6" />
                                            <stop offset="100%" stopColor="#1e3a5f" />
                                        </linearGradient>
                                    </defs>
                                </svg>

                                {/* Y-axis labels */}
                                <div className="absolute left-1 top-0 text-[8px] text-slate-500">90</div>
                                <div className="absolute left-1 top-1/2 text-[8px] text-slate-500">50</div>
                                <div className="absolute left-1 bottom-0 text-[8px] text-slate-500">0</div>
                            </div>
                        ) : (
                            <div className="h-36 flex items-center justify-center text-slate-500 text-xs bg-slate-800/40 rounded">
                                No pLDDT profile data available
                            </div>
                        )}
                        <div className="text-[10px] text-slate-500 mt-1 text-center">
                            {selectedChain
                                ? `Chain ${selectedChain}: ${chainMetrics[selectedChain]?.length || 0} residues • Mean: ${chainMetrics[selectedChain]?.avg_plddt?.toFixed(1) || '—'}`
                                : `${plddtProfile.length} residues • Mean: ${plddtProfile.length > 0 ? (plddtProfile.reduce((a, b) => a + b, 0) / plddtProfile.length).toFixed(1) : '—'}`
                            }
                        </div>
                    </div>
                )}

                {overlayView === 'pae' && (
                    <div>
                        <div className="text-xs text-slate-400 mb-2">Predicted Aligned Error Matrix</div>
                        {paeMatrix ? (
                            <div className="flex flex-col items-center">
                                {/* Canvas container with chain labels */}
                                <div className="relative">
                                    <canvas
                                        ref={canvasRef}
                                        className="rounded border border-slate-700"
                                        style={{ width: '220px', height: '220px', imageRendering: 'pixelated' }}
                                    />

                                    {/* Chain boundary labels on X-axis (bottom) */}
                                    <div className="absolute -bottom-4 left-0 right-0 flex" style={{ height: '16px' }}>
                                        {chainBoundaries.map((chain, idx) => {
                                            const totalResidues = paeMatrix.length;
                                            const leftPct = (chain.start / totalResidues) * 100;
                                            const widthPct = ((chain.end - chain.start) / totalResidues) * 100;
                                            return (
                                                <div
                                                    key={chain.id}
                                                    className="absolute text-[8px] text-slate-400 font-mono flex items-center justify-center"
                                                    style={{
                                                        left: `${leftPct}%`,
                                                        width: `${widthPct}%`,
                                                        borderTop: `2px solid ${['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'][idx % 5]}`,
                                                    }}
                                                    title={`Chain ${chain.id}: residues ${chain.start + 1}-${chain.end}`}
                                                >
                                                    {chain.id}
                                                </div>
                                            );
                                        })}
                                    </div>

                                    {/* Chain boundary labels on Y-axis (left) */}
                                    <div className="absolute -left-3 top-0 bottom-0 flex flex-col" style={{ width: '12px' }}>
                                        {chainBoundaries.map((chain, idx) => {
                                            const totalResidues = paeMatrix.length;
                                            const topPct = (chain.start / totalResidues) * 100;
                                            const heightPct = ((chain.end - chain.start) / totalResidues) * 100;
                                            return (
                                                <div
                                                    key={chain.id}
                                                    className="absolute text-[8px] text-slate-400 font-mono flex items-center justify-center"
                                                    style={{
                                                        top: `${topPct}%`,
                                                        height: `${heightPct}%`,
                                                        borderRight: `2px solid ${['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'][idx % 5]}`,
                                                        writingMode: 'vertical-lr',
                                                        transform: 'rotate(180deg)',
                                                    }}
                                                    title={`Chain ${chain.id}: residues ${chain.start + 1}-${chain.end}`}
                                                >
                                                    {chain.id}
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>

                                <div className="flex items-center gap-2 mt-5 text-[10px] text-slate-400">
                                    <span>Low</span>
                                    <div className="w-20 h-2 rounded" style={{ background: 'linear-gradient(to right, #00ff00, #ffffff, #ff0000)' }} />
                                    <span>High</span>
                                </div>

                                {/* Chain legend */}
                                <div className="flex items-center gap-2 mt-1 flex-wrap justify-center">
                                    {chainBoundaries.map((chain, idx) => (
                                        <span key={chain.id} className="text-[9px] text-slate-400">
                                            <span
                                                className="inline-block w-2 h-2 rounded-sm mr-0.5"
                                                style={{ backgroundColor: ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'][idx % 5] }}
                                            />
                                            {chain.id}:{chain.end - chain.start}
                                        </span>
                                    ))}
                                </div>
                            </div>
                        ) : (
                            <div className="h-40 flex items-center justify-center text-slate-500 text-xs bg-slate-800/40 rounded">
                                No PAE matrix available
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );

    // Full sidebar for normal mode
    const AnalyticsSidebar = () => (
        <div className="flex-1 min-w-[280px] max-w-[400px] space-y-4">
            {/* Design Title */}
            {selectedDesign && (
                <div className="bg-slate-800/50 rounded-lg border border-slate-700/50 p-4">
                    <h3 className="font-medium text-white truncate mb-2">{selectedDesign.name}</h3>
                    <div className="text-xs text-slate-400">
                        {activeJob?.model_id} • {new Date(selectedDesign.created_at).toLocaleDateString()}
                    </div>
                </div>
            )}

            {/* Key Metrics */}
            {selectedDesign && (
                <div className="bg-slate-800/50 rounded-lg border border-slate-700/50 p-4">
                    <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Confidence Metrics</h4>
                    <div className="grid grid-cols-2 gap-3">
                        <div className="bg-slate-900/50 rounded-lg p-3 text-center">
                            <div className={`text-2xl font-bold ${getMetricColor('plddt_overall', selectedDesign.plddt_overall ?? null)}`}>
                                {selectedDesign.plddt_overall?.toFixed(1) ?? '—'}
                            </div>
                            <div className="text-xs text-slate-500 mt-1">pLDDT</div>
                        </div>
                        <div className="bg-slate-900/50 rounded-lg p-3 text-center">
                            <div className={`text-2xl font-bold ${getMetricColor('pae_overall', selectedDesign.pae_overall ?? null)}`}>
                                {selectedDesign.pae_overall?.toFixed(2) ?? '—'}
                            </div>
                            <div className="text-xs text-slate-500 mt-1">PAE</div>
                        </div>
                        <div className="bg-slate-900/50 rounded-lg p-3 text-center">
                            <div className="text-2xl font-bold text-violet-400">
                                {selectedDesign.ptm?.toFixed(3) ?? '—'}
                            </div>
                            <div className="text-xs text-slate-500 mt-1">pTM</div>
                        </div>
                        <div className="bg-slate-900/50 rounded-lg p-3 text-center">
                            <div className="text-2xl font-bold text-amber-400">
                                {selectedDesign.iptm?.toFixed(3) ?? '—'}
                            </div>
                            <div className="text-xs text-slate-500 mt-1">iPTM</div>
                        </div>
                    </div>
                </div>
            )}

            {/* Structure Analysis */}
            {structureAnalysis && (
                <div className="bg-slate-800/50 rounded-lg border border-slate-700/50 p-4">
                    <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Structure Analysis</h4>
                    <div className="space-y-2 text-sm">
                        <div className="flex justify-between">
                            <span className="text-slate-500">Total Residues</span>
                            <span className="text-white font-mono">{structureAnalysis.residue_count}</span>
                        </div>
                        <div className="flex justify-between">
                            <span className="text-slate-500">Chains</span>
                            <span className="text-white font-mono">{structureAnalysis.chain_ids?.length ?? 0}</span>
                        </div>
                        {structureAnalysis.secondary_structure && (
                            <>
                                <div className="flex justify-between">
                                    <span className="text-slate-500">α-Helix</span>
                                    <span className="text-pink-400 font-mono">{structureAnalysis.secondary_structure.helix?.toFixed(0)}%</span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-slate-500">β-Sheet</span>
                                    <span className="text-yellow-400 font-mono">{structureAnalysis.secondary_structure.sheet?.toFixed(0)}%</span>
                                </div>
                            </>
                        )}
                    </div>
                </div>
            )}

            {/* Chain Details Panel (for multi-chain complexes) */}
            {selectedDesign && (
                <ChainDetailsPanel
                    design={selectedDesign}
                    chainMetrics={chainMetrics as Record<string, ChainMetric> | null}
                />
            )}

            {/* Frustration Analysis (FrustraMPNN) */}
            {selectedDesign?.frustration_high_count != null && (
                <div className="bg-slate-800/50 rounded-lg border border-slate-700/50 p-4">
                    <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Frustration Analysis</h4>
                    <div className="space-y-2 text-sm">
                        <div className="flex justify-between">
                            <span className="text-slate-500">Highly Frustrated</span>
                            <span className={`font-mono ${(selectedDesign.frustration_high_count ?? 0) > 5 ? 'text-red-400' : 'text-green-400'}`}>
                                {selectedDesign.frustration_high_count} residues
                            </span>
                        </div>
                        <div className="flex justify-between">
                            <span className="text-slate-500">Minimally Frustrated</span>
                            <span className="text-green-400 font-mono">
                                {selectedDesign.frustration_min_count} residues
                            </span>
                        </div>
                        <div className="flex justify-between">
                            <span className="text-slate-500">% Highly Frustrated</span>
                            <span className={`font-mono ${(selectedDesign.frustration_pct_high ?? 0) > 10 ? 'text-orange-400' : 'text-green-400'}`}>
                                {selectedDesign.frustration_pct_high?.toFixed(1)}%
                            </span>
                        </div>
                    </div>
                    {/* Quick legend */}
                    <div className="flex items-center justify-center gap-3 mt-3 pt-2 border-t border-slate-700/50">
                        <span className="text-[10px] text-slate-500">
                            <span className="text-green-400 mr-1">●</span>min (≥0.58)
                        </span>
                        <span className="text-[10px] text-slate-500">
                            <span className="text-slate-400 mr-1">●</span>neutral
                        </span>
                        <span className="text-[10px] text-slate-500">
                            <span className="text-red-400 mr-1">●</span>high (≤-1.0)
                        </span>
                    </div>
                </div>
            )}

            {/* CDR Info */}
            {(selectedDesign as any)?.cdr_h3 && (
                <div className="bg-slate-800/50 rounded-lg border border-slate-700/50 p-4">
                    <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">CDR Loops</h4>
                    <div className="space-y-2 font-mono text-xs">
                        {['H1', 'H2', 'H3', 'L1', 'L2', 'L3'].map(cdr => {
                            const seq = (selectedDesign as any)?.[`cdr_${cdr.toLowerCase()}`];
                            if (!seq) return null;
                            return (
                                <div key={cdr} className="flex justify-between gap-2">
                                    <span className="text-slate-500 font-bold">{cdr}</span>
                                    <span className="text-white truncate flex-1 text-right">{seq}</span>
                                    <span className="text-slate-600 w-6 text-right">{seq.length}</span>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* Favorite Button */}
            {selectedDesign && (
                <button
                    onClick={async () => {
                        await fetch(`/api/designs/${selectedDesign.id}/favorite`, { method: 'POST' });
                    }}
                    className={`w-full py-2 rounded-lg text-sm font-medium transition-colors ${selectedDesign.is_favorite
                        ? 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/40'
                        : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                        }`}
                >
                    {selectedDesign.is_favorite ? '★ Favorited' : '☆ Add to Favorites'}
                </button>
            )}
        </div>
    );

    return (
        <div
            ref={containerRef}
            className={`${isFullscreen ? 'fixed inset-0 z-50 bg-slate-950' : 'p-4'}`}
        >
            {isFullscreen ? (
                /* FULLSCREEN LAYOUT */
                <>
                    {/* Main Viewer (bottom layer) */}
                    <div className="absolute inset-0">
                        <MolstarViewer
                            key={selectedDesignId + '_' + colorMode}
                            structureUrl={selectedDesignId ? `/api/designs/${selectedDesignId}/pdb` : undefined}
                            format={structureFormat}
                            alphafoldView={colorMode === 'plddt'}
                            selections={colorMode === 'cdr' ? antibodySelections : undefined}
                            height="100%"
                            backgroundColor="#0f172a"
                        />
                    </div>

                    {/* Toolbar (top-left, z-40) */}
                    <div className="absolute top-3 left-3 z-40 flex items-center gap-2 flex-wrap">
                        {/* Design Selector */}
                        <div className="relative">
                            <select
                                value={selectedDesignId ?? ''}
                                onChange={(e) => setSelectedDesignId(e.target.value)}
                                className="appearance-none bg-slate-800/90 backdrop-blur-sm border border-slate-700 rounded-lg px-3 py-1.5 pr-8 text-sm text-white cursor-pointer hover:bg-slate-700 transition-colors min-w-[200px]"
                            >
                                {[...designs].sort((a, b) => (b.plddt_overall ?? 0) - (a.plddt_overall ?? 0)).map(d => (
                                    <option key={d.id} value={d.id}>
                                        {d.name} {d.plddt_overall ? `(${d.plddt_overall.toFixed(0)})` : ''}
                                    </option>
                                ))}
                            </select>
                            <div className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-slate-400">▾</div>
                        </div>

                        {/* Color Mode */}
                        <select
                            value={colorMode}
                            onChange={(e) => setColorMode(e.target.value as 'default' | 'plddt' | 'cdr')}
                            className="appearance-none bg-slate-800/90 backdrop-blur-sm border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-white cursor-pointer hover:bg-slate-700"
                        >
                            <option value="default">Chain Colors</option>
                            <option value="plddt">pLDDT</option>
                            <option value="cdr" disabled={!((designs.find(d => d.id === selectedDesignId) as any)?.cdr_h1_length)}>
                                CDR Regions
                            </option>
                        </select>

                        {/* Exit Fullscreen */}
                        <button
                            onClick={toggleFullscreen}
                            className="px-3 py-1.5 text-xs rounded-lg bg-red-500/80 hover:bg-red-500 text-white backdrop-blur-sm transition-colors"
                        >
                            ✕ Exit Fullscreen
                        </button>
                    </div>

                    {/* Toggleable Analytics Panel (bottom-right, z-40) */}
                    <div className="absolute bottom-4 right-4 z-40">
                        <FullscreenOverlay />
                    </div>
                </>
            ) : (
                /* NORMAL LAYOUT - YouTube-style grid */
                <div className="flex gap-4">
                    {/* Left Column: Viewer */}
                    <div className="flex-[2] min-w-0">
                        {/* Toolbar */}
                        <div className="flex items-center gap-2 mb-3 flex-wrap">
                            {/* Design Selector */}
                            <div className="relative">
                                <select
                                    value={selectedDesignId ?? ''}
                                    onChange={(e) => setSelectedDesignId(e.target.value)}
                                    className="appearance-none bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 pr-8 text-sm text-white cursor-pointer hover:bg-slate-700 transition-colors min-w-[200px]"
                                >
                                    {[...designs].sort((a, b) => (b.plddt_overall ?? 0) - (a.plddt_overall ?? 0)).map(d => (
                                        <option key={d.id} value={d.id}>
                                            {d.name} {d.plddt_overall ? `(${d.plddt_overall.toFixed(0)})` : ''}
                                        </option>
                                    ))}
                                </select>
                                <div className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-slate-400">▾</div>
                            </div>

                            {/* Color Mode */}
                            <select
                                value={colorMode}
                                onChange={(e) => setColorMode(e.target.value as 'default' | 'plddt' | 'cdr')}
                                className="appearance-none bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-white cursor-pointer hover:bg-slate-700"
                            >
                                <option value="default">Chain Colors</option>
                                <option value="plddt">pLDDT</option>
                                <option value="cdr" disabled={!((designs.find(d => d.id === selectedDesignId) as any)?.cdr_h1_length)}>
                                    CDR Regions
                                </option>
                            </select>

                            {/* Color Legend */}
                            {colorMode === 'plddt' && (
                                <div className="flex items-center gap-1 text-xs text-slate-400">
                                    <span className="text-blue-400">■</span>≥90
                                    <span className="text-cyan-400 ml-1">■</span>≥70
                                    <span className="text-yellow-400 ml-1">■</span>≥50
                                    <span className="text-orange-400 ml-1">■</span>&lt;50
                                </div>
                            )}

                            {/* Fullscreen Toggle */}
                            <button
                                onClick={toggleFullscreen}
                                className="px-3 py-1.5 text-xs rounded-lg bg-slate-700 text-slate-400 hover:bg-slate-600 transition-colors"
                            >
                                ⛶ Fullscreen
                            </button>
                        </div>

                        {/* Main Viewer */}
                        <div className="relative rounded-lg overflow-hidden border border-slate-700">
                            <MolstarViewer
                                key={selectedDesignId + '_' + colorMode}
                                structureUrl={selectedDesignId ? `/api/designs/${selectedDesignId}/pdb` : undefined}
                                format={structureFormat}
                                alphafoldView={colorMode === 'plddt'}
                                selections={colorMode === 'cdr' ? antibodySelections : undefined}
                                height={450}
                                backgroundColor="#0f172a"
                            />
                        </div>
                    </div>

                    {/* Right Column: Analytics Sidebar */}
                    <AnalyticsSidebar />
                </div>
            )}
        </div>
    );
}
