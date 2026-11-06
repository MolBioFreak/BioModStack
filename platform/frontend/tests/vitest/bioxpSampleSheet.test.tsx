import React, { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it } from 'vitest';
import { BioXpSampleSheetImport } from '../../src/components/BioXpSampleSheetImport';
import type { WorkflowDeckPlan } from '../../src/lib/bioxpWorkflowPlan';
import { guessMapping, parseSampleSheet, placeSampleSheet, ensureLabware, wellOrder } from '../../src/lib/bioxpSampleSheet';

const csv = 'Barcode,Sample,DNA Type,Working Concentration (ng/uL),Notes\r\n' +
    Array.from({ length: 24 }, (_, i) => `RB${String(i + 1).padStart(2, '0')},"S${i + 1}, plasmid",Plasmid,10,${i === 9 ? 'check id' : ''}`).join('\r\n');
const blank = (): WorkflowDeckPlan => ({ labware: [], materials: [], assignments: [] });

it('keeps concentration separate from unknown volume and places 24 rows in order', () => {
    const sheet = parseSampleSheet(csv);
    expect(sheet.headers[1]).toBe('Sample'); expect(sheet.rows).toHaveLength(24); expect(sheet.rows[0][1]).toBe('S1, plasmid');
    const mapping = guessMapping(sheet.headers);
    expect(mapping).toMatchObject({ name: 1, barcode: 0, concentration: 3, volume: undefined });
    let n = 0; const id = () => `id${n++}`;
    const made = ensureLabware(blank(), ['LOC_STRIP1', 'LOC_STRIP2', 'LOC_STRIP3'].map(s => ({ station: s, name: s })), id);
    const targets = made.ids.map(l => ({ labware_id: l, wells: wellOrder(1) }));
    const out = placeSampleSheet(made.plan, sheet, mapping, targets, id);
    expect(out).toMatchObject({ placed: 24, added: 24, reused: 0, unplaced: [], conflicts: [] });
    const at = (i: number) => { const a = out.plan.assignments[i]; return [out.plan.labware.find(l => l.id === a.labware_id)!.station, a.well, out.plan.materials.find(m => m.id === a.material_id)!.barcode]; };
    expect(at(0)).toEqual(['LOC_STRIP1', 'A1', 'RB01']); expect(at(7)).toEqual(['LOC_STRIP1', 'H1', 'RB08']);
    expect(at(8)).toEqual(['LOC_STRIP2', 'A1', 'RB09']); expect(at(23)).toEqual(['LOC_STRIP3', 'H1', 'RB24']);
    expect(out.plan.assignments.every(a => a.volume_ul === null)).toBe(true);
    expect(out.plan.materials[9].description).toBe('Barcode: RB10; DNA Type: Plasmid; Notes: check id');
    expect(out.plan.materials[0]).toMatchObject({ concentration: '10', concentration_unit: 'ng/µL' });
    const reopened = JSON.parse(JSON.stringify(out.plan));
    const again = placeSampleSheet(reopened, sheet, mapping, targets, () => { throw new Error('re-import allocated another identity'); });
    expect(again.plan).toEqual(reopened);
    expect(again).toMatchObject({ placed: 24, added: 0, reused: 24, conflicts: [] });
});

it('guesses only volume-labelled microlitre fields, leaving other units and ratios alone', () => {
    for (const header of ['Working Concentration (ng/uL)', 'Concentration (ng/µL)', 'ng/μL', 'Volume (mL)', 'Volume (nL)']) {
        expect(guessMapping(['Sample', header]).volume, header).toBeUndefined();
    }
    for (const header of ['Volume (uL)', 'sample_volume_ul', 'Vol (µL)', 'µL', 'Amount (uL)']) {
        expect(guessMapping(['Sample', 'Concentration (ng/uL)', header]).volume, header).toBe(2);
    }
    const sheet = parseSampleSheet('Sample\tConcentration (ng/uL)\tVolume (uL)\n001--sample\t10.00\t01.2500');
    const mapping = guessMapping(sheet.headers);
    const out = placeSampleSheet(blank(), sheet, mapping, [{ labware_id: 'strip', wells: ['A1'] }]);
    expect(out.plan.materials[0].name).toBe('001--sample');
    expect(out.plan.materials[0].concentration).toBe('10.00');
    expect(out.plan.assignments[0].volume_ul).toBe('01.2500');
});

