import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import BinderSelectedControls from '../../src/components/BinderSelectedControls';
import { readBinderSelection, writeBinderSelection, readBinderCandidateDocuments, submitBinderSelected } from '../../src/lib/binderContinuation';

let renderer: ReactTestRenderer | undefined;
const original = api.defaults.adapter;
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const posted: Array<Record<string, any>> = [];
const model = { params: [
    { name: 'maturation_repack_enabled', label: 'Repack', type: 'boolean', default: false },
    { name: 'maturation_anchors_enabled', label: 'Anchors', type: 'boolean', default: false },
    { name: 'maturation_flow_enabled', label: 'Flow', type: 'boolean', default: false },
    { name: 'maturation_redesign_enabled', label: 'Redesign', type: 'boolean', default: false },
    { name: 'samples', label: 'Native samples', type: 'integer', default: 1, minimum: 1, maximum: 8 },
    { name: 'pdb_paths', label: 'Owned inputs', type: 'string', hidden: true },
] };
const open = vi.fn(), md = vi.fn();
async function mount(ids = ['page-1-state-a', 'page-2-state-b'], launchContextId?: string) {
    posted.length = 0;
    api.defaults.adapter = async config => {
        let data: unknown = [];
        if (config.url?.startsWith('/api/models/')) data = model;
        if (config.url?.startsWith('/api/designs/')) data = { job_id: 'producer-sibling', pdb_path: 'primary.pdb' };
        if (config.url?.endsWith('/selection-context')) data = { candidate_documents: Object.fromEntries(ids.map(id => [id,
            [{ artifact_id: `alternate-${id}`, target_state: 'alternate-state', logical_path: 'native/alternate.cif', download_url: '/api/files/download/inputs/alternate.cif' }]])) };
        if (config.method === 'post') {
            posted.push(JSON.parse(config.data));
            data = { source_job_id: 'round2', root_job_id: 'root', selected_design_count: ids.length,
                operation: posted.at(-1)!.operation, launched_jobs: [{ id: 'queued-child', name: 'fixture queue receipt' }] };
        }
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => { renderer = create(<QueryClientProvider client={client}>
        <BinderSelectedControls sourceJobId="round2" selectedDesignIds={ids} onOpenJob={open} onStartMD={md} launchContextId={launchContextId} />
    </QueryClientProvider>); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
}
const button = (text: string) => renderer!.root.findAllByType('button').find(node => node.children.join('') === text)!;
afterEach(async () => { if (renderer) await act(async () => renderer!.unmount()); renderer = undefined; api.defaults.adapter = original; client.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear(); document.body.innerHTML = ''; });

it('mounts model-owned typed controls and submits ALL selected page/state identities with independent false values', async () => {
    await mount();
    const checks = renderer!.root.findAllByType('input').filter(node => node.props.type === 'checkbox');
    expect(checks).toHaveLength(4);
    await act(async () => checks[0].props.onChange({ target: { checked: true } }));
    await act(async () => button('Run selected operation').props.onClick());
    expect(posted).toHaveLength(1);
    expect(posted[0]).toMatchObject({ source_job_id: 'round2', design_ids: ['page-1-state-a', 'page-2-state-b'], operation: 'refine',
        params: { maturation_repack_enabled: true, maturation_anchors_enabled: false, maturation_flow_enabled: false, maturation_redesign_enabled: false, samples: 1 } });
    expect(JSON.stringify(renderer!.toJSON())).toContain('queued-child');
    await act(async () => button('Open child Job').props.onClick());
    expect(open).toHaveBeenCalledWith('queued-child');
});

it('keeps modified model settings across operation switches and hands MD the exact selected state ID', async () => {
    await mount();
    const field = renderer!.root.findAllByType('input').find(node => node.props.type === 'number')!;
    await act(async () => field.props.onChange({ target: { value: '5' } }));
    const select = renderer!.root.findByProps({ 'aria-label': 'Binder continuation operation' });
    await act(async () => select.props.onChange({ target: { value: 'caliby' } }));
    await act(async () => select.props.onChange({ target: { value: 'refine' } }));
    expect(renderer!.root.findAllByType('input').find(node => node.props.type === 'number')!.props.value).toBe(5);
    const buttons = renderer!.root.findAllByType('button').filter(node => node.children.join('') === 'Use as GROMACS MD starting structure');
    await act(async () => buttons[1].props.onClick());
    expect(md).toHaveBeenCalledWith('page-2-state-b');
    expect(posted).toHaveLength(0);
});

it('submits the complete global FrustraMPNN settings rather than a simplified score action', async () => {
    await mount();
    await act(async () => renderer!.root.findByProps({ 'aria-label': 'Binder continuation operation' }).props.onChange({ target: { value: 'frustrampnn' } }));
    await act(async () => button('Run selected operation').props.onClick());
    expect(posted[0].operation).toBe('frustrampnn');
    expect(posted[0].frustrampnn_settings).toHaveProperty('protein_selection');
    expect(posted[0].frustrampnn_settings).toHaveProperty('source_structure');
    expect(posted[0].design_ids).toEqual(['page-1-state-a', 'page-2-state-b']);
});

it('shows queue errors without a fabricated child success', async () => {
    await mount();
    api.defaults.adapter = async () => { throw new Error('model-owned request failed'); };
    await act(async () => button('Run selected operation').props.onClick());
    expect(JSON.stringify(renderer!.toJSON())).toContain('model-owned request failed');
    expect(renderer!.root.findAllByProps({ role: 'status' })).toHaveLength(0);
});

it('persists all selected IDs per source job without mixing another round', () => {
    writeBinderSelection('job1', ['p1', 'p2-state1', 'p2-state2']);
    writeBinderSelection('job2', ['other']);
    expect(readBinderSelection('job1')).toEqual(['p1', 'p2-state1', 'p2-state2']);
    expect(readBinderSelection('job2')).toEqual(['other']);
});

it('selects exact alternate CIF/state without changing cross-page identities and retains native-primary reset', async () => {
    await mount(undefined, 'destination-attempt');
    const selector = renderer!.root.findByProps({ 'aria-label': 'Document for page-2-state-b' });
    const option = selector.findAllByType('option')[1];
    const optionValue = option.props.value;
    await act(async () => selector.props.onChange({ target: { value: optionValue } }));
    expect(JSON.stringify(renderer!.toJSON())).toContain('producer-sibling');
    await act(async () => button('Run selected operation').props.onClick());
    expect(posted[0].design_ids).toEqual(['page-1-state-a', 'page-2-state-b']);
    expect(posted[0].candidate_documents).toEqual({ 'page-2-state-b': { artifact_id: 'alternate-page-2-state-b', target_state: 'alternate-state' } });
    expect(posted[0].launch_context_id).toBe('destination-attempt');
    expect(posted[0].params).not.toHaveProperty('launch_context_id');
    expect(readBinderCandidateDocuments('round2')).toEqual(posted[0].candidate_documents);
    expect(readBinderCandidateDocuments('other-round')).toEqual({});
    await act(async () => renderer!.unmount()); renderer = undefined;
    await mount();
    expect(renderer!.root.findByProps({ 'aria-label': 'Document for page-2-state-b' }).props.value).toBe(optionValue);
    await act(async () => renderer!.root.findByProps({ 'aria-label': 'Document for page-2-state-b' }).props.onChange({ target: { value: '{}' } }));
    await act(async () => button('Run selected operation').props.onClick());
    expect(posted[0].candidate_documents).toEqual({});
});

it('never falls back to primary inspection when an alternate download fails, and still submits the requested state', async () => {
    await mount(['page-1-state-a']);
    const fetcher = vi.fn().mockResolvedValue({ ok: false, status: 404 });
    vi.stubGlobal('fetch', fetcher);
    const selector = renderer!.root.findByProps({ 'aria-label': 'Document for page-1-state-a' });
    await act(async () => selector.props.onChange({ target: { value: selector.findAllByType('option')[1].props.value } }));
    await act(async () => renderer!.root.findByProps({ 'aria-label': 'Binder continuation operation' }).props.onChange({ target: { value: 'frustrampnn' } }));
    expect(fetcher).toHaveBeenCalledWith('/api/files/download/inputs/alternate.cif', expect.anything());
    expect(fetcher.mock.calls.every(call => call[0] === '/api/files/download/inputs/alternate.cif')).toBe(true);
    expect(JSON.stringify(renderer!.toJSON())).toContain('Source inspection unavailable');
    await act(async () => button('Run selected operation').props.onClick());
    expect(posted[0].candidate_documents['page-1-state-a'].target_state).toBe('alternate-state');
    expect(posted[0].frustrampnn_settings).toHaveProperty('protein_selection');
});

it('uses mounted shared remote approval with the retained exact document request and destination, not endpoint replay', async () => {
    const prepared = { name: 'prepared', model_id: 'boltz2', mode: 'complex', execution_target_id: 'vast:one', launch_context_id: 'destination-attempt',
        params: { sequence: 'G', selected_input_manifest: '/retained/selection_manifest.json', source_identity_json: '/retained/source_identity.json' } };
    const preview = { schema: 'bms.job.execution-preview.v1', approval_digest: 'a'.repeat(64), admissible: true,
        request: prepared, plan: { requested_json: prepared.params, effective_json: prepared.params,
            source_identity: { revision: 'b'.repeat(40), tree: 'c'.repeat(40) }, metadata: { static_components: [], dynamic_templates: [], external_services: [] } }, deferred_preparation: [], blockers: [] };
    const post = vi.spyOn(api, 'post').mockImplementation(async url => {
        if (url === '/api/jobs/execution-plan/preview') return { data: preview };
        if (url === '/api/jobs') return { data: { id: 'queued', name: 'prepared' } };
        throw { isAxiosError: true, response: { status: 409, data: { detail: {
            code: 'remote_prepared_job_review_required', job_request: prepared,
            response_context: { source_job_id: 'source', root_job_id: 'root', selected_design_count: 1, operation: 'predict_boltz2' },
        } } } };
    });
    const pending = submitBinderSelected({ source_job_id: 'source', design_ids: ['d'], operation: 'predict_boltz2',
        candidate_documents: { d: { artifact_id: 'alternate', target_state: 'other' } }, launch_context_id: 'destination-attempt' });
    const approve = () => [...document.querySelectorAll('button')].find(row => row.textContent === 'Approve and submit');
    for (let attempt = 0; attempt < 150 && !approve(); attempt++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)); });
    expect(post).toHaveBeenCalledTimes(2);
    await act(async () => { approve()!.click(); await pending; });
    expect(post).toHaveBeenCalledTimes(3);
    expect(post.mock.calls[2][1]).toMatchObject({ ...prepared, execution_plan_approval: preview.approval_digest });
    expect((await pending).launched_jobs[0].id).toBe('queued');
}, 15000);
