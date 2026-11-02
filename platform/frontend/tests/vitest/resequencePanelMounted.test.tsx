import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import models from './fixtures/resequenceDesignerModels.json';

vi.mock('../../src/components/DeNovoNativeSources', () => ({
    NativeSourceInput: ({ onAcquire }: any) => <button onClick={() => onAcquire({ job_id: 'job-1', design_id: 'd-1', output_format: 'native' })}>pick source</button>,
}));
import { ResequencePanel } from '../../src/components/ResequencePanel';

let root: Root; const posts: any[] = []; const previous = api.defaults.adapter;
afterEach(async () => { if (root) await act(async () => root.unmount()); document.body.replaceChildren(); api.defaults.adapter = previous; posts.length = 0; });
async function mount() {
    api.defaults.adapter = async config => {
        const model = /^\/api\/models\/([^/]+)$/.exec(String(config.url));
        if (config.method === 'get' && model) return { data: (models as any)[model[1]], config, status: 200, statusText: 'OK', headers: {} } as any;
        posts.push({ url: config.url, body: JSON.parse(String(config.data)) });
        return { data: { id: 'new-job' }, config, status: 201, statusText: 'Created', headers: {} } as any;
    };
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => { root.render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ResequencePanel /></MemoryRouter></QueryClientProvider>); });
}
const q = (label: string) => document.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(`[aria-label="${label}"]`)!;
async function set(label: string, value: string) { const n = q(label); await act(async () => { const proto = n instanceof HTMLSelectElement ? HTMLSelectElement.prototype : n instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype; Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(n, value); n.dispatchEvent(new Event(n instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); }); }
const run = () => [...document.querySelectorAll('button')].find(b => b.textContent === 'Run resequence and validate') as HTMLButtonElement;
async function click(el: Element) { await act(async () => (el as HTMLElement).click()); }

it('cannot run without a structure, and sends the chosen engine, positions and validators', async () => {
    await mount();
    expect(run().disabled).toBe(true);
    await click([...document.querySelectorAll('button')].find(b => b.textContent === 'pick source')!);
    expect(run().disabled).toBe(false);
    await set('Resequence engine', 'fampnn'); await set('Resequence design chains', 'A,B'); await set('Resequence fixed positions', 'A1-20');
    await click(q('Validate with ESMFold2 (optional)'));
    await click(run());
    expect(posts).toHaveLength(1);
    expect(posts[0].url).toBe('/api/jobs/resequence/from-structures');
    expect(posts[0].body).toMatchObject({ sources: [{ job_id: 'job-1', design_id: 'd-1' }], seq_method: 'fampnn', design_chains: 'A,B', fixed_positions: 'A1-20', structure_validators: ['protenix_v2', 'esmfold2'] });
    expect(posts[0].body).not.toHaveProperty('designable_positions');
});

it('refuses fixed and designable positions together, and an empty validator list', async () => {
    await mount();
    await click([...document.querySelectorAll('button')].find(b => b.textContent === 'pick source')!);
    await set('Resequence fixed positions', 'A1'); await set('Resequence designable positions', 'A2');
    expect(run().disabled).toBe(true);
    await set('Resequence fixed positions', '');
    expect(run().disabled).toBe(false);
    await click(q('Validate with Protenix v2'));
    expect(run().disabled).toBe(true);
});

const flush = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); }); };
const fieldNames = () => [...document.querySelectorAll('[aria-label^="Designer setting "]')].map(n => n.getAttribute('aria-label')!.replace('Designer setting ', ''));

