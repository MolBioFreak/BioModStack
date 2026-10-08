/**
 * GCContentTrack - Visual GC content chart showing GC% across sequence windows
 * 
 * Uses Plotly for smooth, interactive line chart with blue-to-red gradient coloring.
 * Features per-segment color based on GC content and selection-based recomputation.
 * Bidirectional selection sync with sequence viewer.
 */

import { useMemo, useCallback } from 'react';
import Plot from 'react-plotly.js';
import type { Data, Layout, Shape, PlotRelayoutEvent } from 'plotly.js';
import type { SelectionInfo } from './SequenceViewer';

interface GCContentTrackProps {
    sequence: string;
    windowSize?: number;      // Size of sliding window (default: 50bp)
    stepSize?: number;        // Step between windows (default: 10bp)
    height?: number;          // Chart height in pixels
    selection?: SelectionInfo | null;
    onSelectionChange?: (selection: SelectionInfo) => void;
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
 * Uses HSL for smooth color transitions
 */
function getGCColor(gc: number): string {
    // Normalize and amplify contrast
    const normalizedGC = Math.max(0, Math.min(100, gc));
    const amplified = 50 + (normalizedGC - 50) * 2.5;
    const t = Math.max(0, Math.min(100, amplified)) / 100;

    // Map to hue: 240 (blue) -> 120 (green) -> 0 (red)
    const hue = 240 - t * 240;
    return `hsl(${hue}, 85%, 50%)`;
}

export function GCContentTrack({
    sequence,
    windowSize = 50,
    stepSize = 10,
    height = 100,
    selection,
    onSelectionChange
}: GCContentTrackProps) {
    // Calculate GC windows
    const gcData = useMemo(() => {
        if (!sequence) return [];
        return calculateGCWindows(sequence, windowSize, stepSize);
    }, [sequence, windowSize, stepSize]);

    // Compute overall statistics
    const { avgGC } = useMemo(() => {
        if (gcData.length === 0) return { avgGC: 0 };
        const avg = gcData.reduce((sum, d) => sum + d.gc, 0) / gcData.length;
        return { avgGC: avg };
    }, [gcData]);

    // Calculate selection-specific GC
    const selectionGC = useMemo(() => {
        if (!selection || !sequence) return null;
        const start = Math.max(0, selection.start);
        const end = Math.min(sequence.length, selection.end);
        if (end <= start) return null;

        const selectedSeq = sequence.substring(start, end);
        return {
            gc: calculateGC(selectedSeq),
            length: end - start
        };
    }, [selection, sequence]);

    // Handle Plotly selection/zoom events
    const handleRelayout = useCallback((event: PlotRelayoutEvent) => {
        if (!onSelectionChange || !sequence) return;

        // Check if this is a zoom/selection event on x-axis
        if ('xaxis.range[0]' in event && 'xaxis.range[1]' in event) {
            const start = Math.max(0, Math.floor(event['xaxis.range[0]'] as number));
            const end = Math.min(sequence.length, Math.ceil(event['xaxis.range[1]'] as number));

            if (end > start && (end - start) < sequence.length * 0.95) {
                // Only trigger selection if it's not the full sequence
                onSelectionChange({ start, end });
            }
        }
        // Note: autorange/double-click resets are handled by the parent component
    }, [onSelectionChange, sequence]);

    if (!sequence || sequence.length < 10 || gcData.length === 0) {
        return null;
    }

    // Create multiple traces - each segment gets its own color
    const traces: Data[] = [];

    for (let i = 0; i < gcData.length - 1; i++) {
        const d1 = gcData[i];
        const d2 = gcData[i + 1];
        const avgGCSegment = (d1.gc + d2.gc) / 2;

        traces.push({
            x: [d1.position, d2.position],
            y: [d1.gc, d2.gc],
            type: 'scatter',
            mode: 'lines',
            line: {
                width: 2.5,
                color: getGCColor(avgGCSegment),
                shape: 'spline'
            },
            hoverinfo: 'skip',
            showlegend: false
        });
    }

    // Add invisible scatter for hover tooltips
    traces.push({
        x: gcData.map(d => d.position),
        y: gcData.map(d => d.gc),
        type: 'scatter',
        mode: 'markers',
        marker: {
            size: 1,
            color: 'transparent'
        },
        hovertemplate: '<b>%{y:.1f}%</b> GC @ %{x:,.0f}bp<extra></extra>',
        showlegend: false
    });

    // Create shapes for selection and reference line
    const shapes: Partial<Shape>[] = [];

    // 50% reference line
    shapes.push({
        type: 'line',
        x0: 0,
        x1: sequence.length,
        y0: 50,
        y1: 50,
        line: { color: '#64748b', width: 1, dash: 'dot' }
    });

    // Selection highlight
    if (selection && sequence.length > 0) {
        shapes.push({
            type: 'rect',
            x0: selection.start,
            x1: selection.end,
            y0: 0,
            y1: 100,
            fillcolor: 'rgba(139, 92, 246, 0.3)',
            line: { color: 'rgba(139, 92, 246, 0.8)', width: 1 }
        });
    }

    const layout: Partial<Layout> = {
        height: height,
        margin: { l: 40, r: 15, t: 8, b: 25 },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(30,41,59,0.3)',
        xaxis: {
            showgrid: false,
            zeroline: false,
            color: '#94a3b8',
            tickfont: { size: 10, color: '#94a3b8' },
            range: [0, sequence.length],
            tickformat: ','
        },
        yaxis: {
            showgrid: true,
            gridcolor: 'rgba(51,65,85,0.5)',
            zeroline: false,
            color: '#94a3b8',
            tickfont: { size: 10, color: '#94a3b8' },
            range: [0, 100],
            tickvals: [0, 25, 50, 75, 100],
            ticktext: ['0%', '25%', '50%', '75%', '100%']
        },
        shapes: shapes,
        hovermode: 'closest',
        showlegend: false,
        dragmode: 'zoom'  // Enable zoom selection on drag
    };

    return (
        <div className="gc-content-track border-b border-slate-700 relative">
            <Plot
                data={traces}
                layout={layout}
                config={{
                    displayModeBar: false,
                    responsive: true,
                    staticPlot: false,
                    scrollZoom: true  // Enable scroll zoom
                }}
                style={{ width: '100%', height: height }}
                useResizeHandler={true}
                onRelayout={handleRelayout}
            />

            {/* Overall GC badge */}
            <div
                className="absolute top-1 right-2 px-2 py-0.5 rounded text-xs font-bold text-white shadow-sm"
                style={{ backgroundColor: getGCColor(avgGC) }}
            >
                {avgGC.toFixed(1)}% GC
            </div>

            {/* Selection GC badge - shows when region is selected */}
            {selectionGC && (
                <div
                    className="absolute top-1 left-12 px-2 py-0.5 rounded text-xs font-bold text-white shadow-sm border border-purple-400"
                    style={{ backgroundColor: getGCColor(selectionGC.gc) }}
                >
                    Selection: {selectionGC.gc.toFixed(1)}% GC ({selectionGC.length.toLocaleString()}bp)
                </div>
            )}
        </div>
    );
}
