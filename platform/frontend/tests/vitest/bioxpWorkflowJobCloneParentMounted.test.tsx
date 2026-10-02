import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { webcrypto } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpWorkflowEditor } from '../../src/components/BioXpWorkflowEditor';
import { api } from '../../src/lib/api';
import nativeFixture from '../../../api/schemas/bioxp_workflow_native.json';
import { canonicalWorkflowJobId, pendingWorkflowRunStorageKey, retainPendingWorkflowRun, type PendingWorkflowRun } from '../../src/lib/bioxpSavedWorkflowRun';

// Transport fixture only. Real components/helpers/serialization stay mounted;
// receiving qualification replays exported requests against the actual API/SQLite.
let host: HTMLDivElement, root: Root, client: QueryClient;
let requests: any[], db: Record<string, any>, run: PendingWorkflowRun;
let delayClone: Promise<void> | null, delaySave: Promise<void> | null;
const adapter = api.defaults.adapter, exported: any[] = [];
async function mount() {
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpWorkflowEditor generation={19} connected={false} controlsEnabled={false} /></QueryClientProvider>));
}
async function fresh() { await act(async () => root.unmount()); root = createRoot(host); await mount(); }
function button(text: string) {
    const el = [...host.querySelectorAll('button')].find(node => node.textContent === text);
    expect(el, text).toBeTruthy(); return el!;
}
async function click(text: string) { await act(async () => button(text).click()); }
function input(label: string): HTMLInputElement | HTMLSelectElement {
    const aria = host.querySelector(`[aria-label="${label}"]`);
    const wrapped = [...host.querySelectorAll('label')].find(node => node.textContent?.startsWith(label))?.querySelector('input,select');
    expect(aria || wrapped, label).toBeTruthy(); return (aria || wrapped) as HTMLInputElement | HTMLSelectElement;
}
async function change(label: string, value: string) {
    const el = input(label);
    await act(async () => {
        Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value);
        el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    });
}
async function cloneRetained() {
    await click('Review');
    await click('Clone previous job');
    const pane = host.querySelector('[aria-label="Clone previous job"]')!;
    const picker = [...pane.querySelectorAll('select')].find(node => [...node.options].some(option => option.textContent?.includes(run.jobId) || option.value.includes(run.jobId)));
    expect(picker, 'retained original job picker').toBeTruthy();
    const option = [...picker!.options].find(node => node.textContent?.includes(run.jobId) || node.value.includes(run.jobId))!;
    await act(async () => { picker!.value = option.value; picker!.dispatchEvent(new Event('change', { bubbles: true })); });
    const clone = [...pane.querySelectorAll('button')].find(node => node.textContent === 'Clone selected original job');
    expect(clone, 'explicit clone action').toBeTruthy();
    await act(async () => clone!.click());
}
function noRobotRequests() {
    expect(requests.filter(request => request.url.startsWith('/api/bioxp/')).every(request => request.url === '/api/bioxp/workflows/clone')).toBe(true);
}
beforeEach(async () => {
    vi.stubGlobal('crypto', webcrypto); localStorage.clear(); requests = []; db = {}; delayClone = null; delaySave = null;
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const saved: any = { id: 'original-template', name: 'Original run settings', draft: {
        schema: 'bms.bioxp-workflow-draft.v2',
        deck_plan: { labware: [{ id: 'plate', station: 'LOC_MS', name: 'Original plate', profile_id: '' }], materials: [{ id: 'sample', name: 'Sample', kind: 'sample', description: '' }], assignments: [{ id: 'assignment', labware_id: 'plate', well: 'A1', material_id: 'sample', volume_ul: '030.00' }] },
        steps: [{ step_id: 'original-row', intent: { operation: 'aspirate', channels: [0], volume_ul: '010.00', speed: '2.50', future: { false_value: false, zero: 0, blank: '', null_value: null } } }],
        editor_state: { preserved: { future: false }, editing_step_id: 'original-row', edit_baseline: { operation: 'aspirate', channels: [0], volume_ul: '010.00', speed: '2.50' },
            form: { operation: 'aspirate', channels: [0], volume: '000.1250', aspirateSpeed: '2.50', deck: { station: 'LOC_MS', wells: ['A1'] } } },
    } };
    const key = 'fixture-original-run';
    run = { version: 1, key, jobId: await canonicalWorkflowJobId(key), generation: 7, saved,
        document: { protocol_id: 'fixture-source-document', metadata: { bms_saved_workflow: structuredClone(saved) } } };
    expect(retainPendingWorkflowRun(run)).toBeNull();
    api.defaults.adapter = async config => {
        const body = config.data ? JSON.parse(config.data) : undefined;
        const request = { method: config.method, url: config.url, body }; requests.push(request);
        let data: any;
        if (config.url === '/api/bioxp/workflows/clone') {
            if (delayClone) await delayClone;
            const source = body.document.metadata.bms_saved_workflow;
            if (source) data = { name: `${source.name} copy`, draft: { ...structuredClone(source.draft), editor_state: { ...source.draft.editor_state, cloned_from_job_id: body.job_id, cloned_from_workflow_id: source.id } }, issues: [] };
            else {
                // Known single-action native fixture; no browser production projection.
                const { channels, volume_ul, speed } = body.document.stages[0].actions[0].params;
                data = { name: null, draft: { schema: 'bms.bioxp-workflow-draft.v1', steps: [{ step_id: 'cloned-action-0', intent: { operation: 'aspirate', channels, volume_ul, speed } }], editor_state: { cloned_from_job_id: body.job_id } }, issues: [] };
            }
            exported.push({ ...request, expected: structuredClone(data) });
        } else if (config.url?.startsWith('/api/user-templates')) {
            if (config.method === 'post' || config.method === 'put') {
                if (delaySave) await delaySave;
                const id = config.method === 'post' ? `copy-${Object.keys(db).length + 1}` : config.url.split('/').at(-1)!;
                data = db[id] = { ...body, id }; exported.push(request);
            } else data = config.url === '/api/user-templates' ? Object.values(db) : db[config.url.split('/').at(-1)!];
        } else throw new Error(`Unexpected robot request ${config.url}`);
        return { data: structuredClone(data), status: 200, statusText: 'OK', config, headers: {} };
    };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = adapter; vi.unstubAllGlobals(); localStorage.clear(); });