it('builds each designer\'s settings from its served inventory and sends only what was changed', async () => {
    await mount();
    await click([...document.querySelectorAll('button')].find(b => b.textContent === 'pick source')!);
    await set('Resequence engine', 'ligandmpnn'); await set('LigandMPNN mode', 'metal_aware'); await flush();
    for (const group of document.querySelectorAll('details')) (group as HTMLDetailsElement).open = true;
    expect(fieldNames()).toEqual(expect.arrayContaining(['temperature', 'batch_size', 'metal_type', 'omit']));
    expect(fieldNames()).not.toEqual(expect.arrayContaining(['target_pdb']));
    expect(fieldNames()).not.toEqual(expect.arrayContaining(['fixed_residues']));
    await click(run());
    expect(posts[0].body).toMatchObject({ seq_method: 'ligandmpnn', designer_mode: 'metal_aware' });
    expect(posts[0].body).not.toHaveProperty('designer_settings');
    await set('Designer setting batch_size', '3'); await set('Designer setting temperature', '0.25');
    await click(run());
    expect(posts[1].body.designer_settings).toEqual({ batch_size: 3, temperature: 0.25 });
    await set('Designer setting batch_size', '');
    await click(run());
    expect(posts[2].body.designer_settings).toEqual({ temperature: 0.25 });
});

it('switching engine drops the previous engine\'s settings; Caliby and ProtonPottsMPNN show their own fields', async () => {
    await mount();
    await click([...document.querySelectorAll('button')].find(b => b.textContent === 'pick source')!);
    await set('Resequence engine', 'ligandmpnn'); await flush();
    for (const group of document.querySelectorAll('details')) (group as HTMLDetailsElement).open = true;
    await set('Designer setting batch_size', '5');
    await set('Resequence engine', 'caliby'); await flush();
    for (const group of document.querySelectorAll('details')) (group as HTMLDetailsElement).open = true;
    expect(fieldNames()).toEqual(expect.arrayContaining(['num_seqs_per_pdb', 'temperature', 'model_name']));
    expect(fieldNames()).not.toContain('ensembles');
    await set('Designer setting num_seqs_per_pdb', '2');
    await click(run());
    expect(posts[0].body).toMatchObject({ seq_method: 'caliby', designer_settings: { num_seqs_per_pdb: 2 } });
    expect(posts[0].body).not.toHaveProperty('designer_mode');
    await set('Resequence engine', 'protonpottsmpnn'); await flush();
    for (const group of document.querySelectorAll('details')) (group as HTMLDetailsElement).open = true;
    expect(fieldNames()).toContain('seed');
    expect(fieldNames()).not.toContain('target_pdb');
    expect(fieldNames()).not.toContain('binder_chain');
    await click(run());
    expect(posts[1].body).toMatchObject({ seq_method: 'protonpottsmpnn' });
    expect(posts[1].body).not.toHaveProperty('designer_settings');
});

it('structured native tables go through a JSON box that rejects unknown names and blocks the run', async () => {
    await mount();
    await click([...document.querySelectorAll('button')].find(b => b.textContent === 'pick source')!);
    await set('Resequence engine', 'ligandmpnn'); await flush();
    await set('Structured designer settings', '{not json');
    expect(run().disabled).toBe(true);
    await set('Structured designer settings', '{"target_pdb": "x.pdb"}');
    expect(run().disabled).toBe(true);
    await set('Structured designer settings', '{"bias": {"A": -1}}');
    expect(run().disabled).toBe(false);
    await click(run());
    expect(posts[0].body.designer_settings).toEqual({ bias: { A: -1 } });
});

it('ProteinMPNN and FAMPNN show only the settings the loop forwards, with model defaults as placeholders', async () => {
    await mount();
    await click([...document.querySelectorAll('button')].find(b => b.textContent === 'pick source')!);
    await set('Resequence engine', 'mpnn'); await flush();
    for (const group of document.querySelectorAll('details')) (group as HTMLDetailsElement).open = true;
    expect(fieldNames()).toEqual(expect.arrayContaining(['mpnn_temperature', 'seqs_per_design', 'mpnn_omitAAs', 'mpnn_checkpoint_model']));
    for (const owned of ['input_pdb', 'fixed_positions', 'design_chain', 'target_chain', 'binder_chains', 'target_chains', 'mpnn_bias_AA_jsonl', 'mpnn_extra_config'])
        expect(fieldNames()).not.toContain(owned);
    expect(q('Designer setting mpnn_temperature').getAttribute('placeholder')).toBe('0.1');
    await click(run());
    expect(posts[0].body).not.toHaveProperty('designer_settings');
    await set('Designer setting mpnn_temperature', '0.3'); await set('Designer setting mpnn_checkpoint_model', 'v_48_030');
    await click(run());
    expect(posts[1].body).toMatchObject({ seq_method: 'mpnn', designer_settings: { mpnn_temperature: 0.3, mpnn_checkpoint_model: 'v_48_030' } });
    await set('Resequence engine', 'fampnn'); await flush();
    for (const group of document.querySelectorAll('details')) (group as HTMLDetailsElement).open = true;
    expect(fieldNames()).toEqual(expect.arrayContaining(['fampnn_temperature', 'fampnn_checkpoint', 'fampnn_num_steps', 'fampnn_seed', 'fampnn_exclude_cys']));
    for (const owned of ['input_pdb', 'fixed_positions', 'design_chain', 'target_chain', 'fampnn_checkpoint_path', 'fampnn_constraint_mode', 'fampnn_extra_config', 'fampnn_fix_target_sidechains'])
        expect(fieldNames()).not.toContain(owned);
    expect(fieldNames()).not.toContain('mpnn_temperature');
    await set('Designer setting fampnn_temperature', '0.5');
    await click(run());
    expect(posts[2].body.designer_settings).toEqual({ fampnn_temperature: 0.5 });
});

