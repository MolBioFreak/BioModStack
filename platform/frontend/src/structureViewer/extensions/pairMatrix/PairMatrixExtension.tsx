import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react';
import { assessResidueRef, canonicalResidueRefKey, type ResidueRef } from '../../contracts/structureIdentity.js';
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
        const dataset = 'dataset' in layer ? layer.dataset : undefined;
        if (dataset?.matrixDirection === 'directed') {
            const cells = new Map<string, typeof layer.values[number]>();
            const rows = dataset.rowAxis ?? [], columns = dataset.columnAxis ?? [];
            const unavailable = (reason: string) => ({ rows: [] as readonly ResidueRef[], columns: [] as readonly ResidueRef[], cells: new Map<string, typeof layer.values[number]>(), min: 0, max: 1, truncated: false, directed: true, reason });
            if (!rows.length || !columns.length || dataset.shape?.length !== 2 || dataset.shape[0] !== rows.length || dataset.shape[1] !== columns.length) return unavailable('declared axis shape mismatch');
            if (rows.length > MAX_AXIS || columns.length > MAX_AXIS) return unavailable(`declared axis exceeds ${MAX_AXIS}; provide a supported sampled projection`);
            if (dataset.documentIds.length !== 1 || !dataset.documentIds[0] || dataset.descriptorId !== layer.descriptor.id) return unavailable('matrix document or descriptor mismatch');
            const axisIndex = (axis: readonly ResidueRef[]) => {
                const index = new Map<string, number>();
                for (const [position, ref] of axis.entries()) {
                    if (!ref || ref.documentId !== dataset.documentIds[0] || assessResidueRef(ref).status !== 'ok') return null;
                    const key = canonicalResidueRefKey(ref);
                    if (index.has(key)) return null;
                    index.set(key, position);
                }
                return index;
            };
            const rowIndex = axisIndex(rows), columnIndex = axisIndex(columns);
            if (!rowIndex || !columnIndex) return unavailable('foreign, incomplete or duplicate axis identity');
            let min = Infinity, max = -Infinity;
            for (const entry of layer.values) {
                const pair = entry.identity as ResiduePairIdentity;
                if (!pair?.first || !pair?.second) return unavailable('missing cell identity');
                const row = rowIndex.get(canonicalResidueRefKey(pair.first)), column = columnIndex.get(canonicalResidueRefKey(pair.second));
                if (row === undefined || column === undefined) return unavailable('cell identity outside declared axes');
                const key = `${row}:${column}`;
                if (cells.has(key)) return unavailable('duplicate directed cell');
                if (typeof entry.value !== 'number' || !Number.isFinite(entry.value) || entry.value < 0 || entry.missingness !== undefined) return unavailable('invalid directed PAE value');
                cells.set(key, entry);
                min = Math.min(min, entry.value); max = Math.max(max, entry.value);
            }
            if (cells.size !== rows.length * columns.length) return unavailable('incomplete declared matrix');
            return { rows, columns, cells, min, max, truncated: false, directed: true, reason: null };
        }
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
        return { rows: axis, columns: axis, cells, min, max, truncated: residues.size > MAX_AXIS, directed: false, reason: null };
    }, [layer]);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas || matrix.rows.length === 0) return;
        const width = matrix.columns.length, height = matrix.rows.length;
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext('2d');
        if (!context) return;
        const image = context.createImageData(width, height);
        const span = matrix.max - matrix.min || 1;
        for (let row = 0; row < height; row += 1) for (let column = 0; column < width; column += 1) {
            const entry = matrix.cells.get(`${row}:${column}`);
            const offset = (row * width + column) * 4;
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
        const column = Math.min(matrix.columns.length - 1, Math.max(0, Math.floor((event.clientX - rect.left) / rect.width * matrix.columns.length)));
        const row = Math.min(matrix.rows.length - 1, Math.max(0, Math.floor((event.clientY - rect.top) / rect.height * matrix.rows.length)));
        setCursor([row, column]); select(row, column);
    };
    const keyboard = (event: KeyboardEvent<HTMLDivElement>) => {
        let [row, column] = cursor;
        if (event.key === 'ArrowUp') row -= 1; else if (event.key === 'ArrowDown') row += 1;
        else if (event.key === 'ArrowLeft') column -= 1; else if (event.key === 'ArrowRight') column += 1;
        else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(row, column); return; } else return;
        event.preventDefault();
        setCursor([Math.max(0, Math.min(matrix.rows.length - 1, row)), Math.max(0, Math.min(matrix.columns.length - 1, column))]);
    };
    const current = matrix.cells.get(`${cursor[0]}:${cursor[1]}`);

    if (matrix.reason) return <section aria-label={`${layer.descriptor.label} residue pair matrix`}><div role="status">{layer.descriptor.label} unavailable: {matrix.reason}.</div></section>;

    return <section className="space-y-2" aria-label={`${layer.descriptor.label} residue pair matrix`}>
        <div role="grid" tabIndex={0} onKeyDown={keyboard} aria-rowcount={matrix.rows.length} aria-colcount={matrix.columns.length} aria-label={`Matrix cursor row ${cursor[0] + 1}, column ${cursor[1] + 1}`} className="rounded border border-slate-700 p-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
            <canvas ref={canvasRef} onClick={click} className="h-auto w-full max-h-96 cursor-crosshair [image-rendering:pixelated]" role="img" aria-label={`${matrix.rows.length} by ${matrix.columns.length} heatmap; use arrow keys and Enter on the focused matrix`} />
        </div>
        <div aria-live="polite" className="text-xs text-slate-300">{matrix.rows[cursor[0]] ? label(matrix.rows[cursor[0]]!) : '—'} × {matrix.columns[cursor[1]] ? label(matrix.columns[cursor[1]]!) : '—'}: {current?.missingness ?? current?.value ?? 'unavailable'}</div>
        {matrix.truncated && <div role="status" className="text-xs text-amber-300">Axis bounded to {MAX_AXIS} residues; source ordering is retained and no aggregate value is fabricated.</div>}
        {matrix.directed && matrix.cells.size > 1000 && <div className="text-xs text-slate-400">Table lists the first 1000 directed cells; the heatmap and keyboard cursor expose the complete declared matrix.</div>}
        <details><summary className="cursor-pointer text-xs text-slate-400">Accessible value table</summary><div role="table" className="mt-1 max-h-48 overflow-auto text-xs">
            {[...matrix.cells.entries()].filter(([key]) => { const [r, c] = key.split(':').map(Number); return matrix.directed || r <= c; }).slice(0, 1000).map(([key, entry]) => {
                const [row, column] = key.split(':').map(Number); return <button key={key} role="row" className="grid w-full grid-cols-3 text-left hover:bg-blue-950" onClick={() => select(row!, column!)}><span>{label(matrix.rows[row!]!)}</span><span>{label(matrix.columns[column!]!)}</span><span>{entry.missingness ?? entry.value ?? '—'}</span></button>;
            })}
        </div></details>
    </section>;
}