afterAll(() => { if (process.env.BIOXP_JOB_CLONE_UI_EXPORT) writeFileSync(process.env.BIOXP_JOB_CLONE_UI_EXPORT, JSON.stringify({ fixture_only: true, requests: exported }, null, 2)); });

it.each(['changed', 'deleted'])('clones the original retained run when its template was %s, edits and saves a separate record, then cold reopens', async templateState => {
    const original = structuredClone(run), association = localStorage.getItem(pendingWorkflowRunStorageKey);
    if (templateState === 'changed') db['original-template'] = { id: 'original-template', name: 'Changed later', mode: 'bioxp_workflow', params: { schema: 'bms.bioxp-workflow-draft.v1', steps: [], editor_state: {} } };
    const templatesBefore = structuredClone(db);
    await mount(); expect(requests).toEqual([]); await cloneRetained();
    expect(input('Workflow name').value).toBe('Original run settings copy');
    expect(host.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('Build');
    expect(host.textContent).toContain('Cloned job as an unsaved workflow');
    expect(input('Volume').value).toBe('000.1250');
    expect(db).toEqual(templatesBefore); expect(requests).toHaveLength(1);
    await change('Volume', '000.2500'); await click('Update step'); await change('Workflow name', 'Edited job copy'); await click('Save workflow');
    const created = Object.values(db).find(row => row.name === 'Edited job copy')!;
    expect(created.id).not.toBe(original.saved.id);
    expect(created.params.steps[0].intent).toEqual({ ...original.saved.draft.steps[0].intent, volume_ul: '000.2500' });
    expect(created.params.deck_plan).toEqual((original.saved.draft as any).deck_plan);
    expect(created.params.editor_state).toMatchObject({ preserved: { future: false }, cloned_from_job_id: original.jobId, cloned_from_workflow_id: original.saved.id });
    expect(requests.filter(request => request.method === 'post' && request.url === '/api/user-templates')).toHaveLength(1);
    expect(requests.some(request => request.method === 'put')).toBe(false);
    expect(db['original-template']).toEqual(templatesBefore['original-template']);
    expect(run).toEqual(original); expect(localStorage.getItem(pendingWorkflowRunStorageKey)).toBe(association);
    await fresh(); await click('Open workflow'); await click('Edited job copy');
    await act(async () => (host.querySelector('[aria-label="Edit step 1"]') as HTMLButtonElement).click());
    expect(input('Volume').value).toBe('000.2500');
    await click('Review');
    expect(host.querySelector('[aria-label="Saved workflow run"]')?.textContent).toContain('Saved selection: Edited job copy');
    expect(localStorage.getItem(pendingWorkflowRunStorageKey)).toBe(association); noRobotRequests();
});

it('adopts a lossless native projection without a saved authoring name as a new editable workflow', async () => {
    const document = structuredClone(nativeFixture.document_template);
    document.protocol_id = 'Native clone transport fixture';
    document.stages[0].actions = [document.stages[0].actions[0]];
    run.document = document;
    expect(retainPendingWorkflowRun(run)).toBeNull();
    const retained = localStorage.getItem(pendingWorkflowRunStorageKey);
    await mount(); await cloneRetained();
    expect(input('Workflow name').value).toBe('Cloned job');
    await act(async () => (host.querySelector('[aria-label="Edit step 1"]') as HTMLButtonElement).click());
    await change('Volume', '002.500'); await click('Update step'); await click('Save workflow');
    const saved = Object.values(db)[0];
    expect(saved.params.steps[0].intent).toMatchObject({ operation: 'aspirate', volume_ul: '002.500' });
    expect(saved.params.editor_state.cloned_from_job_id).toBe(run.jobId);
    expect(localStorage.getItem(pendingWorkflowRunStorageKey)).toBe(retained);
    noRobotRequests();
});

it('does not let a late clone overwrite edits made after the clone started', async () => {
    let release!: () => void; delayClone = new Promise<void>(resolve => { release = resolve; });
    await mount(); await change('Workflow name', 'Before clone'); await cloneRetained();
    await change('Workflow name', 'Newer unsaved work');
    await act(async () => { release(); await delayClone; });
    expect(input('Workflow name').value).toBe('Newer unsaved work');
    expect(host.textContent).toContain('Editor changed while cloning');
    expect(db).toEqual({}); noRobotRequests();
});

it('does not replace the editor during an in-flight Save, even when the form text is unchanged', async () => {
    let finishClone!: () => void, finishSave!: () => void;
    delayClone = new Promise<void>(resolve => { finishClone = resolve; });
    delaySave = new Promise<void>(resolve => { finishSave = resolve; });
    await mount(); await change('Workflow name', 'My current draft'); await cloneRetained();
    await click('Save workflow');
    await act(async () => { finishClone(); await delayClone; });
    expect(input('Workflow name').value).toBe('My current draft');
    expect(host.textContent).not.toContain('Cloned job as an unsaved workflow');
    await act(async () => { finishSave(); await delaySave; });
    expect(Object.values(db)).toHaveLength(1); expect(Object.values(db)[0].name).toBe('My current draft');
    noRobotRequests();
});
