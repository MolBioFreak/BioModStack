import React, { useMemo, useRef, useState } from 'react';
import { BioXpWellGrid } from './BioXpWellGrid';
import { BioXpSampleSheetImport } from './BioXpSampleSheetImport';
import { ensureLabware } from '../lib/bioxpSampleSheet';
import { deckStations } from '../lib/bioxpWorkflowDeck';
import { PLATE_ROWS, cellWell, dropMaterial, eraseSlots, materialTag, paintMaterials, type FilledWells, type GridCell, type PaintSlot } from '../lib/bioxpWellPaint';
import type { WorkflowAssignment, WorkflowDeckPlan, WorkflowMaterial } from '../lib/bioxpWorkflowPlan';

export const STRIP_STATIONS = ['LOC_STRIP1', 'LOC_STRIP2', 'LOC_STRIP3', 'LOC_STRIP4'];
const PLATE_COLUMNS = Array.from({ length: 12 }, (_, i) => String(i + 1));
const ROWS = PLATE_ROWS.split('');
const MATERIAL = 'application/x-bms-material';
const label = (station: string) => deckStations.find(s => s.id === station)?.label ?? station;
const snapshot = (plan: WorkflowDeckPlan) => JSON.stringify([plan.labware, plan.materials, plan.assignments]);

export type BioXpPlateSheetProps = {
    plan: WorkflowDeckPlan;
    onChange: (plan: WorkflowDeckPlan) => void;
    /** Station clicked on the deck; a strip opens the four-strip sheet. */
    station: string;
    /** Where each labware sits on the page being shown. Labware missing here uses its own station. */
    stationOf?: (labwareId: string) => string;
    /** Preferred labware when a station shows more than one. */
    labwareId?: string;
    /** Wells selected on the deck, outlined on the sheet. */
    selectedWells?: string[];
};

