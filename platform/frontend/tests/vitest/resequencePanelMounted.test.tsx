import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';

vi.mock('../../src/components/DeNovoNativeSources', () => ({
    NativeSourceInput: ({ onAcquire }: any) => <button onClick={() => onAcquire({ job_id: 'job-1', design_id: 'd-1', output_format: 'native' })}>pick source</button>,
}));
import { ResequencePanel } from '../../src/components/ResequencePanel';

let root: Root; const posts: any[] = []; const previous = api.defaults.adapter;
afterEach(async () => { if (root) await act(async () => root.unmount()); document.body.replaceChildren(); api.defaults.adapter = previous; posts.length = 0; });
async function mount() {
    api.defaults.adapter = async config => { posts.push({ url: config.url, body: JSON.parse(String(config.data)) }); return { data: { id: 'new-job' }, config, status: 201, statusText: 'Created', headers: {} } as any; };
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => { root.render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ResequencePanel /></MemoryRouter></QueryClientProvider>); });
}
const q = (label: string) => document.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!;
async function set(label: string, value: string) { const n = q(label); await act(async () => { const proto = n instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(n, value); n.dispatchEvent(new Event(n instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); }); }
const run = () => [...document.querySelectorAll('button')].find(b => b.textContent === 'Run resequence and validate') as HTMLButtonElement;
async function click(el: Element) { await act(async () => (el as HTMLElement).click()); }

it('cannot run without a structure, and sends the chosen engine, positions and validators', async () => {
    await mount();
    expect(run().disabled).toBe(true);
    await click([...document.querySelectorAll('button')].find(b => b.textContent === 'pick source')!);
    expect(run().disabled).toBe(false);
    await set('Resequence engine', 'fampnn'); await set('Resequence design chains', 'A,B'); await set('Resequence fixed positions', 'A1-20');
    await click(q('Validate with Protenix'));
    await click(run());
    expect(posts).toHaveLength(1);
    expect(posts[0].url).toBe('/api/jobs/resequence/from-structures');
    expect(posts[0].body).toMatchObject({ sources: [{ job_id: 'job-1', design_id: 'd-1' }], seq_method: 'fampnn', design_chains: 'A,B', fixed_positions: 'A1-20', structure_validators: ['esmfold2', 'protenix_v2'] });
    expect(posts[0].body).not.toHaveProperty('designable_positions');
});

it('refuses fixed and designable positions together, and an empty validator list', async () => {
    await mount();
    await click([...document.querySelectorAll('button')].find(b => b.textContent === 'pick source')!);
    await set('Resequence fixed positions', 'A1'); await set('Resequence designable positions', 'A2');
    expect(run().disabled).toBe(true);
    await set('Resequence fixed positions', '');
    expect(run().disabled).toBe(false);
    await click(q('Validate with ESMFold2'));
    expect(run().disabled).toBe(true);
});
