import { useState } from 'react';
import type { BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import { deckStations } from '../lib/bioxpWorkflowDeck';
import type { WorkflowDeckPlan, WorkflowMaterial } from '../lib/bioxpWorkflowPlan';
import { ensureLabware, guessMapping, parseSampleSheet, placeSampleSheet, wellOrder, type SampleSheetMapping } from '../lib/bioxpSampleSheet';

const STRIPS = ['LOC_STRIP1', 'LOC_STRIP2', 'LOC_STRIP3', 'LOC_STRIP4'];
type Placement = 'strips' | 'selection';

/** Import a CSV/TSV sample sheet: choose which column means what, then place rows in order. */
export function BioXpSampleSheetImport({ plan, onChange, selection }: { plan: WorkflowDeckPlan; onChange: (plan: WorkflowDeckPlan) => void; selection: BioXpDeckSelection }) {
    const [sheet, setSheet] = useState<{ headers: string[]; rows: string[][]; file: string } | null>(null);
    const [mapping, setMapping] = useState<SampleSheetMapping>({ name: 0 });
    const [placement, setPlacement] = useState<Placement>('strips');
    const [firstStrip, setFirstStrip] = useState(0);
    const [notice, setNotice] = useState('');
    const load = async (file: File) => {
        const parsed = parseSampleSheet(await file.text());
        setSheet({ ...parsed, file: file.name }); setMapping(guessMapping(parsed.headers)); setNotice('');
    };
    const column = (label: string, key: keyof SampleSheetMapping, required = false) => sheet && <label>{label}<select aria-label={`${label} column`} value={mapping[key] === undefined ? '' : String(mapping[key])} onChange={e => setMapping({ ...mapping, [key]: e.target.value === '' ? undefined : Number(e.target.value) })}>
        {!required && <option value="">Not used</option>}{sheet.headers.map((h, i) => <option key={i} value={i}>{h || `Column ${i + 1}`}</option>)}</select></label>;
    const stripsNeeded = sheet ? Math.ceil(sheet.rows.length / 8) : 0;
    const apply = () => {
        if (!sheet) return;
        let next = plan; let targets;
        if (placement === 'strips') {
            const stations = STRIPS.slice(firstStrip, firstStrip + stripsNeeded).map(s => ({ station: s, name: deckStations.find(d => d.id === s)?.label ?? s }));
            const made = ensureLabware(next, stations); next = made.plan;
            targets = made.ids.map(id => ({ labware_id: id, wells: wellOrder(1) }));
        } else {
            const labware = next.labware.find(l => l.station === selection.station);
            const made = labware ? { plan: next, ids: [labware.id] } : ensureLabware(next, [{ station: selection.station, name: deckStations.find(d => d.id === selection.station)?.label ?? selection.station }]);
            next = made.plan;
            // Selection wells in column order (A1, B1 … H1, A2 …), as plate maps are usually read.
            const order = wellOrder(12); const wells = [...selection.wells].sort((a, b) => order.indexOf(a) - order.indexOf(b));
            targets = [{ labware_id: made.ids[0], wells }];
        }
        const result = placeSampleSheet(next, sheet, mapping, targets);
        onChange(result.plan);
        setNotice(`Assigned ${result.placed} of ${sheet.rows.length} rows (${result.added} new, ${result.reused} already assigned).${result.unplaced.length ? ` No well left for: ${result.unplaced.join(', ')}.` : ''}${result.conflicts.length ? ` Existing contents kept; not placed: ${result.conflicts.map(c => `${c.name} at ${next.labware.find(l => l.id === c.labware_id)?.name || c.labware_id} ${c.well}`).join(', ')}. These samples remain available for manual assignment.` : ''}`);
    };
    return <fieldset aria-label="Import sample sheet"><legend>Import samples from CSV</legend>
        <label>Sample sheet (CSV or TSV)<input aria-label="Sample sheet file" type="file" accept=".csv,.tsv,.txt,text/csv" onChange={e => { const f = e.target.files?.[0]; if (f) void load(f); }} /></label>
        {sheet && <>
            <small>{sheet.file}: {sheet.rows.length} rows, {sheet.headers.length} columns. Other columns are kept in each sample&apos;s description.</small>
            <div className="bioxp-plan-columns">
                {column('Sample name', 'name', true)}{column('Barcode', 'barcode')}{column('Concentration', 'concentration')}{column('Volume (µL)', 'volume')}
                <label>Material kind<select aria-label="Imported material kind" value={mapping.kind ?? 'sample'} onChange={e => setMapping({ ...mapping, kind: e.target.value as WorkflowMaterial['kind'] })}><option value="sample">Sample</option><option value="reagent">Reagent</option></select></label>
            </div>
            <label>Place rows<select aria-label="Sample placement" value={placement} onChange={e => setPlacement(e.target.value as Placement)}>
                <option value="strips">In order across 8-well strips (8 per strip)</option>
                <option value="selection">Into the wells selected on the deck</option></select></label>
            {placement === 'strips' && <label>First strip<select aria-label="First strip" value={firstStrip} onChange={e => setFirstStrip(Number(e.target.value))}>{STRIPS.map((s, i) => <option key={s} value={i}>Strip {i + 1}</option>)}</select></label>}
            {placement === 'strips' && <small>Needs {stripsNeeded} strip{stripsNeeded === 1 ? '' : 's'}{firstStrip + stripsNeeded > STRIPS.length ? `; only ${STRIPS.length - firstStrip} available from here, the rest stay unplaced` : ''}.</small>}
            {placement === 'selection' && <small>{selection.wells.length ? `${selection.wells.length} wells selected at ${deckStations.find(d => d.id === selection.station)?.label ?? selection.station}; filled column by column.` : 'Drag across wells on the deck to select them.'}</small>}
            <ol aria-label="Sample sheet preview">{sheet.rows.slice(0, 6).map((r, i) => <li key={i}>{r[mapping.name] || `Row ${i + 1}`}{mapping.barcode !== undefined && r[mapping.barcode] ? ` · ${r[mapping.barcode]}` : ''}</li>)}{sheet.rows.length > 6 && <li>… {sheet.rows.length - 6} more</li>}</ol>
            <button type="button" disabled={placement === 'selection' && !selection.wells.length} onClick={apply}>Add {sheet.rows.length} samples to the deck</button>
        </>}
        {notice && <p role="status">{notice}</p>}
    </fieldset>;
}
export default BioXpSampleSheetImport;
