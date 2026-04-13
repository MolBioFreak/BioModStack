import { useMemo } from 'react';
import type { AnalysisTrack } from './SequenceViewer';
import type { RnaPairProbability, RnaStructureResult } from '../../lib/api';

export type RnaStructureDisplayMode = 'mfe' | 'centroid' | 'mea' | 'probability';

interface RnaStructureViewerProps {
    result: RnaStructureResult;
    displayMode: RnaStructureDisplayMode;
    evidenceTrack?: AnalysisTrack | null;
    className?: string;
}

type ArcPair = {
    i: number;
    j: number;
    probability: number;
};

const BASE_COLORS: Record<string, string> = {
    A: '#22c55e',
    U: '#ec4899',
    G: '#f59e0b',
    C: '#3b82f6',
};

const OPEN_TO_CLOSE: Record<string, string> = {
    '(': ')',
    '[': ']',
    '{': '}',
    '<': '>',
};

const CLOSE_TO_OPEN = Object.fromEntries(
    Object.entries(OPEN_TO_CLOSE).map(([open, close]) => [close, open]),
) as Record<string, string>;

function clamp(value: number, min: number, max: number): number {
    return Math.max(min, Math.min(max, value));
}

function parseDotBracketPairs(dotBracket: string): Array<{ i: number; j: number }> {
    const stacks = new Map<string, number[]>();
    const pairs: Array<{ i: number; j: number }> = [];

    for (let index = 0; index < dotBracket.length; index += 1) {
        const char = dotBracket[index];
        if (OPEN_TO_CLOSE[char]) {
            const stack = stacks.get(char) ?? [];
            stack.push(index);
            stacks.set(char, stack);
            continue;
        }

        const opener = CLOSE_TO_OPEN[char];
        if (!opener) continue;
        const stack = stacks.get(opener);
        const partner = stack?.pop();
        if (partner == null) continue;
        pairs.push({ i: partner, j: index });
    }

    return pairs.sort((left, right) => left.i - right.i || left.j - right.j);
}

function buildProbabilityMap(pairs: RnaPairProbability[]): Map<string, number> {
    const map = new Map<string, number>();
    for (const pair of pairs) {
        const i = Math.min(pair.i, pair.j);
        const j = Math.max(pair.i, pair.j);
        map.set(`${i}:${j}`, pair.probability);
    }
    return map;
}

function buildArcPath(x1: number, x2: number, baselineY: number, maxHeight: number): string {
    const distance = Math.max(1, x2 - x1);
    const height = Math.min(maxHeight, 18 + Math.sqrt(distance) * 10);
    const mid = (x1 + x2) / 2;
    return `M ${x1.toFixed(2)} ${baselineY.toFixed(2)} Q ${mid.toFixed(2)} ${(baselineY - height).toFixed(2)} ${x2.toFixed(2)} ${baselineY.toFixed(2)}`;
}

function normalizeTrackValues(track?: AnalysisTrack | null): {
    values: Array<number | null>;
    min: number;
    max: number;
} | null {
    if (!track || !track.values.length) return null;
    const numeric = track.values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
    if (numeric.length === 0) return null;

    const min = track.minValue ?? Math.min(...numeric);
    const max = track.maxValue ?? Math.max(...numeric);
    return { values: track.values, min, max };
}

function buildPolylinePath(
    values: Array<number | null>,
    width: number,
    startX: number,
    topY: number,
    bottomY: number,
    minValue: number,
    maxValue: number,
): string {
    const span = Math.max(1e-6, maxValue - minValue);
    const step = values.length > 1 ? width / (values.length - 1) : width;
    const points = values.flatMap((value, index) => {
        if (value == null || !Number.isFinite(value)) return [];
        const normalized = (value - minValue) / span;
        const x = startX + index * step;
        const y = bottomY - normalized * (bottomY - topY);
        return `${x.toFixed(2)},${y.toFixed(2)}`;
    });
    return points.length > 1 ? `M ${points.join(' L ')}` : '';
}

function modeLabel(mode: RnaStructureDisplayMode): string {
    switch (mode) {
        case 'centroid':
            return 'Centroid';
        case 'mea':
            return 'MEA';
        case 'probability':
            return 'Probability';
        default:
            return 'MFE';
    }
}

