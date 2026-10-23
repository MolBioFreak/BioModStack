import React, { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it } from 'vitest';
import { MolecularDynamicsNativeSettings, nativeSectionKeys } from '../src/components/MolecularDynamicsNativeSettings';
import { serializeMolecularDynamicsNativeDraft, type MolecularDynamicsNativeDraft } from '../src/components/molecularDynamicsUiState';
import catalog from '../../../scripts/bms_md/native_parameters.json';

it('mounts every active catalog row with its native type, preserving unknown keys through a typed edit and cold reopen', async () => {
    const mdp = Object.fromEntries(catalog.parameters.filter(row => !row.historical_only).map(row => [row.key,
        row.choices.length ? row.choices[0] : row.type === 'number-list' ? ['0', '1'].join(row.list_separator) : row.type === 'number' ? '0' : '']));
    Object.assign(mdp, { 'pull-ncoords': '1', 'pull-ngroups': '1', 'Unknown_New-Key': 'exact', 'gen-seed': '-1' });
    let latest: MolecularDynamicsNativeDraft = { schema_version: 'bms.md.launch-intent.v2', name: 'inventory', input: { kind: 'prepared' },
        stages: [{ name: 'authored', mdp }], replicas: 1, random_seed: 42,
        execution: { ntmpi: 1, ntomp: 1, gpu_offload: 'none', pin: 'off' }, execution_target_id: null, return_to: '/designs/source', analysis: null };
    function Harness() { const [value, setValue] = useState(latest); return <MolecularDynamicsNativeSettings value={value} onChange={next => { latest = next; setValue(next); }} />; }
    const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
    try {
        await act(async () => root.render(<Harness />));
        for (const row of catalog.parameters.filter(row => !row.historical_only)) {
            const label = row.type.endsWith('-list') ? `${row.key} 1` : row.key;
            const control = host.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`);
            expect(control, row.key).not.toBeNull();
            if (row.type === 'number' || row.type === 'number-list') expect(control?.getAttribute('type'), row.key).toBe('number');
            if (row.type === 'enum' || row.type === 'enum-list') expect(control?.tagName, row.key).toBe('SELECT');
        }
        const input = host.querySelector<HTMLInputElement>('[aria-label="nstlist"]')!;
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '25');
            input.dispatchEvent(new Event('input', { bubbles: true }));
        });
        const reopened = JSON.parse(JSON.stringify(serializeMolecularDynamicsNativeDraft(latest)));
        expect(reopened.stages[0].mdp).toEqual({ ...mdp, nstlist: '25' });
        expect(reopened.return_to).toBe('/designs/source'); expect(reopened.execution_target_id).toBeNull();
        expect(reopened.analysis).toBeNull();
    } finally { await act(async () => root.unmount()); host.remove(); }
});

it('expands indexed AWH dimensions, rotation and ion groups and preserves imported higher indices', () => {
    expect(nativeSectionKeys(['awh1-dim1-start', 'awh1-ndim'], { 'awh-nbias': 2, 'awh2-ndim': 3, 'awh5-dim2-start': 0 })).toContain('awh2-dim3-start');
    expect(nativeSectionKeys(['awh1-dim1-start'], { 'awh5-dim2-start': 0 })).toContain('awh5-dim2-start');
    expect(nativeSectionKeys(['rot-rate0'], { 'rot-ngroups': 2 })).toEqual(['rot-rate0', 'rot-rate1']);
    expect(nativeSectionKeys(['iontype0-name'], { iontypes: 2 })).toEqual(['iontype0-name', 'iontype1-name']);
});
