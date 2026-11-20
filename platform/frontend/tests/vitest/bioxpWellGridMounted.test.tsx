import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpTipGrid } from '../../src/components/BioXpWellGrid';
import { BioXpPlateSheet } from '../../src/components/BioXpPlateSheet';
import { BioXpMethodStartingDeck } from '../../src/components/BioXpMethodStartingDeck';
import { BioXpSampleSheetImport } from '../../src/components/BioXpSampleSheetImport';
import { dropMaterial } from '../../src/lib/bioxpWellPaint';
import { parseSampleSheet, guessMapping, placeSheetByWell } from '../../src/lib/bioxpSampleSheet';
import type { WorkflowDeckPlan } from '../../src/lib/bioxpWorkflowPlan';
import { api } from '../../src/lib/api';

vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn() } }));
let host: HTMLDivElement, root: Root;
beforeEach(() => { vi.mocked(api.get).mockReset(); host = document.createElement('div'); document.body.append(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

const ALL = 'ABCDEFGH'.split('').flatMap(r => Array.from({ length: 12 }, (_, c) => `${r}${c + 1}`));
const cell = (label: string, scope: ParentNode = host) => scope.querySelector(`[aria-label="${label}"]`) as HTMLElement;
const fire = (node: EventTarget, type: string, init: MouseEventInit = {}) => node.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, ...init }));
/** Press on the first cell, one move event per later cell, release. */
async function drag(labels: string[], init: MouseEventInit = {}, scope: ParentNode = host) {
    await act(async () => { fire(cell(labels[0], scope), 'pointerdown', init); });
    for (const label of labels.slice(1)) await act(async () => { fire(cell(label, scope), 'pointermove', init); });
    await act(async () => { fire(document, 'pointerup'); });
}
const button = (text: string) => [...host.querySelectorAll('button')].find(b => b.textContent === text) as HTMLButtonElement;
async function type(node: HTMLInputElement | HTMLTextAreaElement, value: string) {
    await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value')!.set!.call(node, value); node.dispatchEvent(new Event('input', { bubbles: true })); });
}

function Tips({ initial, seen }: { initial: string[]; seen: (w: string[]) => void }) {
    const [wells, setWells] = useState(initial); seen(wells);
    return <BioXpTipGrid label="Tip rack 1 wells" wells={wells} onChange={setWells} />;
}

it('C01 a drag paints every well crossed, even from one move event', async () => {
    let latest: string[] = [];
    await act(async () => root.render(<Tips initial={[]} seen={w => { latest = w; }} />));
    await drag(['Tip A1', 'Tip A12']);
    expect(latest).toEqual(Array.from({ length: 12 }, (_, i) => `A${i + 1}`));
});

it('C03 the press decides add or remove, and the count follows', async () => {
    let latest: string[] = [];
    await act(async () => root.render(<Tips initial={ALL} seen={w => { latest = w; }} />));
    await drag(['Tip A1', 'Tip D1']);
    expect(latest).toHaveLength(92);
    expect(host.textContent).toContain('92 of 96 tips');
    await drag(['Tip A1', 'Tip B1']);
    expect(latest).toHaveLength(94);
    expect(latest).toContain('A1'); expect(latest).not.toContain('C1');
    await act(async () => button('Undo').click());
    expect(latest).toHaveLength(92);
});

const plateWith = (materials: WorkflowDeckPlan['materials'], assignments: WorkflowDeckPlan['assignments'] = []): WorkflowDeckPlan =>
    ({ labware: [{ id: 'oc', station: 'LOC_OC', name: 'Output plate', profile_id: '' }], materials, assignments });
function Sheet({ initial, station = 'LOC_OC', seen }: { initial: WorkflowDeckPlan; station?: string; seen: (p: WorkflowDeckPlan) => void }) {
    const [plan, setPlan] = useState(initial); seen(plan);
    return <BioXpPlateSheet plan={plan} onChange={setPlan} station={station} />;
}
const option = (name: string) => [...host.querySelectorAll('[role="option"]')].find(o => o.querySelector('.bps-item-name')?.textContent === name) as HTMLElement;
const wellsOf = (plan: WorkflowDeckPlan, material?: string) => plan.assignments.filter(a => !material || a.material_id === material).map(a => a.well).sort();

