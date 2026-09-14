import React from 'react';
import { writeFileSync } from 'node:fs';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { StructurePredictionTemplate } from '../../src/components/StructurePredictionTemplate';
import { StructureReorchestratePanel } from '../../src/components/dashboard/StructureReorchestratePanel';
import { deriveStructureReorchestrateSettings, buildStructureReorchestrateOverrides } from '../../src/components/dashboard/reorchestrateStructureSettings';

// Real launcher, native settings, shared provider controls, molecular component
// hydration and HTTP serialization; only worker/3D/integration chrome is isolated.
const wire = vi.hoisted(() => ({ preview: null as any }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: (props: any) => { wire.preview = props.workflowRequest; return null; } }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null,
    useModelIntegrationConfig: () => ({ data: { workflows: { structure_prediction: { default_enabled: false } } }, isPending: false, isFetching: false, isError: false }) }));
const text = (n: ReactTestInstance): string => n.children.map(c => typeof c === 'string' ? c : text(c)).join('');
const flush = async () => { await act(async () => { await new Promise(r => setTimeout(r, 0)); }); };
const originalAdapter = api.defaults.adapter;
let renderer: ReactTestRenderer;
let client: QueryClient;
let requests: any[] = [];
let drafts: any[] = [];
let templates: any[] = [];
const fixture = {
    job_name: 'offline-complex', pred_method: 'esmfold2', sequence: 'MQIFVKTLTGKTITLEVEPSDTI', sequence_name: 'saved-protein',
    complex_mode: true, primary_chain_id: 'A', num_parallel_jobs: 1, model_variant: 'full',
    quality_preset: 'custom', num_loops: 3, num_sampling_steps: 200, num_diffusion_samples: 1,
    esmf_use_msa: true, msa_provider: 'neurosnap_api', msa_neurosnap_coverage_percent: 45.5,
    msa_neurosnap_identity_percent: 65.5, msa_neurosnap_max_sequences: 321,
    msa_neurosnap_force_uppercase: false, msa_neurosnap_pad_sequences: false,
    run_frustrampnn: true,
    complex_components: [
        { id: 'A', type: 'protein', sequence: 'MQIFVKTLTGKTITLEVEPSDTI' },
        { id: 'B', type: 'dna', sequence: 'GATATGTAGCTGCT' },
        { id: 'C', type: 'ligand', ccd: 'MN' }, { id: 'D', type: 'ligand', ccd: 'MN' },
        { id: 'E', type: 'ligand', ccd: 'GTP' },
    ],
};
async function mount(initialValues: any = fixture) {
    requests = []; drafts = []; templates = [];
    vi.spyOn(window, 'alert').mockImplementation(() => {});
    api.defaults.adapter = async config => {
        if (config.method === 'post') { requests.push(JSON.parse(config.data)); throw new Error('offline captured POST; no job created'); }
        if (config.url === '/msa/providers') return { data: { providers: { neurosnap_api: { configured: true, credential_configured: true, authentication: 'not_checked', live_acceptance: 'not_checked_by_setup', blockers: [] } } }, status: 200, statusText: 'OK', headers: {}, config };
        if (config.url?.includes('msa')) return { data: { cache_entries: 0, available: true, configured: true }, status: 200, statusText: 'OK', headers: {}, config };
        throw new Error(`Unexpected offline GET ${config.url}`);
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    await act(async () => { renderer = create(<MemoryRouter><QueryClientProvider client={client}>
        <StructurePredictionTemplate onBack={() => {}} initialValues={initialValues} onDraftChange={v => drafts.push(v)} onOpenTemplateManager={v => templates.push(v)} />
    </QueryClientProvider></MemoryRouter>); });
    await flush();
}
async function click(label: string) {
    const button = renderer.root.findAllByType('button').find(n => text(n).includes(label));
    expect(button, label).toBeTruthy(); await act(async () => button!.props.onClick()); await flush();
}
async function edit(label: string, value: string | number) {
    const input = renderer.root.findByProps({ 'aria-label': `ESMFold2 ${label}` });
    await act(async () => input.props.onChange({ target: { value: String(value), valueAsNumber: Number(value) } })); await flush();
}
afterEach(async () => { if (renderer) await act(async () => renderer.unmount()); client?.clear(); api.defaults.adapter = originalAdapter; vi.restoreAllMocks(); });

it('populated actual launcher preserves native settings and molecules in draft/template/preview/submit/clone', async () => {
    await mount();
    expect(renderer.root.findByProps({ 'aria-label': 'ESMFold2 Structure samples' }).props.value).toBe(1);
    await click('MSA Quality Options');
    expect(text(renderer.root)).toContain('Neurosnap');
    const provider = renderer.root.findAllByType('select').find(n => n.props.value === 'neurosnap_api');
    expect(provider).toBeTruthy();
    const coverage = renderer.root.findAllByType('label').find(n => text(n).includes('Coverage'))!.findByType('input');
    await act(async () => coverage.props.onChange({ target: { value: '55.5', valueAsNumber: 55.5 } }));
    await edit('Random seed (optional)', 0);
    await edit('Structure samples', 3);
    await edit('Structure samples', 1);
    await click('Save Template');
    const saved = templates.at(-1).currentParams;
    expect(saved).toEqual(drafts.at(-1));
    const expected = { model_variant: 'full', quality_preset: 'custom', num_loops: 3, num_sampling_steps: 200, num_diffusion_samples: 1,
        esmf_use_msa: true, msa_provider: 'neurosnap_api', msa_neurosnap_coverage_percent: 55.5, seed: 0, run_frustrampnn: true };
    expect(saved).toMatchObject(expected); expect(saved).not.toHaveProperty('use_msa');
    expect(saved.complex_components).toHaveLength(5);
    expect(saved.complex_components.filter((v: any) => v.ccd === 'MN')).toHaveLength(2);
    expect(wire.preview.params).toMatchObject(expected);
    expect(wire.preview.params.complex_components).toEqual(saved.complex_components);
    await click('Launch Prediction');
    expect(requests).toHaveLength(1); expect(requests[0].params).toMatchObject(expected);
    expect(requests[0].params.complex_components).toEqual(saved.complex_components);
    if (process.env.BMS_ESMFOLD2_WIRE_DIR) writeFileSync(`${process.env.BMS_ESMFOLD2_WIRE_DIR}/frontend-mounted-wire.json`, JSON.stringify({ fixture: 'offline populated synthetic complex; not a live submission', draft: saved, preview: wire.preview, submitted: requests[0] }, null, 2));
    await act(async () => renderer.unmount()); client.clear();
    await mount(saved);
    expect(drafts.at(-1)).toMatchObject(expected);
    expect(wire.preview.params).toMatchObject(expected);
    const msaToggle = renderer.root.findAllByType('label').find(n => text(n) === 'Use ESMFold2 MSA')!.findByType('input');
    await act(async () => msaToggle.props.onChange({ target: { checked: false } })); await flush();
    const disabledDraft = drafts.at(-1);
    expect(disabledDraft).toMatchObject({ esmf_use_msa: false, msa_provider: 'neurosnap_api', msa_neurosnap_coverage_percent: 55.5 });
    await act(async () => renderer.unmount()); client.clear();
    await mount(disabledDraft);
    expect(wire.preview.params).toMatchObject({ esmf_use_msa: false, msa_provider: 'neurosnap_api', msa_neurosnap_coverage_percent: 55.5 });
});
it('native presets reveal actual counts, custom edits and invalid input block preview and submit', async () => {
    await mount(); await edit('Quality preset', 'thorough');
    expect(drafts.at(-1)).toMatchObject({ num_loops: 5, num_sampling_steps: 100, num_diffusion_samples: 2 });
    await edit('Structure samples', 1); expect(drafts.at(-1).quality_preset).toBe('custom');
    await edit('Diffusion sampling steps', 1001);
    expect(text(renderer.root)).toContain('must be an integer from 1 to 1000'); expect(wire.preview).toBeNull();
    await click('Launch Prediction'); expect(requests).toHaveLength(0);
});
it('Fast plus MSA stays visibly selected but blocks preview/submit until Full is explicitly chosen', async () => {
    await mount({ ...fixture, model_variant: 'fast' });
    expect(drafts.at(-1)).toMatchObject({ model_variant: 'fast', esmf_use_msa: true });
    expect(text(renderer.root)).toContain('Choose Full to use MSA');
    expect(wire.preview).toBeNull();
    await click('Launch Prediction'); expect(requests).toHaveLength(0);
    await edit('Model Variant', 'full');
    expect(wire.preview.params).toMatchObject({ model_variant: 'full', esmf_use_msa: true });
});
it('old omitted settings keep admitted no-MSA 3/50/1 and disabled MSA retains provider choices through clone', async () => {
    await mount({ pred_method: 'esmfold2', sequence: 'MQLK', model_variant: 'full', run_frustrampnn: false });
    expect(drafts.at(-1)).toMatchObject({ esmf_use_msa: false, num_loops: 3, num_sampling_steps: 50, num_diffusion_samples: 1 });
    expect(renderer.root.findAllByType('button').some(n => text(n).includes('MSA Quality Options'))).toBe(false);
});
it('actual retry panel synchronizes MSA and persists displayed native overrides without sample inflation', async () => {
    api.defaults.adapter = async () => { throw new Error('offline'); };
    const job = { model_id: 'esmfold2', mode: 'predict', params: fixture };
    let settings = deriveStructureReorchestrateSettings(job);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const render = () => <QueryClientProvider client={client}><StructureReorchestratePanel settings={settings} onChange={v => { settings = v; renderer.update(render()); }} /></QueryClientProvider>;
    await act(async () => { renderer = create(render()); });
    expect(settings.skipMsa).toBe(false);
    await edit('Random seed (optional)', 0);
    const overrides = buildStructureReorchestrateOverrides(job, settings);
    expect(overrides.seed).toBe(0); expect(overrides.num_diffusion_samples).toBeUndefined();
    const skip = renderer.root.findAllByType('label').find(n => text(n).includes('Skip MSA on retry'))!.findByType('input');
    await act(async () => skip.props.onChange({ target: { checked: true } }));
    expect(buildStructureReorchestrateOverrides(job, settings).esmf_use_msa).toBe(false);
});
