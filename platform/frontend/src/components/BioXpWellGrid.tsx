import React, { useEffect, useRef, useState } from 'react';
import { cellsAlong, cellsInRectangle, type GridCell } from '../lib/bioxpWellPaint';
import './BioXpWellGrid.css';

export type WellGridCell = { label?: React.ReactNode; title?: string; on?: boolean; pressed?: boolean; className?: string; ariaLabel: string };
export type WellGridProps = {
    label: string;
    rows: string[];
    columns: string[];
    cell: (cell: GridCell) => WellGridCell;
    /** One call per stroke, with the cells in the order they were crossed. A row or column header click is a 'line'. */
    onStroke: (cells: GridCell[], start: GridCell, kind: 'drag' | 'line') => void;
    onHover?: (cell: GridCell | null) => void;
    onDropCell?: (cell: GridCell, event: React.DragEvent) => void;
    /** Number the cells of a stroke in progress (used when rows are placed one per well). */
    numbered?: boolean;
    size?: 'small' | 'large' | 'wide';
    disabled?: boolean;
};

const key = (c: GridCell) => `${c.row}:${c.column}`;

/** A press-and-drag well grid. It reports strokes; the owner decides what a stroke writes. */
export function BioXpWellGrid({ label, rows, columns, cell, onStroke, onHover, onDropCell, numbered = false, size = 'large', disabled = false }: WellGridProps) {
    const grid = useRef<HTMLDivElement>(null);
    const stroke = useRef<{ start: GridCell; last: GridCell; cells: GridCell[]; rectangle: boolean } | null>(null);
    const [pending, setPending] = useState<string[]>([]);
    const stop = useRef<() => void>(() => {});
    useEffect(() => () => stop.current(), []);

    const cellAt = (event: PointerEvent): GridCell | null => {
        const hit = (document.elementFromPoint?.(event.clientX, event.clientY) ?? event.target) as Element | null;
        const node = hit?.closest?.('[data-grid-cell]');
        if (!node || !grid.current?.contains(node)) return null;
        return { row: Number(node.getAttribute('data-row')), column: Number(node.getAttribute('data-column')) };
    };
    const begin = (event: React.PointerEvent, start: GridCell) => {
        if (disabled || event.button !== 0) return;
        event.preventDefault();
        const target = event.target as Element;
        if (target.hasPointerCapture?.(event.pointerId)) target.releasePointerCapture(event.pointerId);
        stroke.current = { start, last: start, cells: [start], rectangle: event.shiftKey };
        setPending([key(start)]);
        const move = (e: PointerEvent) => {
            const s = stroke.current, at = cellAt(e);
            if (!s || !at || key(at) === key(s.last)) return;
            if (s.rectangle) {
                // Column by column, as plate maps are read.
                s.cells = cellsInRectangle(s.start, at).sort((a, b) => a.column - b.column || a.row - b.row);
            } else {
                const seen = new Set(s.cells.map(key));
                for (const c of cellsAlong(s.last, at)) if (!seen.has(key(c))) { seen.add(key(c)); s.cells.push(c); }
            }
            s.last = at;
            setPending(s.cells.map(key));
        };
        const up = () => { const s = stroke.current; stop.current(); if (s) onStroke(s.cells, s.start, 'drag'); };
        const cancel = () => stop.current();
        stop.current = () => {
            document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); document.removeEventListener('pointercancel', cancel);
            stroke.current = null; setPending([]); stop.current = () => {};
        };
        document.addEventListener('pointermove', move); document.addEventListener('pointerup', up); document.addEventListener('pointercancel', cancel);
    };
    const whole = (cells: GridCell[], kind: 'drag' | 'line') => { if (!disabled && cells.length) onStroke(cells, cells[0], kind); };
    const order = new Map(pending.map((k, i) => [k, i + 1]));

    return <div ref={grid} role="group" aria-label={label} className={`bwg bwg-${size}`} style={{ gridTemplateColumns: `auto repeat(${columns.length}, var(--bwg-cell-w))` }}
        onPointerLeave={() => onHover?.(null)}>
        <span />
        {columns.map((name, column) => <button key={name} type="button" className="bwg-head" disabled={disabled} title={`Apply to ${name.length < 3 ? `column ${name}` : name}`}
            onClick={() => whole(rows.map((_, row) => ({ row, column })), 'line')}>{name}</button>)}
        {rows.map((name, row) => <React.Fragment key={name}>
            <button type="button" className="bwg-head" disabled={disabled} title={`Apply to row ${name}`}
                onClick={() => whole(columns.map((_, column) => ({ row, column })), 'line')}>{name}</button>
            {columns.map((_, column) => {
                const at = { row, column }, view = cell(at), n = order.get(key(at));
                return <button key={column} type="button" data-grid-cell="" data-row={row} data-column={column} disabled={disabled}
                    aria-label={view.ariaLabel} aria-pressed={view.pressed} title={view.title}
                    className={`bwg-cell${view.on ? ' is-on' : ''}${n ? ' is-pending' : ''}${view.className ? ` ${view.className}` : ''}`}
                    onPointerDown={e => begin(e, at)} onPointerEnter={() => onHover?.(at)} onFocus={() => onHover?.(at)}
                    // Keyboard and programmatic clicks (detail 0) apply to one well; mouse clicks arrive as strokes.
                    onClick={e => { if (e.detail === 0) whole([at], 'drag'); }}
                    onDragOver={onDropCell ? e => e.preventDefault() : undefined} onDrop={onDropCell ? e => { e.preventDefault(); onDropCell(at, e); } : undefined}>
                    {view.label}{numbered && n && pending.length > 1 ? <span className="bwg-order">{n}</span> : null}
                </button>;
            })}
        </React.Fragment>)}
    </div>;
}

