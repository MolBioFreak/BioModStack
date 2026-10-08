import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { spawnSync } from 'node:child_process';
import { expect, it } from 'vitest';
import { BioXpMethodPreview, BioXpMethodApplicationFields } from '../../src/components/BioXpMethodPreview';
import { methodStateAfter } from '../../src/lib/bioxpMethodSimulation';
import type { MethodCompile } from '../../src/lib/bioxpMethods';

it('renders real simulator occurrence deltas, moving labware, unknown contents and channel contact reset without final-state leakage', async () => {
    const initial = { labware: { plate: { station: 'LOC_OC' } }, vessels: { 'plate:A1': { volume_ul: '20', materials: ['sample'] }, 'plate:B1': { volume_ul: null, materials: null } }, channels: { '0': { contacts: ['old'] } } };
    const occurrences = [
        { occurrence_id: 'first', action: 'tip_pickup', inputs: { channels: [0] } },
        { occurrence_id: 'second', action: 'plate_move', inputs: { labware_id: 'plate', destination_station: 'LOC_TC' } },
    ];
    const produced = spawnSync(process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python', ['-c', 'import json,sys; from bioxp_method_simulation import simulate_method; x=json.load(sys.stdin); print(json.dumps(simulate_method(x["occurrences"], x["initial"])))'], { cwd: '../api', encoding: 'utf8', input: JSON.stringify({ initial, occurrences }) });
    expect(produced.status, produced.stderr).toBe(0);
    const simulation = JSON.parse(produced.stdout);
    const snapshot = JSON.stringify({ simulation, initial });
    expect(methodStateAfter(simulation, initial, 'missing')).toBeNull();
    expect(methodStateAfter(simulation, initial, 'first')).toMatchObject({ labware: { plate: { station: 'LOC_OC' } }, channels: { '0': { contacts: [] } } });
    const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
    try {
        await act(async () => root.render(<BioXpMethodPreview initialState={initial} result={{ document: null, issues: [], provenance: occurrences.map(row => ({ ...row, step_id: row.occurrence_id, native_action_ids: [] })), simulation } as unknown as MethodCompile} />));
        const state = host.querySelector('[aria-label="Planned state after occurrence"]')!;
        expect(state.textContent).toContain('LOC_OC'); expect(state.textContent).not.toContain('LOC_TC');
        expect(state.textContent).toContain('unknown');
        expect(host.querySelectorAll('[data-planned-vessel]')).toHaveLength(2);
        const marker = host.querySelector('[data-planned-vessel="plate:A1"] circle')!;
        const oldX = marker.getAttribute('cx');
        const selection = host.querySelector<HTMLSelectElement>('[aria-label="Preview occurrence"]')!;
        await act(async () => { selection.value = 'second'; selection.dispatchEvent(new Event('change', { bubbles: true })); });
        expect(state.textContent).toContain('LOC_TC'); expect(state.textContent).not.toContain('LOC_OC');
        expect(marker.getAttribute('cx')).not.toBe(oldX);
        await act(async () => { selection.value = 'first'; selection.dispatchEvent(new Event('change', { bubbles: true })); });
        expect(state.textContent).toContain('LOC_OC');
        expect(JSON.stringify({ simulation, initial })).toBe(snapshot);
    } finally { await act(async () => root.unmount()); host.remove(); }
});

it('renders individual real resolver fields rather than swallowing them at the requested/resolved envelope', async () => {
    const produced = spawnSync(process.env.BMS_TEST_PYTHON ?? '../api/.venv/bin/python', ['-c', 'import json; from bioxp_method_liquids import resolve_liquid_settings; print(json.dumps(resolve_liquid_settings({"aspirate_speed_ul_s": None, "carry_air_ul": "1.250"}, context={})))'], { cwd: '../api', encoding: 'utf8' });
    expect(produced.status, produced.stderr).toBe(0);
    const value = JSON.parse(produced.stdout);
    const host = document.createElement('div'); const root = createRoot(host);
    try {
        await act(async () => root.render(<BioXpMethodApplicationFields value={value} />));
        expect(host.querySelectorAll('tbody tr')).toHaveLength(2);
        expect(host.textContent).toContain('/fields/aspirate_speed_ul_s');
        expect(host.textContent).toContain('1.250'); expect(host.textContent).toContain('not_emitted');
        expect(host.textContent).toContain('unknown');
    } finally { await act(async () => root.unmount()); }
});
