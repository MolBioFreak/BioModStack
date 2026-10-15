import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { ParamField } from '../../src/components/ModelParameterField';
import { NativeSetting } from '../../src/components/NativeBinderGeneration';
import { nativeParameterLabel } from '../../src/lib/nativeParameterLabel';
let root: Root;
afterEach(async () => { if (root) await act(async () => root.unmount()); document.body.replaceChildren(); });
it('humanizes native labels without changing numeric or boolean request keys', async () => {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    const patch = vi.fn();
    const parameter = { name: 'maturation_flow_enabled', label: 'maturation_flow_enabled', type: 'boolean', default: true };
    await act(async () => root.render(<NativeSetting parameter={parameter} values={{ maturation_flow_enabled: true }} onPatch={patch} chains={[]} />));
    expect(host.querySelector('label')?.textContent).toBe('Run PPIFlow maturation');
    expect(host.querySelector('details code')?.textContent).toBe('maturation_flow_enabled');
    await act(async () => host.querySelector<HTMLInputElement>('input')!.click());
    expect(patch).toHaveBeenCalledWith({ maturation_flow_enabled: false });
    const update = vi.fn();
    await act(async () => root.render(<ParamField param={{ name: 'ppiflow_start_t', label: 'ppiflow_start_t', type: 'number', default: 0.15 }} params={{ ppiflow_start_t: 0.37 }} updateParam={update} setShowFileBrowser={() => {}} setActiveSequenceField={() => {}} setShowSequenceManager={() => {}} ligandPresets={[]} />));
    expect(host.querySelector('label')?.textContent).toBe('PPIFlow starting time');
    expect(host.querySelector('details code')?.textContent).toBe('ppiflow_start_t');
    expect(host.querySelector<HTMLInputElement>('input[type=number]')?.value).toBe('0.37');
    expect(update).not.toHaveBeenCalled();
});
it('preserves authored labels and humanizes fallback identifiers', () => {
    expect(nativeParameterLabel({ name: 'ppiflow_start_t', label: 'Starting flow time (t)' })).toBe('Starting flow time (t)');
    expect(nativeParameterLabel({ name: 'target_chains' })).toBe('Target chains');
});