const TIP_ROWS = 'ABCDEFGH'.split('');
const TIP_COLUMNS = Array.from({ length: 12 }, (_, i) => String(i + 1));
const allTips = TIP_ROWS.flatMap(r => TIP_COLUMNS.map(c => `${r}${c}`));

/**
 * Tip rack grid. A drag that starts on an empty position adds tips; one that starts on a tip removes them.
 * A row or column header fills the line, or clears it when it is already full. Wells stay in row-major order.
 */
export function BioXpTipGrid({ label, wells, onChange, disabled = false }: { label: string; wells: string[]; onChange: (next: string[]) => void; disabled?: boolean }) {
    const [history, setHistory] = useState<string[][]>([]);
    const present = new Set(wells);
    const stroke = (cells: GridCell[], start: GridCell, kind: 'drag' | 'line') => {
        const names = cells.map(c => `${TIP_ROWS[c.row]}${c.column + 1}`);
        const add = kind === 'line' ? !names.every(w => present.has(w)) : !present.has(`${TIP_ROWS[start.row]}${start.column + 1}`);
        const next = new Set(present);
        for (const w of names) if (add) next.add(w); else next.delete(w);
        const ordered = allTips.filter(w => next.has(w));
        if (ordered.length === wells.length && ordered.every(w => present.has(w))) return;
        setHistory(h => [...h.slice(-49), wells]);
        onChange(ordered);
    };
    return <div className="bwg-tips">
        <BioXpWellGrid label={label} rows={TIP_ROWS} columns={TIP_COLUMNS} size="small" disabled={disabled} onStroke={stroke}
            cell={c => { const w = `${TIP_ROWS[c.row]}${c.column + 1}`; return { ariaLabel: `Tip ${w}`, title: w, on: present.has(w), pressed: present.has(w) }; }} />
        <div className="bwg-foot"><b>{wells.length} of 96 tips</b><span>Drag to add or remove</span><span style={{ flex: 1 }} />
            <button type="button" disabled={!history.length} onClick={() => { const last = history[history.length - 1]; setHistory(h => h.slice(0, -1)); if (last) onChange(last); }}>Undo</button></div>
    </div>;
}
