import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { api, type MolecularRevision, type PreparedRestrictionCloning, type SuggestRestrictionProposal, type SuggestRestrictionRequest, type SuggestRestrictionResponse } from '../../src/lib/api';
import { AssemblyPanel } from '../../src/components/MolBioToolkit/panels/AssemblyPanel';
import { SuggestPlan } from '../../src/components/MolBioToolkit/panels/SuggestPlan';

type Wire = {
    request: SuggestRestrictionRequest; suggestion: SuggestRestrictionProposal; suggested: SuggestRestrictionResponse;
    prepare_request: { request: SuggestRestrictionRequest; selection: SuggestRestrictionProposal['selection']; name: string; idempotency_key: string };
    prepared: PreparedRestrictionCloning; chooser: unknown;
    backbone_revision: MolecularRevision; insert_revision: MolecularRevision; simulation: unknown;
};
let wire: Wire;
let privateWire: string | undefined;
const adapter = api.defaults.adapter;
let root: Root, host: HTMLDivElement, query: QueryClient;
let calls: Array<{ url: string; body: unknown }>;
let unexpected: string[];
let failPrepare: boolean;
const seq = { name: 'Unrelated open linear molecule', sequence: 'ACGTACGT', circular: false, sequenceType: 'dna' as const, features: [], primers: [] };
const noop = () => {};

beforeAll(() => {
    let path = process.env.BMS_RESTRICTION_CLONING_WIRE;
    if (!path) {
        path = privateWire = mkdtempSync(join(tmpdir(), 'bms-restriction-receiving-'));
        const apiRoot = resolve(process.cwd(), '../api');
        const source = resolve(apiRoot, '../..');
        const run = spawnSync(process.env.BMS_TEST_PYTHON || 'python', ['-m', 'pytest',
            'tests/test_restriction_cloning_connected_api.py::test_revision_sources_circular_donor_and_advisory_phosphate',
            '-q', '-o', 'addopts=', '-p', 'no:cacheprovider', '--tb=short'], {
            cwd: apiRoot, timeout: 90_000, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024,
            env: { ...process.env, PYTHONPATH: [apiRoot, source].join(delimiter), BMS_RESTRICTION_CLONING_WIRE: path },
        });
        expect(run.status, `${run.error ?? ''}\n${run.stdout}\n${run.stderr}`).toBe(0);
    }
    // Exact real HTTP captures from migrated disposable SQLite, not invented science responses.
    wire = JSON.parse(readFileSync(join(path, 'pinned.json'), 'utf8')) as Wire;
}, 120_000);
afterAll(() => { if (privateWire) rmSync(privateWire, { recursive: true, force: true }); });

