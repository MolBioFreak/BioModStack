import { expect, it } from 'vitest';
import { guessMapping, parseSampleSheet, placeSampleSheet, ensureLabware, wellOrder } from '../../src/lib/bioxpSampleSheet';

const csv = 'Barcode,Sample,DNA Type,Working Concentration (ng/uL),Notes\r\n' +
    Array.from({ length: 24 }, (_, i) => `RB${String(i + 1).padStart(2, '0')},"S${i + 1}, plasmid",Plasmid,10,${i === 9 ? 'check id' : ''}`).join('\r\n');

it('parses quoted CSV, guesses columns and places 24 samples 8 per strip in order', () => {
    const sheet = parseSampleSheet(csv);
    expect(sheet.headers[1]).toBe('Sample'); expect(sheet.rows).toHaveLength(24); expect(sheet.rows[0][1]).toBe('S1, plasmid');
    const mapping = guessMapping(sheet.headers);
    expect(mapping).toMatchObject({ name: 1, barcode: 0, concentration: 3 });
    let n = 0; const id = () => `id${n++}`;
    const made = ensureLabware({ labware: [], materials: [], assignments: [] }, ['LOC_STRIP1', 'LOC_STRIP2', 'LOC_STRIP3'].map(s => ({ station: s, name: s })), id);
    const out = placeSampleSheet(made.plan, sheet, mapping, made.ids.map(l => ({ labware_id: l, wells: wellOrder(1) })), id);
    expect(out.placed).toBe(24); expect(out.unplaced).toEqual([]);
    const at = (i: number) => { const a = out.plan.assignments[i]; return [out.plan.labware.find(l => l.id === a.labware_id)!.station, a.well, out.plan.materials.find(m => m.id === a.material_id)!.barcode]; };
    expect(at(0)).toEqual(['LOC_STRIP1', 'A1', 'RB01']); expect(at(7)).toEqual(['LOC_STRIP1', 'H1', 'RB08']);
    expect(at(8)).toEqual(['LOC_STRIP2', 'A1', 'RB09']); expect(at(23)).toEqual(['LOC_STRIP3', 'H1', 'RB24']);
    expect(out.plan.materials[9].description).toBe('DNA Type: Plasmid; Notes: check id');
    expect(out.plan.materials[0]).toMatchObject({ concentration: '10', concentration_unit: 'ng/µL' });
    const short = placeSampleSheet(made.plan, sheet, mapping, [{ labware_id: made.ids[0], wells: wellOrder(1) }], id);
    expect(short.placed).toBe(8); expect(short.unplaced).toHaveLength(16);
});