export function RnaStructureViewer({
    result,
    displayMode,
    evidenceTrack,
    className,
}: RnaStructureViewerProps) {
    const width = useMemo(
        () => Math.max(960, Math.min(2600, result.length <= 120 ? result.length * 8 : result.length * 2.2)),
        [result.length],
    );
    const baselineY = 190;
    const pairedTrackTop = 216;
    const pairedTrackBottom = 256;
    const hasEvidenceTrack = Boolean(evidenceTrack?.values?.length);
    const evidenceTop = 288;
    const evidenceBottom = 338;
    const height = hasEvidenceTrack ? 360 : 274;
    const startX = 30;
    const innerWidth = width - startX * 2;
    const step = result.length > 1 ? innerWidth / (result.length - 1) : innerWidth;

    const probabilityMap = useMemo(
        () => buildProbabilityMap(result.pair_probabilities),
        [result.pair_probabilities],
    );

    const displayPairs = useMemo<ArcPair[]>(() => {
        if (displayMode === 'probability') {
            return result.pair_probabilities.map((pair) => ({
                i: pair.i,
                j: pair.j,
                probability: pair.probability,
            }));
        }

        const structure =
            displayMode === 'centroid'
                ? result.centroid?.dot_bracket
                : displayMode === 'mea'
                    ? result.mea?.dot_bracket
                    : result.mfe.dot_bracket;
        if (!structure) return [];

        return parseDotBracketPairs(structure).map((pair) => ({
            ...pair,
            probability: probabilityMap.get(`${pair.i}:${pair.j}`) ?? 1,
        }));
    }, [displayMode, probabilityMap, result.centroid?.dot_bracket, result.mea?.dot_bracket, result.mfe.dot_bracket, result.pair_probabilities]);

    const activeStructure = useMemo(() => {
        if (displayMode === 'centroid') return result.centroid;
        if (displayMode === 'mea') return result.mea;
        return result.mfe;
    }, [displayMode, result.centroid, result.mea, result.mfe]);

    const pairedProbabilityPath = useMemo(
        () => buildPolylinePath(
            result.bases.map((base) => base.paired_probability),
            innerWidth,
            startX,
            pairedTrackTop,
            pairedTrackBottom,
            0,
            1,
        ),
        [innerWidth, result.bases, startX],
    );

    const evidenceSeries = useMemo(
        () => normalizeTrackValues(evidenceTrack),
        [evidenceTrack],
    );

    const evidencePath = useMemo(
        () => evidenceSeries
            ? buildPolylinePath(
                evidenceSeries.values,
                innerWidth,
                startX,
                evidenceTop,
                evidenceBottom,
                evidenceSeries.min,
                evidenceSeries.max === evidenceSeries.min ? evidenceSeries.min + 1 : evidenceSeries.max,
            )
            : '',
        [evidenceBottom, evidenceSeries, evidenceTop, innerWidth, startX],
    );

    const tickStride = result.length <= 120 ? 10 : result.length <= 600 ? 50 : 100;

    return (
        <div className={`h-full w-full overflow-auto bg-slate-950/80 ${className || ''}`}>
            <div className="sticky top-0 z-10 border-b border-slate-800 bg-slate-950/95 px-4 py-3">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="rounded-full border border-violet-500/40 bg-violet-500/10 px-2 py-1 font-medium text-violet-200">
                        {modeLabel(displayMode)}
                    </span>
                    {activeStructure?.energy_kcal_mol != null && (
                        <span className="rounded-full border border-slate-700 bg-slate-900 px-2 py-1 text-slate-300">
                            ΔG {activeStructure.energy_kcal_mol.toFixed(2)} kcal/mol
                        </span>
                    )}
                    {result.partition && (
                        <span className="rounded-full border border-slate-700 bg-slate-900 px-2 py-1 text-slate-300">
                            Ensemble {result.partition.ensemble_free_energy_kcal_mol.toFixed(2)} kcal/mol
                        </span>
                    )}
                    <span className="rounded-full border border-slate-700 bg-slate-900 px-2 py-1 text-slate-300">
                        {displayPairs.length} displayed pairs
                    </span>
                    {evidenceTrack && (
                        <span className="rounded-full border border-cyan-500/40 bg-cyan-500/10 px-2 py-1 text-cyan-200">
                            Evidence: {evidenceTrack.name}
                        </span>
                    )}
                </div>
                <div className="mt-2 text-[11px] leading-5 text-slate-400">
                    Probability-weighted pair arcs are drawn from the current structure selection or from the partition ensemble.
                    {result.warnings.length > 0 && (
                        <span className="ml-2 text-amber-300">{result.warnings.join(' • ')}</span>
                    )}
                </div>
            </div>

            <div className="px-4 py-4">
                <svg
                    className="w-full rounded-2xl border border-slate-800 bg-slate-900"
                    viewBox={`0 0 ${width} ${height}`}
                    preserveAspectRatio="none"
                >
                    <rect x="0" y="0" width={width} height={height} fill="#0f172a" />

                    <line x1={startX} y1={baselineY} x2={width - startX} y2={baselineY} stroke="#334155" strokeWidth="1.5" />

                    {displayPairs.map((pair, index) => {
                        const x1 = startX + pair.i * step;
                        const x2 = startX + pair.j * step;
                        const opacity = displayMode === 'probability'
                            ? clamp(pair.probability, 0.12, 1)
                            : clamp((pair.probability || 0.6) * 1.1, 0.22, 1);
                        const strokeWidth = displayMode === 'probability'
                            ? clamp(1 + pair.probability * 4, 1.2, 4.8)
                            : clamp(1.8 + pair.probability * 2.2, 1.8, 4.2);
                        return (
                            <path
                                key={`${pair.i}-${pair.j}-${index}`}
                                d={buildArcPath(x1, x2, baselineY, 150)}
                                fill="none"
                                stroke={displayMode === 'probability' ? '#7c3aed' : '#8b5cf6'}
                                strokeWidth={strokeWidth}
                                opacity={opacity}
                            />
                        );
                    })}

                    {Array.from({ length: result.length }).map((_, index) => {
                        const x = startX + index * step;
                        const base = result.sequence[index];
                        const color = BASE_COLORS[base] || '#94a3b8';
                        const showLabel = result.length <= 120;
                        return (
                            <g key={`base-${index}`}>
                                <line x1={x} y1={baselineY - 6} x2={x} y2={baselineY + 6} stroke="#475569" strokeWidth={0.8} />
                                {showLabel ? (
                                    <text
                                        x={x}
                                        y={baselineY + 22}
                                        textAnchor="middle"
                                        fontSize="10"
                                        fill={color}
                                        fontFamily="monospace"
                                    >
                                        {base}
                                    </text>
                                ) : (
                                    <rect
                                        x={x - Math.max(0.6, step * 0.35)}
                                        y={baselineY + 8}
                                        width={Math.max(1.2, step * 0.7)}
                                        height={10}
                                        fill={color}
                                        opacity={0.9}
                                    />
                                )}
                                {index % tickStride === 0 && (
                                    <text
                                        x={x}
                                        y={baselineY - 12}
                                        textAnchor="middle"
                                        fontSize="9"
                                        fill="#94a3b8"
                                        fontFamily="monospace"
                                    >
                                        {index + 1}
                                    </text>
                                )}
                            </g>
                        );
                    })}

                    <text x={startX} y={pairedTrackTop - 10} fill="#a5f3fc" fontSize="10" fontFamily="sans-serif">
                        Paired probability
                    </text>
                    <rect x={startX} y={pairedTrackTop} width={innerWidth} height={pairedTrackBottom - pairedTrackTop} fill="rgba(34,197,94,0.06)" rx="6" />
                    {pairedProbabilityPath && (
                        <path d={pairedProbabilityPath} fill="none" stroke="#22c55e" strokeWidth="2" />
                    )}
                    <line x1={startX} y1={pairedTrackBottom} x2={width - startX} y2={pairedTrackBottom} stroke="#334155" strokeWidth="1" />

                    {evidenceSeries && (
                        <>
                            <text x={startX} y={evidenceTop - 10} fill={evidenceTrack?.color || '#67e8f9'} fontSize="10" fontFamily="sans-serif">
                                {evidenceTrack?.name || 'Evidence track'}
                            </text>
                            <rect x={startX} y={evidenceTop} width={innerWidth} height={evidenceBottom - evidenceTop} fill="rgba(103,232,249,0.06)" rx="6" />
                            {evidenceSeries.min < 0 && evidenceSeries.max > 0 && (
                                <line
                                    x1={startX}
                                    y1={evidenceBottom - ((0 - evidenceSeries.min) / (evidenceSeries.max - evidenceSeries.min)) * (evidenceBottom - evidenceTop)}
                                    x2={width - startX}
                                    y2={evidenceBottom - ((0 - evidenceSeries.min) / (evidenceSeries.max - evidenceSeries.min)) * (evidenceBottom - evidenceTop)}
                                    stroke="#334155"
                                    strokeDasharray="4 4"
                                    strokeWidth="1"
                                />
                            )}
                            {evidencePath && (
                                <path d={evidencePath} fill="none" stroke={evidenceTrack?.color || '#67e8f9'} strokeWidth="2" />
                            )}
                            <line x1={startX} y1={evidenceBottom} x2={width - startX} y2={evidenceBottom} stroke="#334155" strokeWidth="1" />
                            <text x={width - startX} y={evidenceTop - 10} textAnchor="end" fill="#94a3b8" fontSize="9" fontFamily="sans-serif">
                                {evidenceSeries.min.toFixed(2)} to {evidenceSeries.max.toFixed(2)}
                            </text>
                        </>
                    )}
                </svg>

                <div className="mt-3 rounded-xl border border-slate-800 bg-slate-900 px-3 py-2">
                    <div className="mb-2 text-[11px] uppercase tracking-[0.18em] text-slate-500">Dot-Bracket</div>
                    <div className="max-h-24 overflow-y-auto break-all font-mono text-[11px] leading-5 text-slate-300">
                        {displayMode === 'probability'
                            ? (result.partition?.dot_bracket || 'No partition structure available')
                            : (activeStructure?.dot_bracket || 'No structure available')}
                    </div>
                </div>
            </div>
        </div>
    );
}

export default RnaStructureViewer;