beforeEach(() => {
    calls = []; unexpected = []; failPrepare = false;
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    api.defaults.adapter = async (config) => {
        const url = config.url ?? '';
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        calls.push({ url, body });
        let data: unknown;
        if (url === '/api/molbio/chooser') data = wire.chooser;
        else if (url.includes(`/revisions/${wire.backbone_revision.revision_id}`)) data = wire.backbone_revision;
        else if (url.includes(`/revisions/${wire.insert_revision.revision_id}`)) data = wire.insert_revision;
        else if (url === '/api/sequences/assembly-workups') data = [];
        else if (url.endsWith('/restriction-cloning/suggest')) data = wire.suggested;
        else if (url.endsWith('/restriction-cloning/prepare')) {
            if (failPrepare) throw { response: { data: { detail: 'Injected transport failure' } } };
            data = wire.prepared;
        } else if (url === '/api/molbio/assembly/ligation/simulate') data = wire.simulation;
        else { unexpected.push(url); throw new Error(`Unexpected request ${url}`); }
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
});
afterEach(async () => {
    await act(async () => root.unmount()); query.clear(); host.remove(); api.defaults.adapter = adapter;
    expect(unexpected).toEqual([]);
});
async function render(node: React.ReactNode) {
    await act(async () => root.render(<QueryClientProvider client={query}>{node}</QueryClientProvider>));
}
async function click(label: string, first = false) {
    const matches = [...host.querySelectorAll('button')].filter(b => b.textContent?.trim() === label || b.textContent?.trim().startsWith(label + ' '));
    expect(matches.length, label).toBeGreaterThan(0);
    await act(async () => { matches[first ? 0 : matches.length - 1].click(); await new Promise(r => setTimeout(r, 10)); });
}
async function value(label: string, text: string) {
    const control = host.querySelector(`[aria-label="${label}"]`) ?? [...host.querySelectorAll('label')]
        .find(l => l.firstChild?.textContent?.trim() === label)?.querySelector('input,select');
    expect(control, label).toBeTruthy();
    await act(async () => {
        const prototype = control instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(control, text);
        control!.dispatchEvent(new Event(control instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
    });
}
async function authoredPlan() {
    await click('Suggest plan'); await click('Restriction-ligation');
    await value('Target molecule', 'saved'); await click('Choose target'); await click('Backbone fixture');
    await click('Add saved source'); await click('Donor fixture');
    await value('Enzymes (optional, comma-separated)', wire.request.enzymes!.join(', '));
    await value('Donor payload start', String(wire.request.insert_start));
    await value('Donor payload end', String(wire.request.insert_end));
    await value('Vector 5′ phosphate', 'unphosphorylated');
    await click('Suggest');
}

describe('Restriction proposal → native preparation → ordinary Ligation receiving', () => {
    it('serializes saved sources and payload intent, then keeps physical ends and the selected circular topology', async () => {
        await render(<AssemblyPanel sequenceData={seq} selection={null} selectedSequenceId={null} onLoadProduct={noop} onLoadSavedWorkup={noop} />);
        await authoredPlan();
        const sent = calls.find(c => c.url.endsWith('/restriction-cloning/suggest'))!.body;
        expect(sent).toEqual(wire.request);
        await click('Prepare fragments and use this plan', true);
        const prepare = calls.find(c => c.url.endsWith('/restriction-cloning/prepare'))!.body as typeof wire.prepare_request;
        expect({ ...prepare, idempotency_key: wire.prepare_request.idempotency_key }).toEqual(wire.prepare_request);
        expect(prepare.idempotency_key).toMatch(/^[0-9a-f-]{36}$/);
        await click('Ligation'); await click('Simulate');
        const payload = calls.find(c => c.url === '/api/molbio/assembly/ligation/simulate')!.body;
        // The server emits the optional description as null; JSON omits the untouched form's undefined.
        expect({ save_description: null, ...(payload as Record<string, unknown>) }).toEqual(wire.prepared.assembly_request);
        expect((payload as { circular: boolean }).circular).toBe(true); // not the unrelated open molecule's topology
    });

    it('retries one preparation identity and keeps the chosen plan frozen when the authoring form changes', async () => {
        const received: unknown[] = [];
        await render(<SuggestPlan sequenceData={seq} selectedSequenceId={null} onUseGibson={noop} onUseGoldenGate={noop} onUseRestriction={request => received.push(request)} />);
        await authoredPlan();
        await value('Donor payload end', String(Number(wire.request.insert_end) + 1));
        failPrepare = true; await click('Prepare fragments and use this plan', true);
        expect(host.textContent).toContain('Injected transport failure');
        failPrepare = false; await click('Prepare fragments and use this plan', true);
        const attempts = calls.filter(c => c.url.endsWith('/restriction-cloning/prepare')).map(c => c.body as typeof wire.prepare_request);
        expect(attempts).toHaveLength(2);
        expect(attempts[0]).toEqual(attempts[1]);
        expect(attempts[1].request).toEqual(wire.request);
        expect(attempts[1].selection).toEqual(wire.suggestion.selection);
        expect(received).toEqual([wire.prepared.assembly_request]);
    });
});
