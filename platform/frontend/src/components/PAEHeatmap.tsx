/**
 * PAE Heatmap Component
 * 
 * Renders a Predicted Aligned Error (PAE) heatmap using canvas.
 * Green = low error (good), White = high error (poor)
 * Supports chain boundary visualization for complexes.
 */

import { useEffect, useRef, useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

interface PAEData {
    design_id: string;
    design_name: string;
    pae_matrix: number[][];
    size: number;
    chain_boundaries?: { chain_id: string; start: number; end: number }[];
}

interface ChainMetric {
    type: string;
    length: number;
    avg_plddt?: number | null;
    plddt?: number[] | null;
    residue_numbers?: number[] | null;
}

interface PAEHeatmapProps {
    designId: string;
    width?: number;
    height?: number;
    chainMetrics?: Record<string, ChainMetric>;
}

const fetchPAEData = (designId: string) =>
    api.get<PAEData>(`/api/designs/${designId}/pae`);

// Green gradient for PAE (low = dark green, high = white)
const getPAEColor = (value: number, maxValue: number = 30): string => {
    // Normalize to 0-1 range
    const norm = Math.min(value / maxValue, 1);

    // Dark green (0, 100, 50) to white (255, 255, 255)
    const r = Math.floor(norm * 255);
    const g = Math.floor(100 + norm * 155);
    const b = Math.floor(50 + norm * 205);

    return `rgb(${r}, ${g}, ${b})`;
};

export function PAEHeatmap({ designId, width = 400, height = 400, chainMetrics }: PAEHeatmapProps) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const [hoveredCell, setHoveredCell] = useState<{ i: number; j: number; value: number; chainI?: string; chainJ?: string } | null>(null);

    const { data: paeData, isLoading, error } = useQuery({
        queryKey: ['pae', designId],
        queryFn: () => fetchPAEData(designId),
        enabled: !!designId,
    });

    const pae = paeData?.data;

    // Compute chain boundaries from chainMetrics
    const chainBoundaries = useMemo(() => {
        if (!chainMetrics) return [];

        const chains = Object.entries(chainMetrics)
            .filter(([, m]) => m.type !== 'ligand')
            .sort(([idA], [idB]) => idA.localeCompare(idB));

        let cumulative = 0;
        return chains.map(([chainId, metric]) => {
            const start = cumulative;
            cumulative += metric.length;
            return { chain_id: chainId, start, end: cumulative, length: metric.length };
        });
    }, [chainMetrics]);

    // Total residues from chain metrics
    const totalResidues = useMemo(() => {
        if (!chainMetrics) return 0;
        return Object.values(chainMetrics)
            .filter(m => m.type !== 'ligand')
            .reduce((sum, m) => sum + m.length, 0);
    }, [chainMetrics]);

    // Map position to chain
    const getChainForPosition = (pos: number): string | undefined => {
        for (const b of chainBoundaries) {
            if (pos >= b.start && pos < b.end) return b.chain_id;
        }
        return undefined;
    };

    // Render heatmap when data is available
    useEffect(() => {
        if (!pae?.pae_matrix || !canvasRef.current) return;

        const canvas = canvasRef.current;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        const matrix = pae.pae_matrix;
        const size = matrix.length;
        const cellWidth = width / size;
        const cellHeight = height / size;

        // Clear canvas
        ctx.fillStyle = '#1e293b';
        ctx.fillRect(0, 0, width, height);

        // Draw heatmap cells
        for (let i = 0; i < size; i++) {
            for (let j = 0; j < size; j++) {
                const value = matrix[i][j];
                ctx.fillStyle = getPAEColor(value);
                ctx.fillRect(j * cellWidth, i * cellHeight, cellWidth + 0.5, cellHeight + 0.5);
            }
        }

        // Draw chain boundaries if we have them
        if (chainBoundaries.length > 1 && totalResidues > 0) {
            const scale = size / totalResidues;

            ctx.strokeStyle = 'rgba(255, 255, 255, 0.8)';
            ctx.lineWidth = 2;

            // Draw divider lines at chain boundaries
            for (const boundary of chainBoundaries) {
                if (boundary.start > 0) {
                    const pos = boundary.start * scale;

                    // Horizontal line
                    ctx.beginPath();
                    ctx.moveTo(0, pos * cellHeight);
                    ctx.lineTo(width, pos * cellHeight);
                    ctx.stroke();

                    // Vertical line
                    ctx.beginPath();
                    ctx.moveTo(pos * cellWidth, 0);
                    ctx.lineTo(pos * cellWidth, height);
                    ctx.stroke();
                }
            }

            // Draw chain labels
            ctx.font = 'bold 12px sans-serif';
            ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';

            for (const boundary of chainBoundaries) {
                const midPos = ((boundary.start + boundary.end) / 2) * scale;
                const x = midPos * cellWidth;
                const y = midPos * cellHeight;

                // Draw label with background
                const label = boundary.chain_id;
                const metrics = ctx.measureText(label);
                const padding = 4;

                ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
                ctx.fillRect(
                    x - metrics.width / 2 - padding,
                    y - 8 - padding,
                    metrics.width + padding * 2,
                    16 + padding * 2
                );

                ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
                ctx.fillText(label, x, y);
            }
        }

        // Draw diagonal line for reference
        ctx.strokeStyle = 'rgba(100, 100, 100, 0.4)';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(width, height);
        ctx.stroke();
        ctx.setLineDash([]);

    }, [pae, width, height, chainBoundaries, totalResidues]);

    // Handle mouse hover for tooltip
    const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
        if (!pae?.pae_matrix || !canvasRef.current) return;

        const canvas = canvasRef.current;
        const rect = canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        const size = pae.pae_matrix.length;
        const j = Math.floor(x / (width / size));
        const i = Math.floor(y / (height / size));

        if (i >= 0 && i < size && j >= 0 && j < size) {
            // Map back to original residue positions for chain lookup
            const scale = totalResidues > 0 ? totalResidues / size : 1;
            const origI = Math.floor(i * scale);
            const origJ = Math.floor(j * scale);

            setHoveredCell({
                i,
                j,
                value: pae.pae_matrix[i][j],
                chainI: getChainForPosition(origI),
                chainJ: getChainForPosition(origJ)
            });
        }
    };

    if (isLoading) {
        return (
            <div className="flex items-center justify-center bg-slate-800/50 rounded-lg" style={{ width, height }}>
                <div className="flex flex-col items-center gap-2">
                    <div className="w-8 h-8 border-2 border-green-500 border-t-transparent rounded-full animate-spin" />
                    <span className="text-xs text-slate-400">Loading PAE data...</span>
                </div>
            </div>
        );
    }

    if (error || !pae) {
        return (
            <div className="flex flex-col items-center justify-center bg-slate-800/50 rounded-lg text-slate-500" style={{ width, height }}>
                <svg className="w-12 h-12 mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5z M4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6z" />
                </svg>
                <span className="text-sm">No PAE data available</span>
                <span className="text-xs mt-1 opacity-70">Select a design with confidence data</span>
            </div>
        );
    }

    return (
        <div className="relative">
            {/* Header with info */}
            <div className="flex items-center justify-between mb-3">
                <div className="text-sm text-slate-300 font-medium">
                    Predicted Aligned Error (PAE)
                </div>
                <div className="flex items-center gap-3 text-xs">
                    {chainBoundaries.length > 1 && (
                        <span className="text-slate-400">
                            {chainBoundaries.length} chains • {totalResidues} residues
                        </span>
                    )}
                    <span className="text-slate-500">{pae.size}×{pae.size} matrix</span>
                </div>
            </div>

            {/* Chain legend for complexes */}
            {chainBoundaries.length > 1 && (
                <div className="flex flex-wrap gap-2 mb-3">
                    {chainBoundaries.map((b, idx) => (
                        <div
                            key={b.chain_id}
                            className="flex items-center gap-1.5 px-2 py-1 bg-slate-700/50 rounded text-xs"
                        >
                            <span
                                className="w-3 h-3 rounded-sm"
                                style={{
                                    backgroundColor: `hsl(${(idx * 60) % 360}, 70%, 50%)`
                                }}
                            />
                            <span className="text-slate-300 font-medium">Chain {b.chain_id}</span>
                            <span className="text-slate-500">{b.length} res</span>
                        </div>
                    ))}
                </div>
            )}

            <div className="relative inline-block">
                <canvas
                    ref={canvasRef}
                    width={width}
                    height={height}
                    className="rounded-lg border border-slate-600 shadow-lg"
                    onMouseMove={handleMouseMove}
                    onMouseLeave={() => setHoveredCell(null)}
                />

                {/* Axis labels */}
                <div className="absolute -bottom-6 left-1/2 -translate-x-1/2 text-xs text-slate-400">
                    Scored residue
                </div>
                <div className="absolute top-1/2 -left-7 -translate-y-1/2 -rotate-90 text-xs text-slate-400">
                    Aligned residue
                </div>

                {/* Tooltip */}
                {hoveredCell && (
                    <div
                        className="absolute z-10 bg-slate-900/95 border border-slate-500 rounded-lg px-3 py-2 text-xs pointer-events-none shadow-xl"
                        style={{ left: 12, top: 12 }}
                    >
                        <div className="text-slate-300 font-medium mb-1">
                            Position ({hoveredCell.i + 1}, {hoveredCell.j + 1})
                        </div>
                        {hoveredCell.chainI && hoveredCell.chainJ && (
                            <div className="text-slate-400 mb-1">
                                Chain {hoveredCell.chainI} → Chain {hoveredCell.chainJ}
                            </div>
                        )}
                        <div className="text-green-400 font-mono text-sm">
                            PAE: {hoveredCell.value.toFixed(1)} Å
                        </div>
                    </div>
                )}
            </div>

            {/* Color legend */}
            <div className="flex items-center gap-3 mt-4">
                <span className="text-xs text-slate-400 font-medium">0 Å</span>
                <div
                    className="flex-1 h-4 rounded-md shadow-inner"
                    style={{
                        background: 'linear-gradient(to right, rgb(0, 100, 50), rgb(80, 140, 100), rgb(160, 200, 180), rgb(255, 255, 255))'
                    }}
                />
                <span className="text-xs text-slate-400 font-medium">30 Å</span>
            </div>
            <div className="flex justify-between mt-1 text-[10px] text-slate-500">
                <span>High confidence</span>
                <span>Low confidence</span>
            </div>
        </div>
    );
}