const validatorNames = (id: string) => [...document.querySelectorAll(`[data-validator-settings="${id}"] [aria-label^="Validator setting "]`)]
    .map(n => n.getAttribute('aria-label')!.replace(`Validator setting ${id} `, ''));

it('each selected validator gets its own settings; defaults read as loop defaults and only changes are sent', async () => {
    await mount();
    await click([...document.querySelectorAll('button')].find(b => b.textContent === 'pick source')!);
    await flush();
    expect(document.querySelector('[data-validator-settings="protenix_v2"]')).not.toBeNull();
    expect(document.querySelector('[data-validator-settings="boltz2"]')).toBeNull();
    for (const group of document.querySelectorAll('details')) (group as HTMLDetailsElement).open = true;
    expect(validatorNames('protenix_v2')).toEqual(expect.arrayContaining(['protenix_n_sample', 'protenix_n_step', 'protenix_n_cycle', 'protenix_seeds']));
    for (const owned of ['sequence', 'protenix_msa_backend', 'msa_provider', 'protenix_model_weights', 'complex_components'])
        expect(validatorNames('protenix_v2')).not.toContain(owned);
    // The catalog default is not what the loop runs, so no catalog number is shown as a placeholder.
    expect(document.querySelector('[aria-label="Validator setting protenix_v2 protenix_n_sample"]')!.getAttribute('placeholder')).toBe('');
    await click(run());
    expect(posts[0].body).not.toHaveProperty('validator_settings');
    await click(q('Validate with Boltz-2')); await click(q('Validate with ESMFold2 (optional)')); await flush();
    for (const group of document.querySelectorAll('details')) (group as HTMLDetailsElement).open = true;
    expect(validatorNames('boltz2')).toEqual(expect.arrayContaining(['boltz_recycling_steps', 'boltz_sampling_steps', 'boltz_diffusion_samples']));
    expect(validatorNames('boltz2')).not.toContain('sequence');
    expect(validatorNames('esmfold2')).toEqual(expect.arrayContaining(['num_loops', 'num_sampling_steps', 'num_diffusion_samples', 'model_variant', 'seed']));
    expect(validatorNames('esmfold2')).not.toContain('pdb_sequence_path');
    await set('Validator setting protenix_v2 protenix_n_sample', '2');
    await set('Validator setting boltz2 boltz_sampling_steps', '100');
    await set('Validator setting esmfold2 num_loops', '4');
    await click(run());
    expect(posts[1].body.validator_settings).toEqual({ protenix_v2: { protenix_n_sample: 2 }, boltz2: { boltz_sampling_steps: 100 }, esmfold2: { num_loops: 4 } });
    // Deselecting a validator drops its settings from the request.
    await click(q('Validate with Boltz-2')); await flush();
    await click(run());
    expect(posts[2].body.validator_settings).toEqual({ protenix_v2: { protenix_n_sample: 2 }, esmfold2: { num_loops: 4 } });
    expect(posts[2].body.structure_validators).toEqual(['protenix_v2', 'esmfold2']);
});