it('C02 Shift-drag paints the rectangle, and a column header paints the column', async () => {
    let latest = plateWith([]);
    await act(async () => root.render(<Sheet initial={plateWith([{ id: 'm1', name: 'Buffer', kind: 'reagent', description: '' }])} seen={p => { latest = p; }} />));
    await act(async () => option('Buffer').click());
    await drag(['Well B2', 'Well D5'], { shiftKey: true });
    expect(wellsOf(latest)).toEqual(['B2', 'B3', 'B4', 'B5', 'C2', 'C3', 'C4', 'C5', 'D2', 'D3', 'D4', 'D5'].sort());
    await act(async () => button('7').click());
    expect(wellsOf(latest).filter(w => w.endsWith('7') && w.length === 2)).toHaveLength(8);
});

it('C07 C13 paints an amount down a column, replaces or keeps, erases and undoes', async () => {
    let latest = plateWith([]);
    const materials = [{ id: 'm1', name: 'Beads', kind: 'reagent' as const, description: '' }, { id: 'm2', name: 'Water', kind: 'reagent' as const, description: '' }];
    await act(async () => root.render(<Sheet initial={plateWith(materials)} seen={p => { latest = p; }} />));
    await act(async () => option('Beads').click());
    await type(host.querySelector('[aria-label="Amount per well (µL)"]')!, '5');
    await drag(['Well A1', 'Well H1']);
    expect(latest.assignments).toHaveLength(8);
    expect(latest.assignments.every(a => a.material_id === 'm1' && a.volume_ul === '5')).toBe(true);
    await act(async () => button('Undo').click());
    expect(latest.assignments).toHaveLength(0);
    await drag(['Well A1', 'Well H1']);
    await act(async () => option('Water').click());
    await drag(['Well A1']);
    expect(latest.assignments.filter(a => a.well === 'A1').map(a => a.material_id)).toEqual(['m2']);
    await act(async () => button('Keep both').click());
    await act(async () => option('Beads').click());
    await drag(['Well A1']);
    expect(latest.assignments.filter(a => a.well === 'A1').map(a => a.material_id).sort()).toEqual(['m1', 'm2']);
    await act(async () => button('Erase').click());
    await drag(['Well A1', 'Well D1']);
    expect(wellsOf(latest)).toEqual(['E1', 'F1', 'G1', 'H1']);
    expect(Object.keys(latest).sort()).toEqual(['assignments', 'labware', 'materials']);
});

it('C08 a well shows the barcode and the readout shows name and amount', async () => {
    const plan = plateWith([{ id: 'm', name: '999-020 PL931', kind: 'sample', description: '', barcode: 'RB07' }],
        [{ id: 'a', labware_id: 'oc', well: 'C3', material_id: 'm', volume_ul: '9' }]);
    await act(async () => root.render(<Sheet initial={plan} seen={() => {}} />));
    expect(cell('Well C3').textContent).toBe('RB07');
    await act(async () => cell('Well C3').focus());
    expect(host.querySelector('.bps-read')!.textContent).toBe('C3 · 999-020 PL931 · 9 µL');
});

const stripPlan = (): WorkflowDeckPlan => ({ labware: [], assignments: [],
    materials: Array.from({ length: 24 }, (_, i) => ({ id: `s${i + 1}`, name: `Sample ${i + 1}`, kind: 'sample' as const, description: '', barcode: `RB${String(i + 1).padStart(2, '0')}` })) });
const at = (plan: WorkflowDeckPlan, station: string, well: string) => plan.assignments.filter(a => plan.labware.find(l => l.id === a.labware_id)?.station === station && a.well === well).map(a => a.material_id);

it('C10 selected rows land one per well in the order crossed, leftovers stay selected', async () => {
    let latest = stripPlan();
    await act(async () => root.render(<Sheet initial={stripPlan()} station="LOC_STRIP1" seen={p => { latest = p; }} />));
    await act(async () => option('Sample 1').click());
    await act(async () => option('Sample 24').dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true })));
    expect(host.textContent).toContain('24 selected');
    const column = (n: number) => 'ABCDEFGH'.split('').map(r => `Well ${r}${n}`);
    await drag(column(1)); expect(host.textContent).toContain('16 still selected');
    await drag(column(2)); await drag(column(3));
    expect(at(latest, 'LOC_STRIP1', 'A1')).toEqual(['s1']); expect(at(latest, 'LOC_STRIP1', 'H1')).toEqual(['s8']);
    expect(at(latest, 'LOC_STRIP2', 'A1')).toEqual(['s9']); expect(at(latest, 'LOC_STRIP3', 'H1')).toEqual(['s24']);
    expect(latest.labware.map(l => l.station)).toEqual(['LOC_STRIP1', 'LOC_STRIP2', 'LOC_STRIP3']);
    expect(latest.assignments).toHaveLength(24);
});

