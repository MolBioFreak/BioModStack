/**
 * GCContentTrack - Visual GC content chart showing GC% across sequence windows
 * 
 * Displays a line chart with GC percentage calculated over sliding windows.
 * Uses blue-to-red gradient coloring based on local GC content.
 */

import { useMemo, useState, useCallback, useRef } from 'react';

interface GCContentTrackProps {
    sequence: string;
    windowSize?: number;      // Size of sliding window (default: 50bp)
    stepSize?: number;        // Step between windows (default: 10bp)
    height?: number;          // Chart height in pixels
    showLabels?: boolean;     // Show axis labels
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
    const results: Array<{ position: number; gc: number }> = [];

    if (sequence.length < windowSize) {
        // For short sequences, just show overall GC
        return [{ position: sequence.length / 2, gc: calculateGC(sequence) }];
    }

    for (let i = 0; i <= sequence.length - windowSize; i += stepSize) {
        const window = sequence.slice(i, i + windowSize);
        const gc = calculateGC(window);
        results.push({
            position: i + windowSize / 2, // Center of window
            gc
        });
    }

    return results;
}

/**
 * Get color based on GC content - gradient from AT-rich (blue) to GC-rich (red)
 * Uses amplified contrast to make typical 40-60% range more distinguishable
 */
function getGCColor(gc: number): string {
    // Amplify contrast: center at 50%, expand differences
    // This makes 40% look very blue and 60% look very red
    const normalizedGC = Math.max(0, Math.min(100, gc));

    // Apply contrast amplification (sigmoid-like curve centered at 50%)
    // Shift so 50% is center, then amplify by 2.5x, then shift back
    const amplified = 50 + (normalizedGC - 50) * 2.5;
    const clamped = Math.max(0, Math.min(100, amplified));

    // Map to hue: 240 (blue) -> 120 (green) -> 0 (red)
    const hue = 240 - (clamped / 100) * 240;
    return `hsl(${hue}, 85%, 50%)`;
}

/**
 * Get badge color based on overall GC
 */
function getBadgeColor(gc: number): string {
    if (gc < 40) return '#3b82f6';      // Blue - AT-rich
    if (gc < 50) return '#22d3ee';      // Cyan
    if (gc < 55) return '#22c55e';      // Green - balanced
    if (gc < 60) return '#eab308';      // Yellow
    return '#ef4444';                    // Red - GC-rich
}

