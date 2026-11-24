import { useState } from 'react';
import type { BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import { deckStations } from '../lib/bioxpWorkflowDeck';
import type { WorkflowDeckPlan, WorkflowMaterial } from '../lib/bioxpWorkflowPlan';
import { addSheetToTray, ensureLabware, guessMapping, parseSampleSheet, placeSampleSheet, placeSheetByWell, wellOrder, type SampleSheetMapping } from '../lib/bioxpSampleSheet';
import type { FilledWells } from '../lib/bioxpWellPaint';

const STRIPS = ['LOC_STRIP1', 'LOC_STRIP2', 'LOC_STRIP3', 'LOC_STRIP4'];
type Placement = 'strips' | 'selection' | 'well' | 'tray';

/** Import a CSV/TSV sample sheet: choose which column means what, then place rows in order. */
/** `sheetStation` is the plate sheet the importer sits in; it adds by-well and tray placement. */
export function BioXpSampleSheetImport({ plan, onChange, selection, sheetStation, filled = 'replace' }: { plan: WorkflowDeckPlan; onChange: (plan: WorkflowDeckPlan) => void; selection: BioXpDeckSelection; sheetStation?: string; filled?: FilledWells }) {
    const [sheet, setSheet] = useState<{ headers: string[]; rows: string[][]; file: string } | null>(null);
    const [mapping, setMapping] = useState<SampleSheetMapping>({ name: 0 });
    const [placement, setPlacement] = useState<Placement>('strips');
    const [firstStrip, setFirstStrip] = useState(0);
    const [notice, setNotice] = useState('');
    const [pasted, setPasted] = useState('');
    const read = (text: string, name: string) => {
        const parsed = parseSampleSheet(text); const guess = guessMapping(parsed.headers);
        setSheet({ ...parsed, file: name }); setMapping(guess); setNotice('');
        if (guess.well !== undefined) setPlacement('well'); else if (sheetStation) setPlacement('tray');
    };
    const load = async (file: File) => read(await file.text(), file.name);
    const stationName = (s: string) => deckStations.find(d => d.id === s)?.label ?? s;
    const column = (label: string, key: keyof SampleSheetMapping, required = false) => sheet && <label>{label}<select aria-label={`${label} column`} value={mapping[key] === undefined ? '' : String(mapping[key])} onChange={e => setMapping({ ...mapping, [key]: e.target.value === '' ? undefined : Number(e.target.value) })}>
        {!required && <option value="">Not used</option>}{sheet.headers.map((h, i) => <option key={i} value={i}>{h || `Column ${i + 1}`}</option>)}</select></label>;
    const stripsNeeded = sheet ? Math.ceil(sheet.rows.length / 8) : 0;
    const apply = () => {
        if (!sheet) return;
        let next = plan; let targets;
        if (placement === 'well') {
            const result = placeSheetByWell(plan, sheet, mapping, sheetStation ?? selection.station, filled, stationName);
            onChange(result.plan);
            setNotice(`Placed ${result.placed} of ${sheet.rows.length} rows in their wells (${result.added} new materials).${result.unplaced.length ? ` No usable well for: ${result.unplaced.join(', ')}.` : ''}`);
            return;
        }
        if (placement === 'tray') {
            const result = addSheetToTray(plan, sheet, mapping);
            onChange(result.plan);
            setNotice(`${sheet.rows.length} rows are in the tray (${result.added} new). Select them and drag across wells.`);
            return;
        }
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
        <label>Or paste rows from a spreadsheet, with the header row<textarea aria-label="Paste sample rows" rows={3} value={pasted} onChange={e => setPasted(e.target.value)} /></label>
        <button type="button" disabled={!pasted.trim()} onClick={() => read(pasted, 'Pasted rows')}>Use pasted rows</button>
        {sheet && <>
            <small>{sheet.file}: {sheet.rows.length} rows, {sheet.headers.length} columns. Other columns are kept in each sample&apos;s description.</small>
            <div className="bioxp-plan-columns">
                {column('Sample name', 'name', true)}{column('Barcode', 'barcode')}{column('Concentration', 'concentration')}{column('Volume (µL)', 'volume')}{column('Well', 'well')}
                <label>Material kind<select aria-label="Imported material kind" value={mapping.kind ?? 'sample'} onChange={e => setMapping({ ...mapping, kind: e.target.value as WorkflowMaterial['kind'] })}><option value="sample">Sample</option><option value="reagent">Reagent</option></select></label>
            </div>
            <label>Place rows<select aria-label="Sample placement" value={placement} onChange={e => setPlacement(e.target.value as Placement)}>
                {mapping.well !== undefined && <option value="well">In the well named in each row</option>}
                {sheetStation && <option value="tray">Into the tray, to drag onto wells</option>}
                <option value="strips">In order across 8-well strips (8 per strip)</option>
                <option value="selection">Into the wells selected on the deck</option></select></label>
            {placement === 'strips' && <label>First strip<select aria-label="First strip" value={firstStrip} onChange={e => setFirstStrip(Number(e.target.value))}>{STRIPS.map((s, i) => <option key={s} value={i}>Strip {i + 1}</option>)}</select></label>}
            {placement === 'strips' && <small>Needs {stripsNeeded} strip{stripsNeeded === 1 ? '' : 's'}{firstStrip + stripsNeeded > STRIPS.length ? `; only ${STRIPS.length - firstStrip} available from here, the rest stay unplaced` : ''}.</small>}
            {placement === 'well' && <small>{(sheetStation ?? selection.station).startsWith('LOC_STRIP') ? 'On strips the column number is the strip: B3 is strip 3, position B.' : `Rows go to ${stationName(sheetStation ?? selection.station)}. A1 and A01 both work.`} Occupied wells follow the Filled wells setting.</small>}
            {placement === 'selection' && <small>{selection.wells.length ? `${selection.wells.length} wells selected at ${deckStations.find(d => d.id === selection.station)?.label ?? selection.station}; filled column by column.` : 'Drag across wells on the deck to select them.'}</small>}
            <ol aria-label="Sample sheet preview">{sheet.rows.slice(0, 6).map((r, i) => <li key={i}>{mapping.well !== undefined && r[mapping.well] ? `${r[mapping.well]} · ` : ''}{r[mapping.name] || `Row ${i + 1}`}{mapping.barcode !== undefined && r[mapping.barcode] ? ` · ${r[mapping.barcode]}` : ''}</li>)}{sheet.rows.length > 6 && <li>… {sheet.rows.length - 6} more</li>}</ol>
            <button type="button" disabled={placement === 'selection' && !selection.wells.length || placement === 'well' && mapping.well === undefined} onClick={apply}>{placement === 'well' ? `Place ${sheet.rows.length} rows in their wells` : placement === 'tray' ? `Add ${sheet.rows.length} rows to the tray` : `Add ${sheet.rows.length} samples to the deck`}</button>
        </>}
        {notice && <p role="status">{notice}</p>}
    </fieldset>;
}
export default BioXpSampleSheetImport;
