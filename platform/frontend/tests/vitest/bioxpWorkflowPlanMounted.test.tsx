import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { webcrypto } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { afterAll, afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpWorkflowEditor } from '../../src/components/BioXpWorkflowEditor';
import { api } from '../../src/lib/api';
import { deckStations } from '../../src/lib/bioxpWorkflowDeck';
import { readWorkflowDraft } from '../../src/lib/bioxpWorkflowDraft';

let host: HTMLDivElement, root: Root, client: QueryClient;
let requests: any[], db: Record<string, any>, previewReply: any, previewFailure: boolean;
let delayWrite: (() => Promise<void>) | null;
const adapter = api.defaults.adapter, exported: any[] = [];
const source = deckStations.find(s => s.locationId !== null && s.wells.length === 96)!;
const destination = deckStations.find(s => s.locationId !== null && s.wells.length === 96 && s.id !== source.id)!;
async function mount(connected = false) { await act(async () => root.render(<QueryClientProvider client={client}><BioXpWorkflowEditor generation={12} connected={connected} controlsEnabled={connected} /></QueryClientProvider>)); }
async function fresh() { await act(async () => root.unmount()); root = createRoot(host); await mount(); }
function button(text: string) { const el = [...host.querySelectorAll('button')].find(e => e.textContent === text); expect(el, text).toBeTruthy(); return el!; }
async function click(text: string) { await act(async () => button(text).click()); }
function input(label: string): HTMLInputElement | HTMLSelectElement {
    const transferField = [...host.querySelectorAll('[aria-label="Transfer editor"] label')].find(e => [...e.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join('') === label)?.querySelector('input,select');
    const aria = transferField || host.querySelector(`[aria-label="${label}"]`);
    const wrapped = [...host.querySelectorAll('label')].find(e => [...e.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join('') === label)?.querySelector('input,select');
    expect(aria || wrapped, label).toBeTruthy(); return (aria || wrapped) as HTMLInputElement | HTMLSelectElement;
}
async function change(label: string, value: string) {
    const el = input(label);
    await act(async () => { Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
}
async function selectWell(station: string, well: string) {
    await change('Deck station', station);
    await act(async () => (host.querySelector(`.bioxp-build-grid [data-station="${station}"][data-well="${well}"]`) as unknown as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true })));
}
async function compose() {
    await change('Workflow name', 'Paired material transfer');
    await change('Step to append', 'transfer');
    await selectWell(source.id, 'B2'); await click('Use deck selection as source');
    await act(async () => { (host.querySelector('.bioxp-materials-disclosure') as HTMLDetailsElement).open = true; });
    await click('Add labware at selected station'); await change('Labware 1 name', 'Source plate');
    await click('Add sample'); await change('Material 1 name', 'Sample A');
    await change('Planned amount per selected well (µL)', '030.00'); await click('Assign material to selected wells');
    await selectWell(destination.id, 'C3'); await click('Use deck selection as destination');
    await change('Volume per channel (µL)', '010.00'); await change('Aspirate speed', '2.50'); await change('Dispense speed', '3.00');
    await change('Source move Z position', '0'); await change('Destination move Z position', '2');
    await change('Source lift target', 'high'); await change('Destination lift height (steps above calibrated low)', '0');
    await act(async () => (host.querySelector('[aria-label="Transfer editor"] input[type=checkbox]') as HTMLInputElement).click());
    await click('Add step');
}
beforeEach(() => {
    vi.stubGlobal('crypto', webcrypto); localStorage.clear(); requests = []; db = {}; previewFailure = false; delayWrite = null;
    previewReply = { document: null, actions: [], issues: [{ step_id: 'incomplete', message: 'Select explicit transfer fields.' }] };
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    api.defaults.adapter = async config => {
        const body = config.data ? JSON.parse(config.data) : undefined;
        requests.push({ method: config.method, url: config.url, body });
        let data: any;
        if (config.url === '/api/bioxp/workflows/preview') {
            if (previewFailure) throw new Error('Preview service unavailable');
            data = previewReply;
        } else if (config.url?.startsWith('/api/user-templates')) {
            if (config.method === 'post' || config.method === 'put') {
                if (delayWrite) await delayWrite();
                const id = config.method === 'post' ? `saved-${Object.keys(db).length + 1}` : config.url.split('/').at(-1)!;
                data = db[id] = { ...body, id }; exported.push({ method: config.method, url: config.url, body });
            } else data = config.url === '/api/user-templates' ? Object.values(db) : db[config.url.split('/').at(-1)!];
        } else throw new Error(`Unexpected robot request ${config.url}`);
        return { data: structuredClone(data), status: 200, statusText: 'OK', config, headers: {} };
    };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = adapter; vi.unstubAllGlobals(); localStorage.clear(); });
afterAll(() => { if (process.env.BIOXP_WORKFLOW_PLAN_UI_EXPORT) writeFileSync(process.env.BIOXP_WORKFLOW_PLAN_UI_EXPORT, JSON.stringify({ fixture_only: true, requests: exported }, null, 2)); });

it('integrates real deck, Materials and Transfer, saves v2, cold reopens, and clones without updating the original', async () => {
    await mount(); await compose(); await click('Save workflow');
    const original = structuredClone(db['saved-1']);
    expect(original.params.schema).toBe('bms.bioxp-workflow-draft.v2');
    expect(original.params.deck_plan.assignments[0]).toMatchObject({ well: 'B2', volume_ul: '030.00' });
    expect(original.params.steps[0].intent).toMatchObject({ operation: 'transfer', source: { station: source.id, wells: ['B2'] }, destination: { station: destination.id, wells: ['C3'] }, channels: [0], volume_ul: '010.00', source_lift_height_steps: null, destination_lift_height_steps: '0' });
    await fresh(); await click('Open workflow'); await click(original.name);
    await act(async () => (host.querySelector('[aria-label="Edit step 1"]') as HTMLButtonElement).click());
    expect(input('Volume per channel (µL)').value).toBe('010.00');
    expect(host.querySelector('[aria-label="Transfer pairs"]')?.textContent).toBe('B2 → C3');
    await click('Clone workflow'); await click('Save workflow');
    expect(db['saved-1']).toEqual(original);
    expect(db['saved-2'].params.steps).toEqual(original.params.steps);
    expect(db['saved-2'].params.deck_plan).toEqual(original.params.deck_plan);
    expect(requests.every(r => r.url.startsWith('/api/user-templates'))).toBe(true);
});

it('previews only on explicit request, scrubs effective native fields and highlights on the actual map without a robot job', async () => {
    await mount(); await compose(); await click('Review'); expect(requests).toEqual([]);
    const row = host.querySelector('[data-step-id]')!.getAttribute('data-step-id');
    const actions = [
        { index: 0, step_id: row, pair_index: 0, kind: 'move_to', params: { location_id: source.locationId, well: 'B2', position_flag: 0 }, label: 'Move source', station: source.id, well: 'B2' },
        { index: 1, step_id: row, pair_index: 0, kind: 'move_to', params: { location_id: destination.locationId, well: 'C3', position_flag: 2 }, label: 'Move destination', station: destination.id, well: 'C3' },
    ];
    // Transport fixture verifies UI interpretation, not native compiler or hardware behavior.
    previewReply = { document: { protocol_id: 'fixture', actions }, actions, issues: [] };
    await click('Preview workflow');
    expect(requests).toHaveLength(1); expect(requests[0].url).toBe('/api/bioxp/workflows/preview');
    expect(requests[0].body.draft.steps[0].intent.volume_ul).toBe('010.00');
    const review = host.querySelector('[aria-label="Workflow review"]')!;
    expect(review.querySelector(`[data-station="${source.id}"][data-well="B2"]`)?.getAttribute('aria-pressed')).toBe('true');
    await click('Next native action');
    expect(review.querySelector(`[data-station="${destination.id}"][data-well="C3"]`)?.getAttribute('aria-pressed')).toBe('true');
    expect(JSON.parse(review.querySelector('[aria-label="Effective native fields"]')!.textContent!)).toEqual({ kind: 'move_to', params: actions[1].params });
    await click('Back to Build'); await change('Workflow name', 'Changed draft'); await click('Review');
    expect(review.textContent).toContain('earlier draft snapshot'); expect(requests).toHaveLength(1);
});

it('shows incomplete/native preview errors and clears old results on network failure without fabricated actions', async () => {
    await mount(); await change('Step to append', 'transfer'); await click('Add step'); await click('Review'); await click('Preview workflow');
    expect(host.textContent).toContain('Select explicit transfer fields.');
    expect(host.querySelector('[aria-label="Native action scrubber"]')).toBeNull();
    previewFailure = true; await click('Preview workflow');
    expect(host.textContent).toContain('Preview service unavailable'); expect(host.querySelector('[aria-label="Logical native preview"]')).toBeNull();
    expect(requests.map(r => r.url)).toEqual(['/api/bioxp/workflows/preview', '/api/bioxp/workflows/preview']);
});

it('preserves unknown/null/omitted transfer fields and unapplied pending editor values through save and reopen', async () => {
    const intent = { operation: 'transfer', source: { station: source.id, location_id: source.locationId, wells: ['B2'], unknown: false }, destination: null, channels: [], volume_ul: null, unknown: { retained: false } };
    db.raw = { id: 'raw', name: 'Raw transfer', mode: 'bioxp_workflow', model_id: null, base_template_id: null, params: { schema: 'bms.bioxp-workflow-draft.v2', deck_plan: { labware: [], materials: [], assignments: [] }, editor_state: { extra: null }, steps: [{ step_id: 'raw-step', intent }] } };
    expect(readWorkflowDraft(db.raw.params)).toBe(db.raw.params);
    await mount(); await click('Open workflow'); await click('Raw transfer');
    await act(async () => (host.querySelector('[aria-label="Edit step 1"]') as HTMLButtonElement).click());
    await click('Update step'); await click('Save workflow'); expect(db.raw.params.steps[0].intent).toEqual(intent);
    await act(async () => (host.querySelector('[aria-label="Edit step 1"]') as HTMLButtonElement).click());
    await change('Volume per channel (µL)', '000.1250'); await click('Save workflow');
    expect(db.raw.params.steps[0].intent).toEqual(intent);
    await fresh(); await click('Open workflow'); await click('Raw transfer');
    expect(input('Volume per channel (µL)').value).toBe('000.1250'); await click('Update step'); await click('Save workflow');
    expect(db.raw.params.steps[0].intent).toEqual({ ...intent, volume_ul: '000.1250' });
});

it('passes live props and exact saved snapshot to explicit Run while keeping unsaved transfer edits out of submission', async () => {
    await mount(true); await compose(); await click('Save workflow');
    const saved = structuredClone(db['saved-1']);
    await act(async () => (host.querySelector('[aria-label="Edit step 1"]') as HTMLButtonElement).click());
    await change('Volume per channel (µL)', '999'); await click('Update step');
    await click('Review');
    previewReply = { document: { protocol_id: 'fixture-only', actions: [] }, actions: [], issues: [] };
    expect(requests.some(r => r.url === '/api/bioxp/protocols/submit')).toBe(false);
    await act(async () => (host.querySelector('[aria-label="Saved workflow run"] input[type=checkbox]') as HTMLInputElement).click());
    expect(button('Run saved workflow').disabled).toBe(false);
    // The adapter records and rejects every robot URL; there is no controller transport.
    await click('Run saved workflow');
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
    const submitted = requests.filter(r => r.url === '/api/bioxp/protocols/submit');
    expect(submitted).toHaveLength(1);
    expect(submitted[0].body.expected_connection_generation).toBe(12);
    expect(submitted[0].body.document.metadata.bms_saved_workflow).toEqual({ id: saved.id, name: saved.name, draft: saved.params });
    expect(requests.find(r => r.url === '/api/bioxp/workflows/preview').body.draft).toEqual(saved.params);
    await click('Back to Build'); await click('Review');
    expect(requests.filter(r => r.url === '/api/bioxp/protocols/submit')).toHaveLength(1);
});

it('retains later edits during Save and exposes only the exact saved readback to the real Run component', async () => {
    await mount(); await compose();
    let release!: () => void; const waiting = new Promise<void>(resolve => { release = resolve; }); delayWrite = () => waiting;
    await act(async () => button('Save workflow').click());
    await change('Workflow name', 'Newer unsaved name');
    await act(async () => { release(); await waiting; });
    expect(host.textContent).toContain('Newer editor changes are not saved.');
    await click('Review');
    expect(host.querySelector('[aria-label="Saved workflow run"]')?.textContent).toContain('Saved selection: Paired material transfer');
    expect(input('Workflow name').value).toBe('Newer unsaved name');
    expect(button('Run saved workflow').disabled).toBe(true);
    await click('Clone workflow'); expect(host.querySelector('[aria-label="Saved workflow run"]')?.textContent).toContain('Save or open a workflow');
});