export function GCContentTrack({
    sequence,
    windowSize = 50,
    stepSize = 10,
    height = 120,
    showLabels = true,
    selection
}: GCContentTrackProps) {
    // Hover state for tooltip
    const [hoverInfo, setHoverInfo] = useState<{ x: number; gc: number; position: number } | null>(null);
    const chartRef = useRef<HTMLDivElement>(null);

    // Calculate GC windows
    const gcData = useMemo(() => {
        if (!sequence) return [];
        return calculateGCWindows(sequence, windowSize, stepSize);
    }, [sequence, windowSize, stepSize]);

    // Overall GC%
    const overallGC = useMemo(() => calculateGC(sequence), [sequence]);

    // Always use 0-100 scale for better visualization
    const minGC = 0;
    const maxGC = 100;

    // Get GC at a specific position by interpolating between windows
    const getGCAtPosition = useCallback((seqPos: number) => {
        if (gcData.length === 0) return overallGC;
        if (gcData.length === 1) return gcData[0].gc;

        // Find the two closest windows
        for (let i = 0; i < gcData.length - 1; i++) {
            if (seqPos >= gcData[i].position && seqPos <= gcData[i + 1].position) {
                // Linear interpolation
                const t = (seqPos - gcData[i].position) / (gcData[i + 1].position - gcData[i].position);
                return gcData[i].gc + t * (gcData[i + 1].gc - gcData[i].gc);
            }
        }
        // Return closest endpoint
        if (seqPos < gcData[0].position) return gcData[0].gc;
        return gcData[gcData.length - 1].gc;
    }, [gcData, overallGC]);

    // Mouse move handler for tooltip
    const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
        if (!chartRef.current || !sequence) return;
        const rect = chartRef.current.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const relativeX = x / rect.width;
        const seqPosition = Math.round(relativeX * sequence.length);
        const gc = getGCAtPosition(seqPosition);
        setHoverInfo({ x, gc, position: seqPosition });
    }, [sequence, getGCAtPosition]);

    const handleMouseLeave = useCallback(() => {
        setHoverInfo(null);
    }, []);

    if (!sequence || sequence.length < 10) {
        return null;
    }

    // Chart dimensions - larger for better visibility
    const chartPadding = showLabels
        ? { left: 45, right: 15, top: 10, bottom: 20 }
        : { left: 5, right: 5, top: 5, bottom: 5 };
    const plotHeight = height - chartPadding.top - chartPadding.bottom;

    // Create area fill path (closed path to bottom)
    const createAreaPath = () => {
        if (gcData.length === 0) return '';

        const seqLen = sequence.length;
        const scaleX = (pos: number) => (pos / seqLen) * 100;
        const scaleY = (gc: number) => plotHeight - ((gc - minGC) / (maxGC - minGC)) * plotHeight;

        const points = gcData.map((d, i) => {
            const x = scaleX(d.position);
            const y = scaleY(d.gc);
            return `${i === 0 ? 'M' : 'L'} ${x} ${y}`;
        });

        // Close the path at bottom
        const lastX = scaleX(gcData[gcData.length - 1].position);
        const firstX = scaleX(gcData[0].position);
        points.push(`L ${lastX} ${plotHeight}`);
        points.push(`L ${firstX} ${plotHeight}`);
        points.push('Z');

        return points.join(' ');
    };

    // Selection highlight
    const selectionRect = selection && sequence.length > 0 ? {
        x: (selection.start / sequence.length) * 100,
        width: ((selection.end - selection.start) / sequence.length) * 100
    } : null;

    // Generate line segments with colors based on local GC
    const createColoredSegments = () => {
        if (gcData.length < 2) return null;

        const seqLen = sequence.length;
        const scaleX = (pos: number) => (pos / seqLen) * 100;
        const scaleY = (gc: number) => plotHeight - ((gc - minGC) / (maxGC - minGC)) * plotHeight;

        const segments = [];
        for (let i = 0; i < gcData.length - 1; i++) {
            const d1 = gcData[i];
            const d2 = gcData[i + 1];
            const avgGC = (d1.gc + d2.gc) / 2;
            const x1 = scaleX(d1.position);
            const y1 = scaleY(d1.gc);
            const x2 = scaleX(d2.position);
            const y2 = scaleY(d2.gc);

            segments.push(
                <line
                    key={i}
                    x1={x1}
                    y1={y1}
                    x2={x2}
                    y2={y2}
                    stroke={getGCColor(avgGC)}
                    strokeWidth="2"
                    vectorEffect="non-scaling-stroke"
                />
            );
        }
        return segments;
    };

    return (
        <div
            className="gc-content-track border-b border-slate-700"
            style={{
                height: `${height}px`,
                background: 'linear-gradient(180deg, rgba(30,41,59,0.8) 0%, rgba(30,41,59,0.4) 100%)'
            }}
        >
            <div className="flex h-full">
                {/* Y-axis labels */}
                {showLabels && (
                    <div
                        className="flex flex-col justify-between text-[11px] text-slate-400 pr-1 font-medium"
                        style={{ width: `${chartPadding.left}px`, paddingTop: chartPadding.top, paddingBottom: chartPadding.bottom }}
                    >
                        <span className="text-red-400">100%</span>
                        <span className="text-yellow-400">50%</span>
                        <span className="text-blue-400">0%</span>
                    </div>
                )}

                {/* Chart area */}
                <div
                    ref={chartRef}
                    className="flex-1 relative cursor-crosshair"
                    style={{ paddingTop: chartPadding.top, paddingBottom: chartPadding.bottom }}
                    onMouseMove={handleMouseMove}
                    onMouseLeave={handleMouseLeave}
                >
                    {/* Hover tooltip */}
                    {hoverInfo && (
                        <div
                            className="absolute z-20 px-2 py-1 text-xs font-medium rounded shadow-lg pointer-events-none"
                            style={{
                                left: hoverInfo.x,
                                top: '50%',
                                transform: 'translate(-50%, -50%)',
                                backgroundColor: getGCColor(hoverInfo.gc),
                                color: 'white',
                                textShadow: '0 1px 2px rgba(0,0,0,0.5)'
                            }}
                        >
                            {hoverInfo.gc.toFixed(1)}% @ {hoverInfo.position.toLocaleString()}bp
                        </div>
                    )}

                    <svg
                        width="100%"
                        height={plotHeight}
                        viewBox={`0 0 100 ${plotHeight}`}
                        preserveAspectRatio="none"
                        className="overflow-visible"
                    >
                        {/* Grid lines */}
                        <line
                            x1="0" y1={plotHeight * 0.25}
                            x2="100" y2={plotHeight * 0.25}
                            stroke="#334155"
                            strokeWidth="0.3"
                            vectorEffect="non-scaling-stroke"
                        />
                        <line
                            x1="0" y1={plotHeight * 0.75}
                            x2="100" y2={plotHeight * 0.75}
                            stroke="#334155"
                            strokeWidth="0.3"
                            vectorEffect="non-scaling-stroke"
                        />

                        {/* 50% reference line - prominent */}
                        <line
                            x1="0"
                            y1={plotHeight * 0.5}
                            x2="100"
                            y2={plotHeight * 0.5}
                            stroke="#64748b"
                            strokeWidth="1"
                            strokeDasharray="4,3"
                            vectorEffect="non-scaling-stroke"
                        />

                        {/* Selection highlight */}
                        {selectionRect && (
                            <rect
                                x={selectionRect.x}
                                y={0}
                                width={selectionRect.width}
                                height={plotHeight}
                                fill="#8b5cf6"
                                opacity={0.25}
                            />
                        )}

                        {/* Area fill with gradient */}
                        <path
                            d={createAreaPath()}
                            fill="url(#gcGradientFill)"
                            opacity={0.2}
                        />

                        {/* Colored line segments */}
                        {createColoredSegments()}

                        {/* Gradient definitions */}
                        <defs>
                            <linearGradient id="gcGradientFill" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="0%" stopColor="#ef4444" stopOpacity="0.6" />
                                <stop offset="50%" stopColor="#22c55e" stopOpacity="0.3" />
                                <stop offset="100%" stopColor="#3b82f6" stopOpacity="0.1" />
                            </linearGradient>
                        </defs>
                    </svg>

                    {/* Overall GC badge */}
                    <div
                        className="absolute top-0 right-2 px-2 py-0.5 text-xs font-semibold rounded shadow-sm"
                        style={{
                            backgroundColor: getBadgeColor(overallGC) + '30',
                            color: getBadgeColor(overallGC),
                            border: `1px solid ${getBadgeColor(overallGC)}50`
                        }}
                    >
                        GC: {overallGC.toFixed(1)}%
                    </div>

                    {/* Legend */}
                    <div className="absolute bottom-0 right-2 flex items-center gap-3 text-[10px] text-slate-500">
                        <span className="flex items-center gap-1">
                            <span className="w-2 h-2 rounded-full bg-blue-500"></span>AT-rich
                        </span>
                        <span className="flex items-center gap-1">
                            <span className="w-2 h-2 rounded-full bg-red-500"></span>GC-rich
                        </span>
                    </div>
                </div>
            </div>

            {/* X-axis label */}
            {showLabels && (
                <div className="text-[10px] text-slate-500 text-center -mt-4">
                    Sequence Position • Window: {windowSize}bp
                </div>
            )}
        </div>
    );
}