it('retains occupied wells, reports conflicts and still places rows in free wells', () => {
    const plan: WorkflowDeckPlan = { ...blank(), materials: [{ id: 'old', name: 'Existing', kind: 'sample', description: '' }],
        assignments: [{ id: 'old-assignment', labware_id: 'strip', well: 'A1', material_id: 'old', volume_ul: '7.00' }] };
    const before = JSON.parse(JSON.stringify(plan));
    const sheet = parseSampleSheet('Sample,Volume (uL)\nNew,1.2500\nFree,');
    const targets = [{ labware_id: 'strip', wells: ['A1', 'B1'] }];
    const out = placeSampleSheet(plan, sheet, guessMapping(sheet.headers), targets);
    expect(out).toMatchObject({ placed: 1, added: 1, reused: 0, unplaced: [],
        conflicts: [{ name: 'New', labware_id: 'strip', well: 'A1' }] });
    expect(plan).toEqual(before);
    expect(out.plan.assignments[0]).toEqual(plan.assignments[0]);
    expect(out.plan.assignments).toHaveLength(2);
    expect(out.plan.assignments[1]).toMatchObject({ well: 'B1', volume_ul: null });
    const again = placeSampleSheet(JSON.parse(JSON.stringify(out.plan)), sheet, guessMapping(sheet.headers), targets);
    expect(again.plan).toEqual(out.plan);
    expect(again).toMatchObject({ added: 0, reused: 1, conflicts: out.conflicts });
});

it('does not duplicate overflow materials or overwrite an explicitly edited amount on re-import', () => {
    const sheet = parseSampleSheet(csv); const mapping = guessMapping(sheet.headers);
    const target = [{ labware_id: 'strip', wells: wellOrder(1) }];
    const first = placeSampleSheet(blank(), sheet, mapping, target);
    expect(first.placed).toBe(8); expect(first.unplaced).toHaveLength(16);
    const again = placeSampleSheet(first.plan, sheet, mapping, target);
    expect(again.plan).toEqual(first.plan);
    const edited = JSON.parse(JSON.stringify(first.plan)); edited.assignments[0].volume_ul = '2.000';
    const retained = placeSampleSheet(edited, sheet, mapping, target);
    expect(retained.plan.assignments).toEqual(edited.assignments);
    expect(retained.conflicts).toEqual([{ name: 'S1, plasmid', labware_id: 'strip', well: 'A1' }]);
});

it('the existing importer panel uses the corrected column guess and repeated Apply stays idempotent', async () => {
    let latest = blank();
    function Controlled() {
        const [plan, setPlan] = useState(blank); latest = plan;
        return <BioXpSampleSheetImport plan={plan} onChange={setPlan} selection={{ station: 'LOC_STRIP1', wells: [] }} />;
    }
    const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
    try {
        await act(async () => root.render(<Controlled />));
        const file = new File([csv], 'samples.csv', { type: 'text/csv' });
        Object.defineProperty(file, 'text', { value: async () => csv });
        const input = host.querySelector<HTMLInputElement>('input[type=file]')!;
        Object.defineProperty(input, 'files', { value: [file] });
        await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })));
        expect(host.querySelector<HTMLSelectElement>('[aria-label="Volume (µL) column"]')!.value).toBe('');
        const apply = () => [...host.querySelectorAll('button')].find(b => b.textContent === 'Add 24 samples to the deck')!;
        await act(async () => apply().click());
        const first = JSON.parse(JSON.stringify(latest));
        expect(first.assignments).toHaveLength(24);
        expect(first.assignments.every((a: { volume_ul: unknown }) => a.volume_ul === null)).toBe(true);
        await act(async () => apply().click());
        expect(latest).toEqual(first);
        expect(host.querySelector('[role=status]')!.textContent).toContain('0 new, 24 already assigned');
    } finally {
        await act(async () => root.unmount()); host.remove();
    }
});
