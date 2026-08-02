import { useEffect, useMemo, useRef, useState } from 'react';
import {
    CANONICAL_AMINO_ACIDS,
    type CmLandscapeResidue,
} from './conformationalMapping/conformationalMappingSemantics.js';

const CLASS_COLORS = {
    high: '#ef4444',
    neutral: '#d97706',
    minimal: '#0891b2',
    unavailable: '#334155',
} as const;

export interface FrustraMpnnOverviewCell {
    residueIndex: number;
    mutationIndex: number;
    residue: CmLandscapeResidue;
    mutationAa: string;
    score: number | null;
    className: string | null;
    status: string;
    reason: string | null;
    isNative: boolean;
}

export interface FrustraMpnnOverviewModel {
    residues: CmLandscapeResidue[];
    cells: FrustraMpnnOverviewCell[];
    chains: Array<{ authAsymId: string; start: number; end: number }>;
}

export function buildFrustraMpnnOverviewModel(residues: CmLandscapeResidue[]): FrustraMpnnOverviewModel {
    const cells: FrustraMpnnOverviewCell[] = [];
    const chains: FrustraMpnnOverviewModel['chains'] = [];
    residues.forEach((residue, residueIndex) => {
        const lastChain = chains.at(-1);
        if (!lastChain || lastChain.authAsymId !== residue.auth_asym_id) {
            chains.push({ authAsymId: residue.auth_asym_id, start: residueIndex, end: residueIndex + 1 });
        } else {
            lastChain.end = residueIndex + 1;
        }
        CANONICAL_AMINO_ACIDS.forEach((mutationAa, mutationIndex) => {
            const slot = residue.slots.find((candidate) => candidate.mutation_aa === mutationAa);
            if (!slot) throw new Error(`missing_exact_20_slot:${residue.key}:${mutationAa}`);
            cells.push({
                residueIndex,
                mutationIndex,
                residue,
                mutationAa,
                score: slot.score,
                className: slot.class,
                status: slot.status,
                reason: slot.reason,
                isNative: mutationAa === residue.wt,
            });
        });
    });
    return { residues, cells, chains };
}

const identityLabel = (residue: CmLandscapeResidue): string => (
    `${residue.auth_asym_id}:${residue.auth_seq_id}${residue.insertion_code || ''}`
);