it('C11 dropping a placed row on another well moves it', () => {
    let plan = plateWith([{ id: 'm', name: 'S', kind: 'sample', description: '' }]);
    plan = dropMaterial(plan, 'm', { labware_id: 'oc', well: 'C4' }, ['oc'], null, 'replace');
    plan = dropMaterial(plan, 'm', { labware_id: 'oc', well: 'C5' }, ['oc'], null, 'replace');
    expect(plan.assignments.map(a => a.well)).toEqual(['C5']);
});

it('C09 rows go to their named wells; on strips the column number is the strip', () => {
    const rows = Array.from({ length: 24 }, (_, i) => ({ well: `${'ABCDEFGH'[i % 8]}${Math.floor(i / 8) + 1}`, name: `S${i + 1}` })).reverse();
    const sheet = parseSampleSheet(['Well,Sample,Barcode', ...rows.map((r, i) => `${i % 2 ? r.well : r.well.replace(/(\d)$/, '0$1')},${r.name},RB${r.name.slice(1)}`)].join('\n'));
    const mapping = guessMapping(sheet.headers);
    expect(mapping.well).toBe(0);
    const out = placeSheetByWell({ labware: [], materials: [], assignments: [] }, sheet, mapping, 'LOC_STRIP1', 'replace');
    expect(out).toMatchObject({ placed: 24, unplaced: [] });
    const name = (station: string, well: string) => at(out.plan, station, well).map(id => out.plan.materials.find(m => m.id === id)!.name);
    expect(name('LOC_STRIP1', 'A1')).toEqual(['S1']); expect(name('LOC_STRIP3', 'B1')).toEqual(['S18']); expect(name('LOC_STRIP3', 'H1')).toEqual(['S24']);
    const plate = placeSheetByWell(plateWith([]), parseSampleSheet('Sample,Well\nX,b07\nY,Z9'), { name: 0, well: 1 }, 'LOC_OC', 'replace');
    expect(plate.plan.assignments.map(a => [a.labware_id, a.well])).toEqual([['oc', 'B7']]);
    expect(plate.unplaced).toEqual(['Y']);
});

it('C12 rows pasted from a spreadsheet go into the tray', async () => {
    let latest: WorkflowDeckPlan = { labware: [], materials: [], assignments: [] };
    function Controlled() { const [plan, setPlan] = useState(latest); latest = plan; return <BioXpSampleSheetImport plan={plan} onChange={setPlan} selection={{ station: 'LOC_OC', wells: [] }} sheetStation="LOC_OC" />; }
    await act(async () => root.render(<Controlled />));
    await type(host.querySelector('[aria-label="Paste sample rows"]')!, 'Sample\tBarcode\nAlpha\tRB01\nBeta\tRB02\nGamma\tRB03');
    await act(async () => button('Use pasted rows').click());
    await act(async () => button('Add 3 rows to the tray').click());
    expect(latest.materials.map(m => [m.name, m.barcode])).toEqual([['Alpha', 'RB01'], ['Beta', 'RB02'], ['Gamma', 'RB03']]);
    expect(latest.assignments).toEqual([]);
});

it('C05 a workflow rack is painted directly and can copy the robot record', async () => {
    let latest: WorkflowDeckPlan = { labware: [], materials: [], assignments: [], tip_racks: [{ tray: 2, tips: 'full' }] };
    function Controlled() { const [plan, setPlan] = useState(latest); latest = plan; return <BioXpMethodStartingDeck plan={plan} station="TECANRACK2" wells={[]} onChange={setPlan} generation={4} />; }
    await act(async () => root.render(<Controlled />));
    await act(async () => button('1').click());
    expect(latest.tip_racks).toEqual([{ tray: 2, tips: { available: ALL.filter(w => !/^[A-H]1$/.test(w)) } }]);
    const occupancy = ALL.map((_, i) => i < 40);
    vi.mocked(api.get).mockResolvedValue({ data: { trays: [{ tray: 2, occupancy }] } } as never);
    await act(async () => button("Copy the robot's record for this rack").click());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    expect(api.get).toHaveBeenCalledWith('/api/bioxp/operator-controls/tip-trays');
    expect(latest.tip_racks![0].tips).toEqual({ available: ALL.slice(0, 40) });
    expect(host.textContent).toContain("Copied the robot's record: 40 of 96 tips.");
});
