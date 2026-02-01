/**
 * GCContentTrack - Visual GC content chart showing GC% across sequence windows
 * 
 * Uses Plotly for smooth, interactive line chart with blue-to-red gradient coloring.
 */

import { useMemo } from 'react';
import Plot from 'react-plotly.js';

interface GCContentTrackProps {
    sequence: string;
    windowSize?: number;      // Size of sliding window (default: 50bp)
    stepSize?: number;        // Step between windows (default: 10bp)
    height?: number;          // Chart height in pixels
    selection?: { start: number; end: number } | null;
}

/**
 * Calculate GC content for a sequence segment
 */
function calculateGC(seq: string): number {
    if (!seq || seq.length === 0) return 0;
    const gc = (seq.match(/[GC]/gi) || []).length;
    return (gc / seq.length) * 100;
}

/**
 * Calculate GC content across sliding windows
 */
function calculateGCWindows(
    sequence: string,
    windowSize: number,
    stepSize: number
): Array<{ position: number; gc: number }> {
    const windows: Array<{ position: number; gc: number }> = [];
    const seq = sequence.toUpperCase();

    for (let i = 0; i <= seq.length - windowSize; i += stepSize) {
        const windowSeq = seq.substring(i, i + windowSize);
        const gc = calculateGC(windowSeq);
        windows.push({
            position: i + Math.floor(windowSize / 2),
            gc
        });
    }

    return windows;
}

/**
 * Get color based on GC content - gradient from AT-rich (blue) to GC-rich (red)
 */
function getGCColor(gc: number): string {
    // Normalize to 0-1 range with amplified contrast
    const normalizedGC = Math.max(0, Math.min(100, gc));
    const amplified = 50 + (normalizedGC - 50) * 2.5;
    const t = Math.max(0, Math.min(100, amplified)) / 100;

    // Blue (low GC) -> Green (50%) -> Red (high GC)
    if (t < 0.5) {
        // Blue to Green
        const ratio = t * 2;
        const r = Math.round(30 + 100 * ratio);
        const g = Math.round(130 + 70 * ratio);
        const b = Math.round(230 - 180 * ratio);
        return `rgb(${r},${g},${b})`;
    } else {
        // Green to Red
        const ratio = (t - 0.5) * 2;
        const r = Math.round(130 + 110 * ratio);
        const g = Math.round(200 - 150 * ratio);
        const b = Math.round(50 - 40 * ratio);
        return `rgb(${r},${g},${b})`;
    }
}

export function GCContentTrack({
    sequence,
    windowSize = 50,
    stepSize = 10,
    height = 100,
    selection
}: GCContentTrackProps) {
    // Calculate GC windows
    const gcData = useMemo(() => {
        if (!sequence) return [];
        return calculateGCWindows(sequence, windowSize, stepSize);
    }, [sequence, windowSize, stepSize]);

    // Compute statistics
    const { avgGC } = useMemo(() => {
        if (gcData.length === 0) return { avgGC: 0 };
        const avg = gcData.reduce((sum, d) => sum + d.gc, 0) / gcData.length;
        return { avgGC: avg };
    }, [gcData]);

    if (!sequence || sequence.length < 10 || gcData.length === 0) {
        return null;
    }

    // Prepare data for Plotly
    const positions = gcData.map(d => d.position);
    const gcValues = gcData.map(d => d.gc);

    // Create color array for each point
    const colors = gcValues.map(gc => getGCColor(gc));

    // Create selection shape if present
    const shapes: Partial<Plotly.Shape>[] = [];
    if (selection && sequence.length > 0) {
        shapes.push({
            type: 'rect',
            x0: selection.start,
            x1: selection.end,
            y0: 0,
            y1: 100,
            fillcolor: 'rgba(139, 92, 246, 0.25)',
            line: { width: 0 }
        });
    }

    // Add 50% reference line
    shapes.push({
        type: 'line',
        x0: 0,
        x1: sequence.length,
        y0: 50,
        y1: 50,
        line: { color: '#64748b', width: 1, dash: 'dot' }
    });

    return (
        <div className="gc-content-track border-b border-slate-700 relative">
            <Plot
                data={[
                    {
                        x: positions,
                        y: gcValues,
                        type: 'scatter',
                        mode: 'lines',
                        line: {
                            width: 2,
                            color: gcValues.map(gc => getGCColor(gc)),
                            shape: 'spline',
                            smoothing: 0.7
                        },
                        hovertemplate: '<b>%{y:.1f}%</b> GC @ %{x:,.0f}bp<extra></extra>',
                        marker: {
                            color: colors,
                            size: 2
                        }
                    }
                ]}
                layout={{
                    width: undefined,
                    height: height,
                    margin: { l: 40, r: 15, t: 8, b: 25 },
                    paper_bgcolor: 'rgba(0,0,0,0)',
                    plot_bgcolor: 'rgba(30,41,59,0.5)',
                    xaxis: {
                        title: undefined,
                        showgrid: false,
                        zeroline: false,
                        color: '#94a3b8',
                        tickfont: { size: 10, color: '#94a3b8' },
                        range: [0, sequence.length],
                        tickformat: ','
                    },
                    yaxis: {
                        title: undefined,
                        showgrid: true,
                        gridcolor: 'rgba(51,65,85,0.5)',
                        zeroline: false,
                        color: '#94a3b8',
                        tickfont: { size: 10, color: '#94a3b8' },
                        range: [0, 100],
                        tickvals: [0, 25, 50, 75, 100],
                        ticktext: ['0%', '25%', '50%', '75%', '100%']
                    },
                    shapes: shapes as any,
                    hovermode: 'x unified',
                    showlegend: false,
                    autosize: true
                }}
                config={{
                    displayModeBar: false,
                    responsive: true,
                    staticPlot: false
                }}
                style={{ width: '100%', height: height }}
                useResizeHandler={true}
            />
            {/* Overall GC badge */}
            <div
                className="absolute top-1 right-2 px-2 py-0.5 rounded text-xs font-bold text-white shadow-sm"
                style={{ backgroundColor: getGCColor(avgGC) }}
            >
                {avgGC.toFixed(1)}% GC
            </div>
        </div>
    );
}
