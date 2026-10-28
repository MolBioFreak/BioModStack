import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { isolateDeNovoTransport } from './deNovoTransport';
vi.mock('../../src/components/EpitopeMolstarViewer', () => ({ default: () => <div data-testid="molecule" /> }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
vi.mock('../../src/lib/api', async original => ({ ...await original<object>(), fetchSystemStatus: vi.fn(async () => ({ data: { gpus: [] } })) }));
import ProteinLocalRedesignTemplate from '../../src/components/ProteinLocalRedesignTemplate';

it.each(['native_recall', 'partial_diffusion'])('transports %s source roles without a hidden task change', async mode => {
    const restore = isolateDeNovoTransport();
    const pdb = 'ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C  \nATOM      2  CA  GLY B   2       3.800   0.000   0.000  1.00 20.00           C  \nTER\nEND\n';
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, blob: async () => new Blob([pdb]), json: async () => ({ structures: [], cached: [] }) })));
    vi.stubGlobal('URL', Object.assign(URL, { revokeObjectURL: vi.fn(), createObjectURL: vi.fn(() => 'blob:legacy-test') }));
    if (!File.prototype.text) Object.defineProperty(File.prototype, 'text', { configurable: true, value() { return new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(this); }); } });
    const changed = vi.fn();
    const initial = { input_structure: 'retained/two.pdb', _source_prepared: { path: 'retained/two.pdb', format: 'pdb', sha256: 'a'.repeat(64), model_number: 1 },
        redesign_mode: mode, design_chains: ['A'], context_chains: ['B'], seed: 0,
        ...(mode === 'native_recall' ? { contig: 'A1,/0,B2', select_fixed_atoms: { A1: ['CA'] }, select_unfixed_sequence: 'B2', redesign_ranges: 'A1', partial_t: null }
            : { region_mode: 'interface_shell', interface_cutoff: 4, region_padding: 0 }) };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const container = document.createElement('div'); document.body.appendChild(container); const root = createRoot(container);
    try {
        await act(async () => root.render(<MemoryRouter><QueryClientProvider client={client}><ProteinLocalRedesignTemplate embedded onBack={vi.fn()} initialValues={initial} onDraftChange={changed} /></QueryClientProvider></MemoryRouter>));
        await vi.waitFor(async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); }); expect(changed.mock.lastCall?.[0].native_job_request).toBeTruthy(); });
        const params = changed.mock.lastCall![0].native_job_request.params;
        expect(params.design_chains).toEqual(['A']); expect(params.context_chains).toEqual(['B']);
        expect(params.seed).toBe(0);
        if (mode === 'native_recall') {
            expect(params.select_unfixed_sequence).toBe('B2');
            expect(params.select_fixed_atoms).toEqual({ A1: ['CA'] });
            expect(params.partial_t).toBeNull(); expect(params.sequence_policy).toBe('explicit_positions');
        } else {
            expect(params.region_mode).toBe('interface_shell'); expect(params.interface_cutoff).toBe(4); expect(params.region_padding).toBe(0);
            const sampling = [...container.querySelectorAll('button')].find(b => b.textContent === 'Sampling')!;
            await act(async () => sampling.click());
            const editNoise = async (text: string) => {
                const input = container.querySelector<HTMLInputElement>('[aria-label="Partial noise exact Å"]')!;
                expect(input).toBeTruthy();
                await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, text); input.dispatchEvent(new Event('input', { bubbles: true })); });
            };
            await editNoise('27.123456789');
            expect(changed.mock.lastCall![0].native_job_request.params.partial_t).toBe(27.123456789);
            await editNoise('');
            expect(changed.mock.lastCall![0].native_job_request.params.partial_t).toBe('');
            const saved = JSON.parse(JSON.stringify(changed.mock.lastCall![0]));
            await act(async () => root.render(<MemoryRouter><QueryClientProvider client={client}><ProteinLocalRedesignTemplate key="cold" embedded onBack={vi.fn()} initialValues={saved} onDraftChange={changed} /></QueryClientProvider></MemoryRouter>));
            await vi.waitFor(async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); }); expect(changed.mock.lastCall![0].native_job_request.params.partial_t).toBe(''); });
        }
    } finally { await act(async () => root.unmount()); client.clear(); container.remove(); vi.unstubAllGlobals(); restore(); }
});
