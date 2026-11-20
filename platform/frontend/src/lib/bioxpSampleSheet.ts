import type { WorkflowDeckPlan, WorkflowLabware, WorkflowMaterial } from './bioxpWorkflowPlan';
import { normalizeWell, paintMaterials, type FilledWells } from './bioxpWellPaint';

/** Parse CSV/TSV text (quoted fields, CRLF) into a header row and data rows. */
export function parseSampleSheet(text: string): { headers: string[]; rows: string[][] } {
    const delimiter = (text.split(/\r?\n/, 1)[0].match(/\t/g)?.length ?? 0) > (text.split(/\r?\n/, 1)[0].match(/,/g)?.length ?? 0) ? '\t' : ',';
    const out: string[][] = []; let row: string[] = []; let field = ''; let quoted = false;
    for (let i = 0; i < text.length; i++) {
        const c = text[i];
        if (quoted) {
            if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
            else if (c === '"') quoted = false;
            else field += c;
        } else if (c === '"' && field === '') quoted = true;
        else if (c === delimiter) { row.push(field); field = ''; }
        else if (c === '\n' || c === '\r') {
            if (c === '\r' && text[i + 1] === '\n') i++;
            row.push(field); field = '';
            if (row.some(v => v.trim() !== '')) out.push(row);
            row = [];
        } else field += c;
    }
    row.push(field); if (row.some(v => v.trim() !== '')) out.push(row);
    const [headers = [], ...rows] = out;
    return { headers: headers.map(h => h.trim()), rows: rows.map(r => headers.map((_, i) => (r[i] ?? '').trim())) };
}

/** Column roles. Unmapped columns are kept in the description as "Header: value". */
export type SampleSheetMapping = { name: number; barcode?: number; concentration?: number; volume?: number; well?: number; kind?: WorkflowMaterial['kind'] };

/** Best-guess column roles from header names; the operator can change any of them. */
export function guessMapping(headers: string[]): SampleSheetMapping {
    const find = (re: RegExp) => { const i = headers.findIndex(h => re.test(h)); return i < 0 ? undefined : i; };
    const volume = headers.findIndex(header => {
        const h = header.replace(/[_-]/g, ' ').trim();
        // A denominator such as ng/uL is not a sample volume. Do not silently
        // convert other volume units either; explicit column selection remains available.
        if (/concentration|\bconc\b|[/⁄]|\b(?:ml|nl|pl)\b/i.test(h)) return false;
        return /\bvol(?:ume)?\b/i.test(h) || /^(?:[uµμ]l|microlit(?:er|re)s?)$/i.test(h)
            || /\bamount\b/i.test(h) && /(?:^|[^a-z])[uµμ]l(?:$|[^a-z])/i.test(h);
    });
    return {
        name: find(/^(sample|name|sample[ _]?(id|name)|id)$/i) ?? find(/sample|name/i) ?? 0,
        barcode: find(/barcode|^rb\d*$/i),
        concentration: find(/working.*conc|^conc/i) ?? find(/conc/i),
        volume: volume < 0 ? undefined : volume,
        well: find(/^(well|position|well[ _]?(id|position|name)|plate[ _]?well)$/i),
    };
}

/** Wells in fill order: one column (A..H) per strip, or column-wise across a plate. */
export function wellOrder(columns: number): string[] {
    const wells: string[] = [];
    for (let c = 1; c <= columns; c++) for (const r of 'ABCDEFGH') wells.push(`${r}${c}`);
    return wells;
}

export type SampleTarget = { labware_id: string; wells: string[] };

const MATERIAL_FIELDS = ['name', 'kind', 'description', 'concentration', 'concentration_unit', 'barcode'] as const;

/** The material a sheet row describes. Unmapped columns go into the description as "Header: value". */
export function rowMaterial(sheet: { headers: string[] }, mapping: SampleSheetMapping, row: string[], index: number): Omit<WorkflowMaterial, 'id'> {
    const name = row[mapping.name] || `Row ${index + 1}`;
    const roles = new Set([mapping.name, mapping.concentration, mapping.volume, mapping.well].filter((v): v is number => v !== undefined));
    const barcode = mapping.barcode !== undefined ? row[mapping.barcode] : '';
    const description = sheet.headers.map((h, i) => roles.has(i) || !row[i] ? '' : `${h}: ${row[i]}`).filter(Boolean).join('; ');
    const conc = mapping.concentration !== undefined ? row[mapping.concentration] : '';
    return { name, kind: mapping.kind ?? 'sample', description,
        ...(conc ? { concentration: conc, concentration_unit: /ng\/?u?l/i.test(sheet.headers[mapping.concentration!]) ? 'ng/µL' : '' } : {}),
        ...(barcode ? { barcode } : {}) };
}

/** Reuse an identical material already in the plan, or add one. */
function materialFor(materials: WorkflowMaterial[], values: Omit<WorkflowMaterial, 'id'>, newId: () => string): { material: WorkflowMaterial; added: boolean } {
    const match = materials.find(m => MATERIAL_FIELDS.every(key => m[key] === values[key]));
    if (match) return { material: match, added: false };
    const material = { id: newId(), ...values }; materials.push(material);
    return { material, added: true };
}