/** Enlarged plate map with a material tray: drag to paint, drop rows, import a sheet. Writes deck_plan only. */
export function BioXpPlateSheet({ plan, onChange, station, stationOf, labwareId, selectedWells = [] }: BioXpPlateSheetProps) {
    const strips = STRIP_STATIONS.includes(station);
    const stations = strips ? STRIP_STATIONS : [station];
    const where = (id: string) => stationOf?.(id) || plan.labware.find(l => l.id === id)?.station || '';
    const labwareAt = (s: string) => { const here = plan.labware.filter(l => where(l.id) === s); return here.find(l => l.id === labwareId) ?? here[0]; };
    const sheetLabware = stations.map(labwareAt);
    const sheetIds = sheetLabware.flatMap(l => l ? [l.id] : []);
    const columns = strips ? STRIP_STATIONS.map((_, i) => `Strip ${i + 1}`) : PLATE_COLUMNS;
    const slotStation = (cell: GridCell) => strips ? STRIP_STATIONS[cell.column] : station;
    const slotWell = (cell: GridCell) => strips ? `${ROWS[cell.row]}1` : cellWell(cell);
    const materials = useMemo(() => new Map(plan.materials.map(m => [m.id, m])), [plan.materials]);
    const at = (cell: GridCell): WorkflowAssignment[] => {
        const l = sheetLabware[strips ? cell.column : 0];
        return l ? plan.assignments.filter(a => a.labware_id === l.id && a.well === slotWell(cell)) : [];
    };

    const [tool, setTool] = useState<'paint' | 'erase'>('paint');
    const [filled, setFilled] = useState<FilledWells>('replace');
    const [volume, setVolume] = useState('');
    const [search, setSearch] = useState('');
    const [selected, setSelected] = useState<string[]>([]);
    const anchor = useRef<string | null>(null);
    const [hover, setHover] = useState<GridCell | null>(null);
    const [history, setHistory] = useState<{ before: WorkflowDeckPlan; after: string }[]>([]);
    const [notice, setNotice] = useState('');
    const canUndo = history.length > 0 && history[history.length - 1].after === snapshot(plan);

    const commit = (next: WorkflowDeckPlan) => {
        if (snapshot(next) === snapshot(plan)) return;
        setHistory(h => [...(h.length && h[h.length - 1].after === snapshot(plan) ? h : []).slice(-49), { before: plan, after: snapshot(next) }]);
        onChange(next);
    };
    const undo = () => {
        const last = history[history.length - 1];
        if (!last || last.after !== snapshot(plan)) return;
        setHistory(h => h.slice(0, -1)); onChange(last.before); setNotice('Undone.');
    };
    /** Slots for the cells, creating labware for any station that has none yet. */
    const slotsFor = (base: WorkflowDeckPlan, cells: GridCell[]) => {
        const missing = [...new Set(cells.map(slotStation))].filter(s => !labwareAt(s));
        const made = missing.length ? ensureLabware(base, missing.map(s => ({ station: s, name: label(s) }))) : { plan: base, ids: [] as string[] };
        const idFor = (s: string) => labwareAt(s)?.id ?? made.ids[missing.indexOf(s)];
        return { plan: made.plan, slots: cells.map(c => ({ labware_id: idFor(slotStation(c)), well: slotWell(c) })) as PaintSlot[] };
    };
    const amount = () => { const v = volume.trim(); return v === '' ? null : v; };

    const stroke = (cells: GridCell[]) => {
        setNotice('');
        if (tool === 'erase') {
            const slots = cells.flatMap(c => { const l = sheetLabware[strips ? c.column : 0]; return l ? [{ labware_id: l.id, well: slotWell(c) }] : []; });
            commit(eraseSlots(plan, slots)); return;
        }
        if (!selected.length) { setNotice('Pick a material in the tray first.'); return; }
        const { plan: base, slots } = slotsFor(plan, cells);
        const result = paintMaterials(base, slots, selected, amount(), filled);
        commit(result.plan);
        if (selected.length > 1) {
            const left = selected.slice(result.used);
            setSelected(left);
            setNotice(left.length ? `${result.used} placed · ${left.length} still selected` : `${result.used} placed`);
        }
    };
    const drop = (cell: GridCell, event: React.DragEvent) => {
        const id = event.dataTransfer.getData(MATERIAL);
        if (!materials.has(id)) return;
        const { plan: base, slots } = slotsFor(plan, [cell]);
        const sheet = [...new Set([...sheetIds, slots[0].labware_id])];
        commit(dropMaterial(base, id, slots[0], sheet, amount(), filled));
        setNotice('');
    };

    const placedHere = (m: WorkflowMaterial) => plan.assignments.filter(a => a.material_id === m.id && sheetIds.includes(a.labware_id));
    const placeName = (a: WorkflowAssignment) => { const s = where(a.labware_id); return strips && STRIP_STATIONS.includes(s) ? `${a.well[0]}${STRIP_STATIONS.indexOf(s) + 1}` : a.well; };
    const query = search.trim().toLowerCase();
    const tray = plan.materials.filter(m => !query || `${m.name} ${m.barcode ?? ''} ${m.description}`.toLowerCase().includes(query));
    const ordered = [...tray.filter(m => placedHere(m).length), ...tray.filter(m => !placedHere(m).length)];
    const pick = (id: string, event: React.MouseEvent) => {
        const ids = ordered.map(m => m.id);
        if (event.shiftKey && anchor.current && ids.includes(anchor.current)) {
            const [a, b] = [ids.indexOf(anchor.current), ids.indexOf(id)].sort((x, y) => x - y);
            setSelected(ids.slice(a, b + 1));
        } else if (event.ctrlKey || event.metaKey) {
            setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]); anchor.current = id;
        } else { setSelected(s => s.length === 1 && s[0] === id ? [] : [id]); anchor.current = id; }
        setTool('paint');
    };

    const hovered = hover ? at(hover) : [];
    const readout = hover ? `${strips ? `${ROWS[hover.row]}${hover.column + 1}` : cellWell(hover)} · ${hovered.length ? hovered.map(a => `${materials.get(a.material_id)?.name || 'Unnamed'}${a.volume_ul !== null && a.volume_ul !== '' ? ` · ${a.volume_ul} µL` : ''}`).join('; ') : 'empty'}`
        : `${stations.reduce((n, _s, i) => n + (sheetLabware[i] ? new Set(plan.assignments.filter(a => a.labware_id === sheetLabware[i]!.id).map(a => a.well)).size : 0), 0)} of ${strips ? 32 : 96} wells filled${sheetLabware[0]?.name ? ` · ${strips ? 'Reagent strips' : sheetLabware[0].name}` : ''}`;
    const multi = tool === 'paint' && selected.length > 1;

    return <section className="bps" aria-label="Plate sheet" tabIndex={-1}
        onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === 'z' && !(e.target as Element).closest('input,textarea,select')) { e.preventDefault(); e.stopPropagation(); undo(); } }}>
        <div>
            <BioXpWellGrid label={strips ? 'Reagent strips wells' : `${label(station)} wells`} rows={ROWS} columns={columns} size={strips ? 'wide' : 'large'} numbered={multi}
                onStroke={stroke} onHover={setHover} onDropCell={drop}
                cell={cell => {
                    const here = at(cell), first = materials.get(here[0]?.material_id ?? '');
                    const name = strips ? `${ROWS[cell.row]}${cell.column + 1}` : cellWell(cell);
                    const tag = here.length ? `${materialTag(first)}${here.length > 1 ? `+${here.length - 1}` : ''}` : '';
                    const tail = first ? (String(first.name).split(' ').slice(1).join(' ') || String(first.name)) : '';
                    return { on: here.length > 0, ariaLabel: `Well ${name}`, title: here.length ? here.map(a => materials.get(a.material_id)?.name || 'Unnamed').join('; ') : name,
                        className: selectedWells.includes(slotWell(cell)) && (!strips || cell.column === STRIP_STATIONS.indexOf(station)) ? 'is-selected-well' : undefined,
                        label: strips && here.length ? <><span className="bwg-tag">{tag}</span><span className="bwg-name">{tail}</span></> : tag };
                }} />
            <div className="bps-read" aria-live="polite">{readout}</div>
            <div className="bps-hint">{tool === 'erase' ? 'Drag across wells to clear them.' : multi ? 'Rows land one per well, in the order the wells are crossed. Leftover rows stay selected.'
                : `Drag across wells to paint · Shift-drag for a rectangle · click ${strips ? 'a row letter or strip name' : 'a row letter or column number'} to fill the line · drop a row from the tray on a well`}</div>
        </div>
        <div className="bps-tray">
            <div className="bps-bar">
                <button type="button" aria-pressed={tool === 'paint'} onClick={() => setTool('paint')}>{selected.length > 1 ? 'Place rows' : 'Paint'}</button>
                <button type="button" aria-pressed={tool === 'erase'} onClick={() => setTool('erase')}>Erase</button>
                <span className="bps-grow" />
                <button type="button" disabled={!canUndo} onClick={undo} title="Undo the last stroke, drop or import (Ctrl+Z)">Undo</button>
            </div>
            <div className="bps-bar"><span className="bps-muted">Filled wells</span>
                <span className="bps-seg" role="group" aria-label="Filled wells">
                    <button type="button" aria-pressed={filled === 'replace'} onClick={() => setFilled('replace')} title="Replace what is in the well">Replace</button>
                    <button type="button" aria-pressed={filled === 'keep'} onClick={() => setFilled('keep')} title="Leave what is there and add alongside">Keep both</button>
                </span>
                <span className="bps-grow" /><span className="bps-muted">{selected.length ? `${selected.length} selected` : `${plan.materials.length} materials`}</span>
            </div>
            <div className="bps-bar">
                <input className="bps-search" aria-label="Search materials" placeholder={`Search ${plan.materials.length} materials`} value={search} onChange={e => setSearch(e.target.value)} />
                <span className="bps-amount"><input className="bps-ul" aria-label="Amount per well (µL)" inputMode="decimal" placeholder="0" value={volume} onChange={e => setVolume(e.target.value)} />µL</span>
            </div>
            <div className="bps-list" role="listbox" aria-label="Materials" aria-multiselectable="true">
                {ordered.length === 0 && <span className="bps-muted">{plan.materials.length ? 'No match.' : 'No materials yet. Import a sheet or add one below.'}</span>}
                {ordered.map(m => {
                    const placed = placedHere(m);
                    return <div key={m.id} role="option" aria-selected={selected.includes(m.id)} tabIndex={0} draggable
                        className={`bps-item${selected.includes(m.id) ? ' is-selected' : ''}${placed.length ? ' is-placed' : ''}`}
                        title={`${m.name || 'Unnamed'}${m.barcode ? ` · ${m.barcode}` : ''}`}
                        onClick={e => pick(m.id, e)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(m.id, e as unknown as React.MouseEvent); } }}
                        onDragStart={e => { e.dataTransfer.setData(MATERIAL, m.id); e.dataTransfer.effectAllowed = 'copyMove'; }}>
                        <span className="bps-item-tag">{materialTag(m)}</span>
                        <span className="bps-item-name">{m.name || `Unnamed ${m.kind}`}</span>
                        <span className="bps-item-where">{placed.length ? `${placeName(placed[0])}${placed.length > 1 ? ` +${placed.length - 1}` : ''}` : ''}</span>
                    </div>;
                })}
            </div>
            {notice && <p role="status" className="bps-muted">{notice}</p>}
            <details className="bps-import">
                <summary>Import rows · CSV or paste</summary>
                <BioXpSampleSheetImport plan={plan} onChange={next => { commit(next); }} selection={{ station, wells: selectedWells }} sheetStation={station} filled={filled} />
            </details>
        </div>
    </section>;
}
