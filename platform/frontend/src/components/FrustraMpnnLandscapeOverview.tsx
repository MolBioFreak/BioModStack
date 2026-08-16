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
    selectedResidue,
    onSelectResidue,
}: {
    residues: CmLandscapeResidue[];
    selectedResidue?: { authAsymId?: string; authSeqId?: number; insertionCode?: string } | null;
    onSelectResidue: (residue: CmLandscapeResidue) => void;
}) {
    const canvasRef = useRef<HTMLCanvasElement | null>(null);
    const wrapperRef = useRef<HTMLDivElement | null>(null);
    const [width, setWidth] = useState(1200);
    const [hovered, setHovered] = useState<FrustraMpnnOverviewCell | null>(null);
    const model = useMemo(() => buildFrustraMpnnOverviewModel(residues), [residues]);
    const selectedIndex = selectedResidue ? model.residues.findIndex((residue) => (
        residue.auth_asym_id === selectedResidue.authAsymId
        && residue.auth_seq_id === String(selectedResidue.authSeqId)
        && residue.insertion_code === (selectedResidue.insertionCode ?? '')
    )) : -1;
    const height = 410;
    const left = 42;
    const top = 56;
    const right = 12;
    const bottom = 40;
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
        context.fillStyle = '#07101f';
        context.fillRect(0, 0, width, height);
        context.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';
        context.textAlign = 'right';
        context.textBaseline = 'middle';
        context.fillStyle = '#94a3b8';
        CANONICAL_AMINO_ACIDS.forEach((aa, index) => {
            if (index % 2 === 0) {
                context.fillStyle = 'rgba(148,163,184,0.035)';
                context.fillRect(left, top + (index * cellHeight), matrixWidth, cellHeight);
            }
            context.fillStyle = '#cbd5e1';
            context.fillText(aa, left - 10, top + ((index + 0.5) * cellHeight));
        });

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
        model.chains.forEach((chain, chainIndex) => {
            const x = left + (chain.start * cellWidth);
            const chainWidth = (chain.end - chain.start) * cellWidth;
            context.fillStyle = chainIndex % 2 === 0 ? 'rgba(8,145,178,0.12)' : 'rgba(99,102,241,0.12)';
            context.fillRect(x, 22, chainWidth, 23);
            if (chain.start > 0) {
                context.beginPath();
                context.moveTo(x, top);
                context.lineTo(x, top + matrixHeight);
                context.stroke();
            }
            context.fillStyle = '#e2e8f0';
            context.fillText(`Chain ${chain.authAsymId} · ${chain.end - chain.start} residues`, x + 5, top - 15);
        });
        const tickStep = Math.max(10, Math.ceil(model.residues.length / 12 / 10) * 10);
        context.font = '9px ui-monospace, SFMono-Regular, Menlo, monospace';
        context.textAlign = 'center';
        context.textBaseline = 'top';
        context.fillStyle = '#64748b';
        model.residues.forEach((residue, index) => {
            if (index % tickStep !== 0 && index !== model.residues.length - 1) return;
            const x = left + ((index + 0.5) * cellWidth);
            context.fillText(`${residue.auth_asym_id}:${residue.auth_seq_id}${residue.insertion_code}`, x, top + matrixHeight + 9);
        });
        const focusedIndex = hovered?.residueIndex ?? selectedIndex;
        if (focusedIndex >= 0) {
            const x = left + (focusedIndex * cellWidth);
            context.strokeStyle = hovered ? '#f8fafc' : '#22d3ee';
            context.lineWidth = hovered ? 1 : 2;
            context.strokeRect(x, top - 1, Math.max(2, cellWidth), matrixHeight + 2);
            context.lineWidth = 1;
        }
        context.strokeStyle = '#475569';
        context.strokeRect(left, top, matrixWidth, matrixHeight);
    }, [cellHeight, cellWidth, height, hovered, matrixHeight, matrixWidth, model, selectedIndex, width]);

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
                    <h3 className="text-sm font-semibold text-slate-200">Mutation × residue map</h3>
                    <p className="mt-1 max-w-4xl text-xs text-slate-500">Scan every exact author residue and all 20 substitutions in one bounded map. Click a cell to open that residue's exact 20-slot profile and synchronize the structure selection.</p>
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
            <div className="mt-3 flex flex-wrap items-center gap-2 border-y border-slate-800/80 py-2 text-[11px]">
                <span className="font-medium uppercase tracking-[0.12em] text-slate-500">Jump to chain</span>
                {model.chains.map((chain) => <button key={chain.authAsymId} type="button" onClick={() => onSelectResidue(model.residues[chain.start])} className="rounded-md border border-slate-700 bg-slate-900 px-2.5 py-1 text-slate-300 hover:border-cyan-500/60 hover:text-cyan-200">{chain.authAsymId} <span className="text-slate-500">{chain.end - chain.start}</span></button>)}
                <span className="ml-auto font-mono text-slate-500">{model.residues.length.toLocaleString()} residues · {model.cells.length.toLocaleString()} exact slots</span>
            </div>
            <div ref={wrapperRef} className="mt-3 w-full overflow-hidden rounded-xl border border-slate-700/80 bg-[#07101f] shadow-inner shadow-black/30">
                <canvas
                    ref={canvasRef}
                    role="img"
                    aria-label={`Complete FrustraMPNN heatmap showing ${model.residues.length} residues and ${model.cells.length} substitution slots`}
                    tabIndex={0}
                    className="block cursor-crosshair outline-none focus:ring-2 focus:ring-inset focus:ring-cyan-400/70"
                    onMouseMove={(event) => setHovered(locateCell(event.clientX, event.clientY))}
                    onMouseLeave={() => setHovered(null)}
                    onClick={(event) => {
                        const cell = locateCell(event.clientX, event.clientY);
                        if (cell) onSelectResidue(cell.residue);
                    }}
                    onKeyDown={(event) => {
                        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                        event.preventDefault();
                        const current = selectedIndex >= 0 ? selectedIndex : 0;
                        const next = event.key === 'Home' ? 0 : event.key === 'End' ? model.residues.length - 1 : event.key === 'ArrowLeft' ? Math.max(0, current - 1) : Math.min(model.residues.length - 1, current + 1);
                        onSelectResidue(model.residues[next]);
                    }}
                />
            </div>
            <div aria-live="polite" className="mt-2 min-h-12 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs text-slate-300">
                {hovered ? <div className="flex flex-wrap items-center gap-x-4 gap-y-1"><span className="font-mono font-semibold text-cyan-200">{identityLabel(hovered.residue)}</span><span>WT <b>{hovered.residue.wt}</b> → <b>{hovered.mutationAa}</b>{hovered.isNative ? ' · native slot' : ''}</span><span>Score <b>{hovered.score == null ? 'unavailable' : hovered.score.toFixed(3)}</b></span><span className="capitalize">Class <b>{hovered.className ?? hovered.status}</b></span>{hovered.reason && <span className="text-amber-200">{hovered.reason}</span>}</div> : selectedIndex >= 0 ? <div><span className="font-semibold text-cyan-200">Selected {identityLabel(model.residues[selectedIndex])}</span><span className="ml-3 text-slate-400">Use ←/→ while the map is focused to inspect adjacent residues.</span></div> : <div><span className="font-medium text-slate-200">Hover</span> for exact mutation evidence · <span className="font-medium text-slate-200">click</span> to select · <span className="font-medium text-slate-200">focus + arrow keys</span> to move residue-by-residue.</div>}
            </div>
        </section>
    );
}