/** Add every row as a material without placing it; rows already in the plan are reused. */
export function addSheetToTray(plan: WorkflowDeckPlan, sheet: { headers: string[]; rows: string[][] }, mapping: SampleSheetMapping,
    newId: () => string = () => crypto.randomUUID()): { plan: WorkflowDeckPlan; ids: string[]; added: number } {
    const materials = [...plan.materials]; let added = 0;
    const ids = sheet.rows.map((row, index) => { const made = materialFor(materials, rowMaterial(sheet, mapping, row, index), newId); if (made.added) added++; return made.material.id; });
    return { plan: { ...plan, materials }, ids, added };
}

const STRIPS = ['LOC_STRIP1', 'LOC_STRIP2', 'LOC_STRIP3', 'LOC_STRIP4'];

/**
 * Place each row in the well named by its Well column. On a strip, the column number is the strip
 * number: B3 is strip 3, position B. Occupied wells follow `filled`.
 */
export function placeSheetByWell(plan: WorkflowDeckPlan, sheet: { headers: string[]; rows: string[][] }, mapping: SampleSheetMapping, station: string,
    filled: FilledWells, labwareName: (station: string) => string = s => s, newId: () => string = () => crypto.randomUUID()): {
        plan: WorkflowDeckPlan; placed: number; added: number; unplaced: string[];
    } {
    if (mapping.well === undefined) return { plan, placed: 0, added: 0, unplaced: sheet.rows.map((r, i) => r[mapping.name] || `Row ${i + 1}`) };
    const strip = STRIPS.includes(station);
    let next: WorkflowDeckPlan = { ...plan, materials: [...plan.materials] };
    const unplaced: string[] = []; let placed = 0; let added = 0;
    sheet.rows.forEach((row, index) => {
        const values = rowMaterial(sheet, mapping, row, index);
        const well = normalizeWell(row[mapping.well!] ?? '');
        const target = well && (strip ? (Number(well.slice(1)) <= 4 ? { station: STRIPS[Number(well.slice(1)) - 1], well: `${well[0]}1` } : null) : { station, well });
        if (!target) { unplaced.push(values.name); return; }
        const made = ensureLabware(next, [{ station: target.station, name: labwareName(target.station) }], newId); next = { ...made.plan, materials: [...made.plan.materials] };
        const material = materialFor(next.materials, values, newId); if (material.added) added++;
        const amount = mapping.volume !== undefined && row[mapping.volume] ? row[mapping.volume] : null;
        next = paintMaterials(next, [{ labware_id: made.ids[0], well: target.well }], [material.material.id], amount, filled, newId).plan;
        placed++;
    });
    return { plan: next, placed, added, unplaced };
}

/**
 * Place rows without replaying existing associations or overwriting occupied wells.
 * Matching materials keep their stable IDs across save/reopen and re-import.
 * Conflicts remain ordinary unplaced authoring data, never a Run prerequisite.
 */
export function placeSampleSheet(plan: WorkflowDeckPlan, sheet: { headers: string[]; rows: string[][] }, mapping: SampleSheetMapping,
    targets: SampleTarget[], newId: () => string = () => crypto.randomUUID()): {
        plan: WorkflowDeckPlan; placed: number; added: number; reused: number; unplaced: string[];
        conflicts: { name: string; labware_id: string; well: string }[];
    } {
    const slots = targets.flatMap(t => t.wells.map(well => ({ labware_id: t.labware_id, well })));
    const materials = [...plan.materials]; const assignments = [...plan.assignments]; const unplaced: string[] = [];
    const conflicts: { name: string; labware_id: string; well: string }[] = [];
    let added = 0; let reused = 0;
    sheet.rows.forEach((row, index) => {
        const values = rowMaterial(sheet, mapping, row, index); const name = values.name;
        const matching = materials.filter(m => MATERIAL_FIELDS.every(key => m[key] === values[key]));
        const slot = slots[index];
        const volume = mapping.volume !== undefined ? row[mapping.volume] : '';
        const amount = volume || null;
        const occupied = slot ? assignments.filter(a => a.labware_id === slot.labware_id && a.well === slot.well) : [];
        if (occupied.some(a => matching.some(m => m.id === a.material_id) && a.volume_ul === amount)) {
            reused++; return;
        }
        // Keep an unplaced row available for ordinary manual assignment, too.
        const material = matching[0] ?? materialFor(materials, values, newId).material;
        if (!slot) { unplaced.push(name); return; }
        if (occupied.length) { conflicts.push({ name, ...slot }); return; }
        assignments.push({ id: newId(), ...slot, material_id: material.id, volume_ul: amount });
        added++;
    });
    return { plan: { ...plan, materials, assignments }, placed: added + reused, added, reused, unplaced, conflicts };
}

/** Labware for each named station, creating it when the plan has none there. */
export function ensureLabware(plan: WorkflowDeckPlan, stations: { station: string; name: string }[], newId: () => string = () => crypto.randomUUID()): { plan: WorkflowDeckPlan; ids: string[] } {
    const labware: WorkflowLabware[] = [...plan.labware]; const ids: string[] = [];
    for (const s of stations) {
        let item = labware.find(l => l.station === s.station);
        if (!item) { item = { id: newId(), station: s.station, name: s.name, profile_id: '' }; labware.push(item); }
        ids.push(item.id);
    }
    return { plan: { ...plan, labware }, ids };
}
