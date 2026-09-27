import React from 'react';
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer';
import { afterEach, expect, test, vi } from 'vitest';
import { AxiosError } from 'axios';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { reviewExecutionPlan } from '../../src/components/ExecutionPlanApproval';
vi.mock('../../src/components/ExecutionPlanApproval', () => ({ reviewExecutionPlan: vi.fn(async () => true) }));
const mount = (element: React.ReactElement) => create(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{element}</QueryClientProvider>);
import { api } from '../../src/lib/api';
import BlindPoseSelectedControls from '../../src/components/BlindPoseSelectedControls';
import BinderDiagnosticRawResults, { diagnosticCsv } from '../../src/components/BinderDiagnosticRawResults';

const text = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
const original = api.defaults.adapter;
let renderer: ReactTestRenderer;
afterEach(async () => { api.defaults.adapter = original; if (renderer) await act(async () => renderer.unmount()); });

test('descendant controls load root targets and submit exact state independently', async () => {
    const requests: Array<{ url: string; body: any }> = [];
    api.defaults.adapter = async config => {
        const url = String(config.url);
        requests.push({ url, body: typeof config.data === 'string' ? JSON.parse(config.data) : config.data });
        const data = url === '/api/execution-targets' ? [] : url.endsWith('/selection-context') ? {
            source_job_id: 'round2', lineage_root_job_id: 'root', targets: [{ name: 'independent', owner_job_id: 'root' }],
            candidate_documents: { selected: [{ artifact_id: 'state-artifact', target_state: 'off', logical_path: 'native/off.cif' }] },
        } : url.includes('ligandmpnn') ? { job: { id: 'ligand' } } : { id: 'blind' };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => { renderer = mount(<BlindPoseSelectedControls sourceJobId="round2" sourceModelId="proteinmpnn"
        sourceParams={{}} selectedDesignIds={['selected']} onOpenJob={() => undefined} />); });
    const click = async (label: string) => { await act(async () => renderer.root.findAllByType('button').find(b => text(b) === label)!.props.onClick()); };
    const change = async (label: string, value: string) => { await act(async () => renderer.root.findByProps({ 'aria-label': label }).props.onChange({ target: { value } })); };
    await click('Load candidate documents and declared targets');
    expect(text(renderer.root)).toContain('scientific root root');
    await change('Declared target', 'independent');
    await change('Document/state for selected', 'state-artifact');
    await change('Blind pose target chains', 'T');
    await change('Binder chains for selected', 'B');
    await click('Run selected blind pose');
    const blind = requests.find(row => row.url === '/api/blind-pose/selected')!.body;
    expect(blind.execution_target_id).toBeNull();
    expect(blind.target_name).toBe('independent');
    expect(blind.candidate_documents).toEqual({ selected: { artifact_id: 'state-artifact', target_state: 'off' } });
    expect(blind.design_ids).toEqual(['selected']);
    await change('Fixed binder chain', 'B');
    await change('Target chain', 'T');
    await change('Target patch residue IDs', 'T1');
    await click('Run selected interface context');
    const ligand = requests.find(row => row.url.includes('ligandmpnn'))!.body;
    expect(ligand.candidate_documents).toEqual(blind.candidate_documents);
    expect(ligand.settings).toEqual({ binder_chain: 'B', target_chain: 'T', target_patch: ['T1'], seed: 0, samples: 1, temperature: 0.1 });
    expect(ligand).not.toHaveProperty('target_name');
    expect(ligand.execution_target_id).toBeNull();
});

test.each(['blind', 'interface'] as const)('%s reviews retained child and opens its actual Job', async kind => {
    const requests: Array<{ url: string; body: any }> = [];
    const child = { name: 'retained', model_id: kind === 'blind' ? 'esmfold2' : 'ligandmpnn',
        mode: kind === 'blind' ? 'blind_pose' : 'interface_context', execution_target_id: 'worker',
        launch_context_id: 'child-context', params: { selection_snapshot: '/retained/exact.json' } };
    const opened: string[] = [];
    api.defaults.adapter = async config => {
        const url = String(config.url);
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        requests.push({ url, body });
        if (url.endsWith('/selected')) throw new AxiosError('review', 'ERR_BAD_REQUEST', config, undefined, {
            status: 409, statusText: 'Conflict', headers: {}, config,
            data: { detail: { code: 'remote_prepared_job_review_required', job_request: child,
                response_context: { selection: body } } },
        });
        const data = url === '/api/execution-targets' ? [{ id: 'worker', name: 'test worker', active: true, state: 'ready', capabilities: {} }]
            : url.endsWith('/execution-plan/preview') ? { request: child, approval_digest: 'a'.repeat(64) }
            : { id: 'actual-child' };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => { renderer = mount(<BlindPoseSelectedControls sourceJobId="source" sourceModelId="proteinmpnn"
        sourceParams={{}} selectedDesignIds={['selected']} launchContextId="destination"
        candidateDocuments={{ selected: { artifact_id: 'exact-state', target_state: 'off' } }} onOpenJob={id => opened.push(id)} />); });
    const click = async (label: string) => { await act(async () => renderer.root.findAllByType('button').find(b => text(b) === label)!.props.onClick()); };
    await vi.waitFor(() => expect(renderer.root.findAllByType('button').some(b => text(b) === 'Vast · test worker')).toBe(true));
    await click('Vast · test worker');
    await click(kind === 'blind' ? 'Run selected blind pose' : 'Run selected interface context');
    const selected = requests.filter(row => row.url.endsWith('/selected'));
    expect(selected).toHaveLength(1);
    expect(selected[0].body).toMatchObject({ execution_target_id: 'worker', launch_context_id: 'destination',
        candidate_documents: { selected: { artifact_id: 'exact-state', target_state: 'off' } } });
    expect(requests.find(row => row.url.endsWith('/execution-plan/preview'))!.body).toMatchObject(child);
    expect(requests.find(row => row.url === '/api/jobs')!.body).toMatchObject({ ...child, execution_plan_approval: 'a'.repeat(64) });
    expect(reviewExecutionPlan).toHaveBeenCalled();
    await click('Open result Job');
    expect(opened).toEqual(['actual-child']);
});

test('raw native samples have readable tables and lossless JSON/CSV exports', async () => {
    const native = { source_job_id: 'round2', records: [{ candidate_id: 'selected', conditions: {
        supplied_complex: { samples: [{ batch_idx: 0, design_idx: 0, patch_exact_matches: 1, patch_residue_count: 2, sampled_patch: { T1: 'A', T2: 'G' } }] },
        without_binder: { samples: [{ batch_idx: 0, design_idx: 0, patch_exact_matches: 0, patch_residue_count: 2, sampled_patch: { T1: 'G', T2: 'A' } }] },
    } }] };
    await act(async () => { renderer = create(<BinderDiagnosticRawResults native={native} jobId="result" />); });
    expect(renderer.root.findByProps({ 'aria-label': 'Raw diagnostic observations' }).findAllByType('tbody')[0].findAllByType('tr')).toHaveLength(2);
    const csv = diagnosticCsv(native);
    expect(csv).toContain('"supplied_complex"');
    expect(csv).toContain('"without_binder"');
    const json = renderer.root.findAllByType('a').find(a => a.props.download.endsWith('.json'))!;
    expect(JSON.parse(decodeURIComponent(json.props.href.split(',')[1]))).toEqual(native);
    expect(text(renderer.root)).toContain('does not establish affinity');
});
