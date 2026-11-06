import type { WorkflowDeckPlan, WorkflowLabware, WorkflowMaterial } from './bioxpWorkflowPlan';

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
export type SampleSheetMapping = { name: number; barcode?: number; concentration?: number; volume?: number; kind?: WorkflowMaterial['kind'] };

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
    };
}

/** Wells in fill order: one column (A..H) per strip, or column-wise across a plate. */
export function wellOrder(columns: number): string[] {
    const wells: string[] = [];
    for (let c = 1; c <= columns; c++) for (const r of 'ABCDEFGH') wells.push(`${r}${c}`);
    return wells;
}

export type SampleTarget = { labware_id: string; wells: string[] };

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
    const fields = ['name', 'kind', 'description', 'concentration', 'concentration_unit', 'barcode'] as const;
    let added = 0; let reused = 0;
    sheet.rows.forEach((row, index) => {
        const name = row[mapping.name] || `Row ${index + 1}`;
        const roles = new Set([mapping.name, mapping.concentration, mapping.volume].filter((v): v is number => v !== undefined));
        const barcode = mapping.barcode !== undefined ? row[mapping.barcode] : '';
        const description = sheet.headers.map((h, i) => roles.has(i) || !row[i] ? '' : `${h}: ${row[i]}`).filter(Boolean).join('; ');
        const conc = mapping.concentration !== undefined ? row[mapping.concentration] : '';
        const values: Omit<WorkflowMaterial, 'id'> = { name, kind: mapping.kind ?? 'sample', description,
            ...(conc ? { concentration: conc, concentration_unit: /ng\/?u?l/i.test(sheet.headers[mapping.concentration!]) ? 'ng/µL' : '' } : {}),
            ...(barcode ? { barcode } : {}) };
        const matching = materials.filter(m => fields.every(key => m[key] === values[key]));
        const slot = slots[index];
        const volume = mapping.volume !== undefined ? row[mapping.volume] : '';
        const amount = volume || null;
        const occupied = slot ? assignments.filter(a => a.labware_id === slot.labware_id && a.well === slot.well) : [];
        if (occupied.some(a => matching.some(m => m.id === a.material_id) && a.volume_ul === amount)) {
            reused++; return;
        }
        // Keep an unplaced row available for ordinary manual assignment, too.
        let material = matching[0];
        if (!material) { material = { id: newId(), ...values }; materials.push(material); }
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