export default function FrustraMpnnLandscapeOverview({
    residues,
    onSelectResidue,
}: {
    residues: CmLandscapeResidue[];
    onSelectResidue: (residue: CmLandscapeResidue) => void;
}) {
    const canvasRef = useRef<HTMLCanvasElement | null>(null);
    const wrapperRef = useRef<HTMLDivElement | null>(null);
    const [width, setWidth] = useState(1200);
    const [hovered, setHovered] = useState<FrustraMpnnOverviewCell | null>(null);
    const model = useMemo(() => buildFrustraMpnnOverviewModel(residues), [residues]);
    const height = 350;
    const left = 30;
    const top = 34;
    const right = 8;
    const bottom = 22;
    const matrixWidth = Math.max(1, width - left - right);
    const matrixHeight = height - top - bottom;
    const cellWidth = matrixWidth / Math.max(1, model.residues.length);
    const cellHeight = matrixHeight / CANONICAL_AMINO_ACIDS.length;

    useEffect(() => {
        const node = wrapperRef.current;
        if (!node) return;
        const update = () => setWidth(Math.max(640, Math.floor(node.clientWidth)));
        update();
        const observer = new ResizeObserver(update);
        observer.observe(node);
        return () => observer.disconnect();
    }, []);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ratio = window.devicePixelRatio || 1;
        canvas.width = Math.floor(width * ratio);
        canvas.height = Math.floor(height * ratio);
        canvas.style.width = `${width}px`;
        canvas.style.height = `${height}px`;
        const context = canvas.getContext('2d');
        if (!context) return;
        context.setTransform(ratio, 0, 0, ratio, 0, 0);
        context.fillStyle = '#020617';
        context.fillRect(0, 0, width, height);
        context.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';
        context.textAlign = 'right';
        context.textBaseline = 'middle';
        context.fillStyle = '#94a3b8';
        CANONICAL_AMINO_ACIDS.forEach((aa, index) => context.fillText(aa, left - 7, top + ((index + 0.5) * cellHeight)));

        model.cells.forEach((cell) => {
            const x = left + (cell.residueIndex * cellWidth);
            const y = top + (cell.mutationIndex * cellHeight);
            const classKey = cell.status === 'ok' && cell.className && cell.className in CLASS_COLORS
                ? cell.className as keyof typeof CLASS_COLORS
                : 'unavailable';
            context.fillStyle = CLASS_COLORS[classKey];
            context.fillRect(x, y, Math.max(1, cellWidth + 0.15), Math.max(1, cellHeight + 0.15));
            if (cell.isNative && cellWidth >= 2) {
                context.fillStyle = 'rgba(255,255,255,0.72)';
                context.fillRect(x, y, Math.max(1, cellWidth), 1);
            }
        });

        context.strokeStyle = '#94a3b8';
        context.fillStyle = '#cbd5e1';
        context.textAlign = 'left';
        context.textBaseline = 'bottom';
        model.chains.forEach((chain) => {
            const x = left + (chain.start * cellWidth);
            if (chain.start > 0) {
                context.beginPath();
                context.moveTo(x, top);
                context.lineTo(x, top + matrixHeight);
                context.stroke();
            }
            context.fillText(`Chain ${chain.authAsymId}`, x + 3, top - 7);
        });
        context.strokeStyle = '#475569';
        context.strokeRect(left, top, matrixWidth, matrixHeight);
    }, [cellHeight, cellWidth, height, matrixHeight, matrixWidth, model, width]);

    const locateCell = (clientX: number, clientY: number): FrustraMpnnOverviewCell | null => {
        const canvas = canvasRef.current;
        if (!canvas) return null;
        const rect = canvas.getBoundingClientRect();
        const x = clientX - rect.left;
        const y = clientY - rect.top;
        const residueIndex = Math.floor((x - left) / cellWidth);
        const mutationIndex = Math.floor((y - top) / cellHeight);
        if (residueIndex < 0 || residueIndex >= model.residues.length || mutationIndex < 0 || mutationIndex >= CANONICAL_AMINO_ACIDS.length) return null;
        return model.cells[(residueIndex * CANONICAL_AMINO_ACIDS.length) + mutationIndex] ?? null;
    };

    return (
        <section aria-label="Complete FrustraMPNN mutation landscape" className="border-b border-slate-800 bg-slate-950/40 p-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h3 className="text-sm font-semibold text-slate-200">Complete mutation landscape</h3>
                    <p className="mt-1 max-w-4xl text-xs text-slate-500">All {model.residues.length.toLocaleString()} exact author residues × 20 substitutions ({model.cells.length.toLocaleString()} persisted slots). Columns are residues; rows are mutation amino acids. Colors render the backend-owned canonical class without recalculating thresholds.</p>
                </div>
                <div aria-label="Canonical frustration class legend" className="flex flex-wrap gap-3 text-[11px] text-slate-300">
                    {([
                        ['high', 'Highly frustrated'],
                        ['neutral', 'Neutral'],
                        ['minimal', 'Minimally frustrated'],
                        ['unavailable', 'Missing'],
                    ] as const).map(([key, label]) => <span key={key} className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: CLASS_COLORS[key] }} />{label}</span>)}
                    <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 border-t border-white/70 bg-slate-700" />Native slot</span>
                </div>
            </div>
            <div ref={wrapperRef} className="mt-3 w-full overflow-hidden rounded-lg border border-slate-800 bg-slate-950">
                <canvas
                    ref={canvasRef}
                    role="img"
                    aria-label={`Complete FrustraMPNN heatmap showing ${model.residues.length} residues and ${model.cells.length} substitution slots`}
                    className="block cursor-crosshair"
                    onMouseMove={(event) => setHovered(locateCell(event.clientX, event.clientY))}
                    onMouseLeave={() => setHovered(null)}
                    onClick={(event) => {
                        const cell = locateCell(event.clientX, event.clientY);
                        if (cell) onSelectResidue(cell.residue);
                    }}
                />
            </div>
            <div aria-live="polite" className="mt-2 min-h-5 font-mono text-[11px] text-slate-300">
                {hovered ? `${identityLabel(hovered.residue)} · WT ${hovered.residue.wt} → ${hovered.mutationAa} · score ${hovered.score == null ? 'unavailable' : hovered.score.toFixed(3)} · ${hovered.className ?? hovered.status}${hovered.reason ? ` · ${hovered.reason}` : ''}` : 'Hover for exact score and identity; click a column to select that residue in the structure/detail views.'}
            </div>
        </section>
    );
}
