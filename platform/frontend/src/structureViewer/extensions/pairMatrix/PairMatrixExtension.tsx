import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react';
import { canonicalResidueRefKey, type ResidueRef } from '../../contracts/structureIdentity.js';
import type { MetricLayer, MetricSelection, ResiduePairIdentity } from '../../metrics/metricContracts.js';

export interface PairMatrixExtensionProps {
    readonly layer: MetricLayer;
    readonly onSelection: (selection: MetricSelection) => void;
}

const MAX_AXIS = 512;
const label = (residue: ResidueRef): string => `${residue.authAsymId ?? residue.labelAsymId}:${residue.authSeqId ?? residue.labelSeqId}${residue.insertionCode ?? ''}`;

export function PairMatrixExtension({ layer, onSelection }: PairMatrixExtensionProps) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const [cursor, setCursor] = useState<readonly [number, number]>([0, 0]);
    const matrix = useMemo(() => {
        const residues = new Map<string, ResidueRef>();
        for (const entry of layer.values) {
            const pair = entry.identity as ResiduePairIdentity;
            residues.set(canonicalResidueRefKey(pair.first), pair.first);
            residues.set(canonicalResidueRefKey(pair.second), pair.second);
        }
        const axis = [...residues.values()].slice(0, MAX_AXIS);
        const index = new Map(axis.map((residue, position) => [canonicalResidueRefKey(residue), position]));
        const cells = new Map<string, typeof layer.values[number]>();
        for (const entry of layer.values) {
            const pair = entry.identity as ResiduePairIdentity;
            const row = index.get(canonicalResidueRefKey(pair.first));
            const column = index.get(canonicalResidueRefKey(pair.second));
            if (row === undefined || column === undefined) continue;
            cells.set(`${row}:${column}`, entry);
            cells.set(`${column}:${row}`, entry);
        }
        const finite = [...cells.values()].flatMap((entry) => typeof entry.value === 'number' && Number.isFinite(entry.value) ? [entry.value] : []);
        const min = finite.length ? Math.min(...finite) : 0;
        const max = finite.length ? Math.max(...finite) : 1;
        return { axis, cells, min, max, truncated: residues.size > MAX_AXIS };
    }, [layer]);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas || matrix.axis.length === 0) return;
        const size = matrix.axis.length;
        canvas.width = size;
        canvas.height = size;
        const context = canvas.getContext('2d');
        if (!context) return;
        const image = context.createImageData(size, size);
        const span = matrix.max - matrix.min || 1;
        for (let row = 0; row < size; row += 1) for (let column = 0; column < size; column += 1) {
            const entry = matrix.cells.get(`${row}:${column}`);
            const offset = (row * size + column) * 4;
            if (!entry || typeof entry.value !== 'number' || !Number.isFinite(entry.value)) {
                image.data.set([71, 85, 105, 255], offset);
            } else {
                const fraction = Math.max(0, Math.min(1, (entry.value - matrix.min) / span));
                image.data.set([Math.round(25 + 220 * fraction), Math.round(80 + 110 * (1 - fraction)), Math.round(220 - 190 * fraction), 255], offset);
            }
        }
        context.putImageData(image, 0, 0);
    }, [matrix]);

    const select = (row: number, column: number) => {
        const entry = matrix.cells.get(`${row}:${column}`);
        if (!entry) return;
        onSelection({ metricId: layer.descriptor.id, identities: [entry.identity as ResiduePairIdentity], origin: 'matrix' });
    };
    const click = (event: MouseEvent<HTMLCanvasElement>) => {
        const rect = event.currentTarget.getBoundingClientRect();
        const column = Math.min(matrix.axis.length - 1, Math.max(0, Math.floor((event.clientX - rect.left) / rect.width * matrix.axis.length)));
        const row = Math.min(matrix.axis.length - 1, Math.max(0, Math.floor((event.clientY - rect.top) / rect.height * matrix.axis.length)));
        setCursor([row, column]); select(row, column);
    };
    const keyboard = (event: KeyboardEvent<HTMLDivElement>) => {
        let [row, column] = cursor;
        if (event.key === 'ArrowUp') row -= 1; else if (event.key === 'ArrowDown') row += 1;
        else if (event.key === 'ArrowLeft') column -= 1; else if (event.key === 'ArrowRight') column += 1;
        else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(row, column); return; } else return;
        event.preventDefault();
        setCursor([Math.max(0, Math.min(matrix.axis.length - 1, row)), Math.max(0, Math.min(matrix.axis.length - 1, column))]);
    };
    const current = matrix.cells.get(`${cursor[0]}:${cursor[1]}`);

    return <section className="space-y-2" aria-label={`${layer.descriptor.label} residue pair matrix`}>
        <div role="grid" tabIndex={0} onKeyDown={keyboard} aria-rowcount={matrix.axis.length} aria-colcount={matrix.axis.length} aria-label={`Matrix cursor row ${cursor[0] + 1}, column ${cursor[1] + 1}`} className="rounded border border-slate-700 p-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
            <canvas ref={canvasRef} onClick={click} className="h-auto w-full max-h-96 cursor-crosshair [image-rendering:pixelated]" role="img" aria-label={`${matrix.axis.length} by ${matrix.axis.length} heatmap; use arrow keys and Enter on the focused matrix`} />
        </div>
        <div aria-live="polite" className="text-xs text-slate-300">{matrix.axis[cursor[0]] ? label(matrix.axis[cursor[0]]!) : '—'} × {matrix.axis[cursor[1]] ? label(matrix.axis[cursor[1]]!) : '—'}: {current?.missingness ?? current?.value ?? 'unavailable'}</div>
        {matrix.truncated && <div role="status" className="text-xs text-amber-300">Axis bounded to {MAX_AXIS} residues; source ordering is retained and no aggregate value is fabricated.</div>}
        <details><summary className="cursor-pointer text-xs text-slate-400">Accessible value table</summary><div role="table" className="mt-1 max-h-48 overflow-auto text-xs">
            {[...matrix.cells.entries()].filter(([key]) => { const [r, c] = key.split(':').map(Number); return r <= c; }).slice(0, 1000).map(([key, entry]) => {
                const [row, column] = key.split(':').map(Number); return <button key={key} role="row" className="grid w-full grid-cols-3 text-left hover:bg-blue-950" onClick={() => select(row!, column!)}><span>{label(matrix.axis[row!]!)}</span><span>{label(matrix.axis[column!]!)}</span><span>{entry.missingness ?? entry.value ?? '—'}</span></button>;
            })}
        </div></details>
    </section>;
}
