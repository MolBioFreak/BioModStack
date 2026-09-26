import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { CanceledError, type InternalAxiosRequestConfig } from 'axios';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { JobSubmission } from '../../src/components/JobSubmission';
import { api } from '../../src/lib/api';
const loaded = vi.hoisted(() => ({ oligo: vi.fn(), antibody: vi.fn(), prediction: vi.fn(), md: vi.fn(), modification: vi.fn(), mutagenesis: vi.fn() }));
// Count module evaluation, not just conditional render. Scientific editor
// round-trips are covered separately by their real mounted owner suites.
vi.mock('../../src/components/OligoDesignerTemplate', () => { loaded.oligo(); return { OligoDesignerTemplate: ({ onBack }: any) => <section>Selected oligo editor<button onClick={onBack}>Editor back</button></section> }; });
vi.mock('../../src/components/AntibodyDenovoTemplate', () => { loaded.antibody(); return { AntibodyDenovoTemplate: () => null }; });
vi.mock('../../src/components/StructurePredictionTemplate', () => { loaded.prediction(); return { StructurePredictionTemplate: () => null }; });
vi.mock('../../src/components/MolecularDynamicsTemplate', () => { loaded.md(); return { MolecularDynamicsTemplate: () => null }; });
vi.mock('../../src/components/ProteinModificationTemplate', () => { loaded.modification(); return { ProteinModificationTemplate: () => null }; });
vi.mock('../../src/components/MutagenesisTemplate', () => { loaded.mutagenesis(); return { MutagenesisTemplate: () => null }; });
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
let root: Root; let client: QueryClient; let host: HTMLDivElement; let requests: InternalAxiosRequestConfig[];
let failDetail: boolean; let hangDetail: boolean;
const oldAdapter = api.defaults.adapter;
const definition = { id: 'fixture', name: 'Fixture definition', category: 'fixture', modes: [{ id: 'edit', name: 'Edit', params: ['label', 'count', 'enabled'] }], params: [{ name: 'label', type: 'string', default: 'default label' }, { name: 'count', type: 'integer', default: 7 }, { name: 'enabled', type: 'boolean', default: true }] };
const compact = { ...definition, params: undefined, modes: definition.modes.map(({ params: _, ...mode }) => mode) };
function Location() { const location = useLocation(); return <output data-route>{location.pathname + location.search}</output>; }
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); }); }
async function until(check: () => void) { await vi.waitFor(async () => { await settle(); check(); }); }
async function mount(route: string) { await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}><Location /><JobSubmission /></MemoryRouter></QueryClientProvider>)); await settle(); }
const button = (label: string) => [...host.querySelectorAll('button')].find(b => b.textContent?.trim() === label)!;
beforeEach(() => {
    failDetail = false; hangDetail = false; requests = [];
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    api.defaults.adapter = async config => {
        requests.push(config); expect(config.method).toBe('get'); let data: unknown = [];
        if (config.url === '/api/models') { expect(config.params.compact).toBe(true); data = [compact]; }
        else if (config.url === '/api/models/fixture') {
            if (hangDetail) await new Promise((_resolve, reject) => config.signal!.addEventListener!('abort', () => reject(new CanceledError()), { once: true }));
            if (failDetail) throw new Error('fixture offline'); data = definition;
        } else if (config.url === '/api/gpu/status') data = { gpus: [] };
        else if (config.url === '/api/gpu/gpus') data = { gpus: [] };
        else if (config.url?.endsWith('/integration')) data = { workflows: {} };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = oldAdapter; localStorage.clear(); });
it('landing imports no unselected editors; selecting/back/reopening imports only the chosen module once', async () => {
    await mount('/submit'); expect(Object.values(loaded).every(fn => fn.mock.calls.length === 0)).toBe(true);
    expect(requests.filter(r => r.url === '/api/models/fixture')).toHaveLength(0);
    const choice = [...host.querySelectorAll('h3')].find(b => b.textContent === 'Oligo Designer')!;
    expect(choice).toBeTruthy(); await act(async () => choice.click());
    await until(() => expect(host.textContent).toContain('Selected oligo editor'));
    expect(loaded.oligo).toHaveBeenCalledTimes(1); for (const [name, fn] of Object.entries(loaded)) if (name !== 'oligo') expect(fn).not.toHaveBeenCalled();
    await act(async () => button('Editor back').click());
    await act(async () => [...host.querySelectorAll('h3')].find(b => b.textContent === 'Oligo Designer')!.click());
    await until(() => expect(host.textContent).toContain('Selected oligo editor')); expect(loaded.oligo).toHaveBeenCalledTimes(1);
});
it('manual deep link resolves full selected settings, reports failed read and retries without changing the route', async () => {
    failDetail = true; await mount('/submit?model=fixture&mode=edit&keep=value');
    await until(() => expect(host.textContent).toContain('Selected model settings unavailable'));
    expect(host.querySelector('[data-route]')!.textContent).toContain('model=fixture');
    failDetail = false; await act(async () => button('Retry model settings').click());
    await until(() => expect([...host.querySelectorAll('input')].some(input => input.value === 'default label')).toBe(true));
    expect(host.querySelector('[data-route]')!.textContent).toContain('keep=value');
    expect(requests.filter(r => r.url === '/api/models/fixture').every(r => r.timeout === 10000 && r.signal)).toBe(true);
});
it('cloned false/zero/empty settings survive delayed selected detail and a failed refresh', async () => {
    localStorage.setItem('clonedJobData', JSON.stringify({ name: 'Retained clone', model_id: 'fixture', mode: 'edit', params: { label: '', count: 0, enabled: false } }));
    await mount('/submit');
    await until(() => expect([...host.querySelectorAll<HTMLInputElement>('input[type="number"]')].some(input => input.value === '0')).toBe(true));
    expect([...host.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].some(input => !input.checked)).toBe(true);
    expect([...host.querySelectorAll<HTMLInputElement>('input')].some(input => input.value === 'default label')).toBe(false);
    failDetail = true; await act(async () => { await client.invalidateQueries({ queryKey: ['model', 'fixture'] }); }); await settle();
    expect([...host.querySelectorAll<HTMLInputElement>('input[type="number"]')].some(input => input.value === '0')).toBe(true);
});
it('leaving a pending selected-model read cancels its transport', async () => {
    hangDetail = true; await mount('/submit?model=fixture&mode=edit');
    await until(() => expect(requests.some(r => r.url === '/api/models/fixture')).toBe(true));
    const request = requests.find(r => r.url === '/api/models/fixture')!;
    await act(async () => root.render(null)); expect(request.signal!.aborted).toBe(true);
});
