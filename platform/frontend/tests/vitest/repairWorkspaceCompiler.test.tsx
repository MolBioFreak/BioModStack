import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { BioXpMethodPreview, nativeMethodActions } from '../../src/components/BioXpMethodPreview';
import { RepairWorkspaceClassEditor } from '../../src/components/repairWorkspaceClasses';
import { methodStateAfter } from '../../src/lib/bioxpMethodSimulation';
import { previewActionDestination } from '../../src/lib/bioxpWorkflowPlan';

it('receives actual compiler immutable seed, every prefix and native target envelopes', async () => {
    const path = process.env.REPAIR_WORKSPACE_COMPILER_OUTPUT;
    expect(path, 'Run repairWorkspaceCompilerProducer.py with the compiler lane first').toBeTruthy();
    const { request, result, expected_prefix_states, custody } = JSON.parse(readFileSync(path!, 'utf8'));
    const untouched = structuredClone(result);
    for (const [index, row] of result.resolved.occurrences.entries()) {
        const state = methodStateAfter(result.simulation, request.initial_state, row.occurrence_id)!;
        expect(state.vessels).toEqual(expected_prefix_states[index].vessels);
        expect(state.labware).toEqual(expected_prefix_states[index].labware);
    }
    const actions = nativeMethodActions(result.document);
    const transferIds = new Set(result.provenance.find((p: any) => p.step_id === 'transfer').native_action_ids);
    const targets = actions.filter(a => !transferIds.has(a.action_id)).map(a => previewActionDestination({ kind: String(a.kind), params: a.params as any, station: null, well: null }, custody.destinations));
    const transferMoves = actions.filter(a => transferIds.has(a.action_id) && (a.params as any).operation === 'move');
    expect(transferMoves.map(a => (a.params as any).location_id)).toEqual([2, 3]);
    expect(targets).toEqual([{ station: null, well: null }, { station: 'LOC_TC', well: null }, { station: 'LOC_TC', well: 'A1' }, { station: 'LOC_OC_COVER_STORAGE', well: null }, { station: 'LOC_PARK', well: null }]);
    const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
    try {
        await act(async () => root.render(<BioXpMethodPreview result={result} initialState={request.initial_state} custodyDestinations={custody.destinations} />));
        expect(host.querySelector('[aria-label="Planned state after occurrence"]')!.textContent).toContain('untouched');
        const select = host.querySelector<HTMLSelectElement>('[aria-label="Preview occurrence"]')!;
        for (const row of result.provenance) {
            await act(async () => { select.value = row.occurrence_id; select.dispatchEvent(new Event('change', { bubbles: true })); });
            if (row.step_id === 'carry') expect(host.textContent).toContain('Station: LOC_TC');
            if (row.step_id === 'park') expect(host.textContent).toContain('Station: LOC_PARK');
            expect(host.querySelector('[aria-label="Planned state after occurrence"]')!.textContent).toContain('untouched');
        }
    } finally { await act(async () => root.unmount()); host.remove(); }
    expect(result).toEqual(untouched);
    expect(request.initial_state).not.toHaveProperty('labware');
});
it('All 51 actual source entries retain every settings field and immutable source through lazy task disclosure', async () => {
    const { starters } = JSON.parse(readFileSync(process.env.REPAIR_WORKSPACE_COMPILER_OUTPUT!, 'utf8'));
    expect(starters).toHaveLength(51); const before = JSON.stringify(starters);
    const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
    try {
        for (const entry of starters) {
            await act(async () => root.render(<RepairWorkspaceClassEditor key={entry.id} value={entry} onChange={() => { throw new Error('Opening class details must not author values'); }} />));
            for (const title of ['Advanced class fields', 'Source, exact identity & provenance']) {
                const details = [...host.querySelectorAll('details')].find(d => d.querySelector('summary')?.textContent === title)!;
                await act(async () => { details.open = true; details.dispatchEvent(new Event('toggle')); });
            }
            for (const key of Object.keys(entry.settings)) expect(host.querySelector(`[aria-label="Liquid class.settings.${key} presence"]`), `${entry.id}:${key}`).toBeTruthy();
            expect(JSON.parse(host.querySelector('pre')!.textContent!).source).toEqual(entry.source);
        }
    } finally { await act(async () => root.unmount()); host.remove(); }
    expect(JSON.stringify(starters)).toBe(before);
});
it('legacy replay retains contacts append/reset and unknown target stays unknown', () => {
    const simulation = { after_occurrences: [{ occurrence_id: 'one', state_delta: { channels: { '0': { contacts_append: ['new'] } } } }, { occurrence_id: 'two', state_delta: { channels: { '0': { contacts_reset: true, contacts_append: ['fresh'] } } } }] };
    const initial = { channels: { '0': { contacts: ['old'], liquid: null } }, custody: { retained: null }, thermal_tasks: { untouched: false } };
    expect((methodStateAfter(simulation, initial, 'one')!.channels as any)['0'].contacts).toEqual(['old', 'new']);
    expect((methodStateAfter(simulation, initial, 'two')!.channels as any)['0'].contacts).toEqual(['fresh']);
    expect(methodStateAfter(simulation, initial, 'two')!.thermal_tasks).toEqual(initial.thermal_tasks);
    expect(previewActionDestination({ kind: 'plate_move', params: { target_location: 'unknown' }, station: 'LOC_TC', well: null }).station).toBeNull();
});
