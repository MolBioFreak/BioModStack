/**
 * PAE Heatmap Component
 * 
 * Renders a Predicted Aligned Error (PAE) heatmap using canvas.
 * Green = low error (good), White = high error (poor)
 */

import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

interface PAEData {
    design_id: string;
    design_name: string;
    pae_matrix: number[][];
    size: number;
}

interface PAEHeatmapProps {
    designId: string;
    width?: number;
    height?: number;
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

export function PAEHeatmap({ designId, width = 300, height = 300 }: PAEHeatmapProps) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const [hoveredCell, setHoveredCell] = useState<{ i: number; j: number; value: number } | null>(null);

    const { data: paeData, isLoading, error } = useQuery({
        queryKey: ['pae', designId],
        queryFn: () => fetchPAEData(designId),
        enabled: !!designId,
    });

    const pae = paeData?.data;

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

        // Draw diagonal line for reference
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(width, height);
        ctx.stroke();

    }, [pae, width, height]);

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
            setHoveredCell({ i, j, value: pae.pae_matrix[i][j] });
        }
    };

    if (isLoading) {
        return (
            <div className="flex items-center justify-center bg-slate-800/50 rounded-lg" style={{ width, height }}>
                <div className="w-6 h-6 border-2 border-green-500 border-t-transparent rounded-full animate-spin" />
            </div>
        );
    }

    if (error || !pae) {
        return (
            <div className="flex items-center justify-center bg-slate-800/50 rounded-lg text-slate-500 text-sm" style={{ width, height }}>
                No PAE data available
            </div>
        );
    }

    return (
        <div className="relative">
            <div className="text-xs text-slate-400 mb-2 font-medium">
                Predicted Aligned Error (PAE)
            </div>
            <div className="relative">
                <canvas
                    ref={canvasRef}
                    width={width}
                    height={height}
                    className="rounded-lg border border-slate-700"
                    onMouseMove={handleMouseMove}
                    onMouseLeave={() => setHoveredCell(null)}
                />

                {/* Axis labels */}
                <div className="absolute -bottom-5 left-1/2 -translate-x-1/2 text-xs text-slate-500">
                    Scored residue
                </div>
                <div className="absolute top-1/2 -left-6 -translate-y-1/2 -rotate-90 text-xs text-slate-500">
                    Aligned residue
                </div>

                {/* Tooltip */}
                {hoveredCell && (
                    <div
                        className="absolute z-10 bg-slate-900/95 border border-slate-600 rounded px-2 py-1 text-xs pointer-events-none"
                        style={{ left: 10, top: 10 }}
                    >
                        <div className="text-slate-400">
                            Position: ({hoveredCell.i + 1}, {hoveredCell.j + 1})
                        </div>
                        <div className="text-green-400 font-mono">
                            PAE: {hoveredCell.value.toFixed(1)} Å
                        </div>
                    </div>
                )}
            </div>

            {/* Color legend */}
            <div className="flex items-center gap-2 mt-3 text-xs">
                <span className="text-slate-500">0 Å</span>
                <div
                    className="flex-1 h-3 rounded"
                    style={{
                        background: 'linear-gradient(to right, rgb(0, 100, 50), rgb(128, 177, 152), rgb(255, 255, 255))'
                    }}
                />
                <span className="text-slate-500">30 Å</span>
            </div>
        </div>
    );
}
