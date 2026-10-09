import React, { act } from 'react';
import cloneSchema from '../../../../schemas/ngs_molbio/ngs-ont-clone_validation-v1.schema.json';
import doradoLock from '../../../../config/ngs/dorado_v2.1.2.lock.json';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { InternalAxiosRequestConfig } from 'axios';
import { api, prepareOntNgsJob, submitOntNgsJob, type Job, type OntNgsPreparedReview, type OntNgsSubmitRequest } from '../../src/lib/api';
import { normalizeNanoporeCloneState } from '../../src/lib/nanoporeCloneState';
import { NanoporeTemplate } from '../../src/components/NanoporeTemplate';

// The mounted form, shared plan dialog, placement/policy controls and API
// helpers/interceptors are real. Only transport and surrounding workspace/GPU
// discovery are doubled: no service or scientific execution is contacted.
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    useGlobalExperimentContext: () => ({
        workspaceId: 'workspace-1', globalExperimentId: 'experiment-1', stateRevisionId: 'state-1',
        selectedDomainExperiment: { domain_experiment_id: 'domain-1' },
        availability: { canMutateDomain: true, reason: '' }, contextHref: (path: string) => path,
    }),
}));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({
    useLiveGpuCatalog: () => ({ gpuOptions: [{ index: 2, label: 'GPU 2' }] }),
}));

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
const originalAdapter = api.defaults.adapter;
type Call = { url: string; body: OntNgsSubmitRequest; context: unknown };
let calls: Call[];
let unexpected: string[];
let prepare: (body: OntNgsSubmitRequest, alias: string) => Promise<OntNgsPreparedReview>;
const snapshot = { relative_path: `${'a'.repeat(64)}/${'b'.repeat(64)}/launch-${'c'.repeat(32)}.fastq`, sha256: 'b'.repeat(64), size_bytes: 23 };
const launchId = '11111111-1111-4111-8111-111111111111';
const digest = 'd'.repeat(64);
function prepared(body: OntNgsSubmitRequest, alias: string): OntNgsPreparedReview {
    return {
        workflow_id: alias,
        request: { ...body, name: 'server-stable-name', execution_plan_approval: null,
            ...(['fastq_qc', 'ont_fastq_qc'].includes(alias) ? { fastq_snapshot: snapshot } : {}),
            ...(body.managed_reference ? { managed_reference: { ...body.managed_reference, launch_snapshot_id: launchId } } : {}),
            ...(body.params.ngs_comparison_panel_receipt_id ? { comparison_launch_id: launchId } : {}),
        },
        preview: {
            schema: 'bms.job.execution-preview.v1', approval_digest: digest, admissible: true,
            request: { model_id: 'nanopore', mode: 'basecall_dna', execution_target_id: body.execution_target_id,
                launch_context_id: 'server-context' },
            plan: { requested_json: body.params, effective_json: { ...body.params, selected_native_profile: 'transport-fixture-profile', declaration_basis: 'operator-declared' },
                source_identity: { revision: 'fixture-source', tree: 'fixture-tree' },
                metadata: { static_components: [{ component_key: 'native-ngs' }], dynamic_templates: [], external_services: [] } },
            deferred_preparation: [], blockers: [],
        },
    };
}
const reference = { reference_id: 'reference-1', id: 'reference-revision-1', name: 'Reference one',
    global_domain_experiment_id: 'domain-1', revision_number: 1, canonical_fasta_sha256: 'a'.repeat(64),
    molecule_type: 'dna', topology: 'circular' };
const molecular = { id: 'sequence-1', name: 'Saved sequence', sequence_type: 'dna' };
const revision = { id: 'revision-1', revision_id: 'revision-1', sequence_id: 'sequence-1', revision_number: 1, content_length: 100, content_sha256: 'a'.repeat(64) };

beforeEach(() => {
    calls = []; unexpected = []; prepare = async (body, alias) => prepared(body, alias);
    window.history.replaceState({}, '', '/?launch_context_id=ambient-context');
    window.sessionStorage.clear(); window.localStorage.clear();
    api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
        const url = config.url ?? '';
        const body = config.data ? JSON.parse(config.data as string) : {};
        calls.push({ url, body, context: config.headers.get('X-BMS-Launch-Context-ID') });
        let data: unknown;
        if (url.endsWith('/prepare')) data = await prepare(body, url.split('/').at(-2)!);
        else if (url.endsWith('/submit')) data = { id: 'new-job' };
        else if (url === '/api/execution-targets') data = [{ id: 'worker-1', name: 'Test worker', active: true, state: 'ready', capabilities: { gpu_count: 1, gpu_name: 'fixture' } }];
        else if (url === '/api/sequences/') data = [molecular];
        else if (url.includes('/state/revisions/')) data = { id: 'state-1', members: [
            { role: 'ngs_reference', entity_kind: 'ngs_reference_revision', entity_id: 'reference-revision-1' },
            { role: 'input', entity_kind: 'molecular_revision', entity_id: 'revision-1', reopen_destination: { params: { sequence_id: 'sequence-1', revision_id: 'revision-1' } } },
        ] };
        else if (url.endsWith('/summaries/reference')) data = { items: [reference], total: 1, next_cursor: null };
        else if (url.endsWith('/revisions')) data = [revision];
        else if (url.endsWith('/ngs-receipts')) data = { receipt_id: 'new-receipt', sequence_id: 'sequence-1', revision_id: 'revision-1' };
        else if (url === '/api/files/browse') data = { path: '/data', parent: '/', entries: [{ name: 'picked-primers.fasta', path: 'data/picked-primers.fasta', is_directory: false }] };
        else { unexpected.push(url); throw new Error(`Unexpected transport: ${url}`); }
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/receipts')
        ? { receipt_id: 'new-panel-receipt' }
        : { panels: [{ id: 'panel-1', version: 1, status: 'APPROVED', label: 'Panel one', snapshot_sha256: 'e'.repeat(64) }] } })));
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(() => {
    act(() => root.unmount()); container.remove(); client.clear(); api.defaults.adapter = originalAdapter; vi.unstubAllGlobals();
    expect(unexpected).toEqual([]);
});
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); }
async function until(assertion: () => void) { await vi.waitFor(async () => { await settle(); assertion(); }); }
function button(text: string) { return Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(node => node.textContent?.trim() === text)!; }
async function click(text: string) { expect(button(text), text).toBeTruthy(); await act(async () => button(text).click()); await settle(); }
async function input(selector: string, value: string) {
    const node = container.querySelector<HTMLInputElement | HTMLSelectElement>(selector)!;
    expect(node, selector).toBeTruthy();
    await act(async () => {
        Object.getOwnPropertyDescriptor(node instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(node, value);
        node.dispatchEvent(new Event(node instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
    });
    await settle();
}
const base = { selectedWorkflow: 'dna', inputSource: 'pod5', pod5Dir: '/data/pod5', jobName: 'operator-name', doradoModel: 'sup', runAssembly: false, runFastqQc: false };
async function mount(values: Record<string, unknown> = base) {
    await act(async () => root.render(React.createElement(QueryClientProvider, { client }, <MemoryRouter><NanoporeTemplate onBack={() => {}} initialValues={values} /></MemoryRouter>)));
    await until(() => expect(button('Review and submit')?.disabled).toBe(false));
}
const prepares = () => calls.filter(call => call.url.endsWith('/prepare'));
const submits = () => calls.filter(call => call.url.endsWith('/submit'));
async function remoteReview() { await click('Vast · Test worker'); await click('Review and submit'); await until(() => expect(button('Approve and submit')).toBeTruthy()); }

describe('ordinary NGS prepared launch through mounted consumers and real API transport', () => {
    it.each([['simplex', '64 (default)'], ['duplex', '32 (default)']])('preserves the %s default when batch is omitted', async (doradoMode, placeholder) => {
        await mount({ ...base, doradoMode, duplexPairs: '/data/pairs.txt' }); await click('Show advanced controls');
        const control = container.querySelector<HTMLInputElement>('input[aria-label="Dorado batch size"]')!;
        expect(control.value).toBe(''); expect(control.placeholder).toBe(placeholder);
        await click('Review and submit'); await until(() => expect(submits()).toHaveLength(1));
        expect(submits()[0].body.params).not.toHaveProperty('dorado_batch_size');
    });
    it.each([0, 1, 257, 4096])('edits native batch %s and submits it without a copied maximum', async value => {
        await mount({ ...base, batchSize: 0 });
        await click('Show advanced controls');
        const selector = 'input[aria-label="Dorado batch size"]';
        expect(container.querySelector<HTMLInputElement>(selector)!.value).toBe('0');
        expect(container.querySelector(selector)!.hasAttribute('max')).toBe(false);
        await input(selector, String(value));
        await click('Review and submit');
        await until(() => expect(submits()).toHaveLength(1));
        expect(submits()[0].body.params.dorado_batch_size).toBe(value);
    });
    it.each(['-1', '1.5'])('keeps invalid batch %s visible without silently truncating it', async value => {
        await mount(); await click('Show advanced controls');
        const selector = 'input[aria-label="Dorado batch size"]';
        await input(selector, value);
        expect(container.querySelector<HTMLInputElement>(selector)!.value).toBe(value);
        expect(container.querySelector(selector)!.getAttribute('aria-invalid')).toBe('true');
        expect(button('Review and submit').disabled).toBe(true);
        expect(submits()).toHaveLength(0);
        await input(selector, '');
        expect(container.querySelector<HTMLInputElement>(selector)!.placeholder).toBe('64 (default)');
        await click('Review and submit'); await until(() => expect(submits()).toHaveLength(1));
        expect(submits()[0].body.params).not.toHaveProperty('dorado_batch_size');
    });
    const profileSelect = 'select[aria-label="Declared clone consensus profile"]';
    const cloneBase = { ...base, selectedWorkflow: 'clone', runAssembly: true, ngsReferenceRevisionId: 'reference-revision-1' };
    async function quality(value: string) {
        const label = value === 'hac' ? 'High Accuracy (HAC)' : value === 'sup' ? 'Super Accurate (SUP)' : 'Fast';
        const node = Array.from(container.querySelectorAll('button')).find(node => node.textContent?.includes(label))!;
        await act(async () => node.click()); await settle();
    }
    function capturedRequest() {
        // Exact Axios wire request, not a fabricated native execution result.
        console.log('NGS_OPERATOR_REQUEST ' + JSON.stringify({ url: submits().at(-1)!.url, request: submits().at(-1)!.body }));
        return submits().at(-1)!.body;
    }
    it.each(cloneSchema.properties.wf_clone_basecaller_model.enum)('edits and reopens the exact native declaration %s', async (model) => {
        await mount({ ...cloneBase, inputSource: 'fastq', fastqPath: '/data/reads.fastq' });
        const select = container.querySelector<HTMLSelectElement>(profileSelect)!;
        expect(Array.from(select.options).map(option => option.value)).toEqual(cloneSchema.properties.wf_clone_basecaller_model.enum);
        expect(select.value).toBe(cloneSchema.properties.wf_clone_basecaller_model.default);
        expect(container.textContent).toContain('not proof of historical read origin');
        expect(container.textContent).toContain('medaka=2.2.2');
        await input(profileSelect, model);
        await click('Review and submit'); await until(() => expect(submits()).toHaveLength(1));
        const request = capturedRequest();
        expect(request.params.wf_clone_basecaller_model).toBe(model);
        const saved = JSON.parse(JSON.stringify(normalizeNanoporeCloneState({ name: request.name, params: { ...request.params, ont_workflow_id: 'wf_clone_validation' } } as unknown as Job)));
        expect(saved.wfCloneBasecallerModel).toBe(model);
        await act(async () => root.unmount()); root = createRoot(container);
        await mount({ ...saved, ngsReferenceRevisionId: 'reference-revision-1' });
        expect(container.querySelector<HTMLSelectElement>(profileSelect)!.value).toBe(model);
        await click('Review and submit'); await until(() => expect(submits()).toHaveLength(2));
        expect(capturedRequest().params.wf_clone_basecaller_model).toBe(model);
    });
    it.each(['hac', 'sup', 'fast'])('defaults undeclared POD5 %s without inventing FAST compatibility', async mode => {
        await mount({ ...cloneBase, doradoModel: mode });
        const model = mode === 'fast' ? cloneSchema.properties.wf_clone_basecaller_model.default
            : doradoLock.models.dna[mode as 'hac' | 'sup'].id;
        expect(container.querySelector<HTMLSelectElement>(profileSelect)!.value).toBe(model);
        if (mode === 'fast') expect(container.textContent).toContain('FAST has no native Medaka mapping');
        await click('Review and submit'); await until(() => expect(submits()).toHaveLength(1));
        expect(capturedRequest().params).toMatchObject({ dorado_quality_mode: mode, wf_clone_basecaller_model: model });
    });
    it('changes the absent-declaration recommendation but preserves explicit historical declarations across quality and remote review edits', async () => {
        await mount({ ...cloneBase, doradoModel: 'hac' });
        await quality('sup');
        expect(container.querySelector<HTMLSelectElement>(profileSelect)!.value).toBe(doradoLock.models.dna.sup.id);
        const historical = cloneSchema.properties.wf_clone_basecaller_model.enum.at(-1)!;
        await input(profileSelect, historical); await quality('fast');
        expect(container.querySelector<HTMLSelectElement>(profileSelect)!.value).toBe(historical);
        await remoteReview();
        expect(prepares()[0].body.params.wf_clone_basecaller_model).toBe(historical);
        await input(profileSelect, doradoLock.models.dna.hac.id);
        await until(() => expect(button('Approve and submit')).toBeUndefined());
        await click('Review and submit'); await until(() => expect(button('Approve and submit')).toBeTruthy());
        await click('Approve and submit'); await until(() => expect(submits()).toHaveLength(1));
        expect(capturedRequest().params).toMatchObject({ dorado_quality_mode: 'fast', wf_clone_basecaller_model: doradoLock.models.dna.hac.id });
    });
    it.each(['dna', 'rna'].flatMap(molecule => ['fast', 'hac', 'sup'].map(mode => [molecule, mode])))('preserves %s %s and displays its exact current source pin', async (molecule, mode) => {
        await mount({ ...base, selectedWorkflow: molecule, doradoMolecule: molecule, doradoModel: mode });
        expect(container.textContent).toContain(doradoLock.models[molecule as 'dna' | 'rna'][mode as 'fast' | 'hac' | 'sup'].id);
        expect(container.textContent).toContain(doradoLock.dorado.version);
        await click('Review and submit'); await until(() => expect(submits()).toHaveLength(1));
        expect(capturedRequest().params).toMatchObject({ ont_molecule_type: molecule, dorado_quality_mode: mode });
        expect(submits()[0].body.params).not.toHaveProperty('wf_clone_basecaller_model');
    });
    it('reopens a saved POD5 historical declaration without replacing it with the current basecaller', async () => {
        const historical = cloneSchema.properties.wf_clone_basecaller_model.enum[2];
        const saved = JSON.parse(JSON.stringify(normalizeNanoporeCloneState({ name: 'historical-pod5', params: {
            ont_workflow_id: 'wf_clone_validation', pod5_dir: '/data/pod5', run_assembly: true,
            dorado_quality_mode: 'hac', wf_clone_basecaller_model: historical,
        } } as unknown as Job)));
        await mount({ ...saved, ngsReferenceRevisionId: 'reference-revision-1' });
        expect(container.querySelector<HTMLSelectElement>(profileSelect)!.value).toBe(historical);
        await quality('sup');
        expect(container.querySelector<HTMLSelectElement>(profileSelect)!.value).toBe(historical);
        await click('Review and submit'); await until(() => expect(submits()).toHaveLength(1));
        expect(capturedRequest().params).toMatchObject({ dorado_quality_mode: 'sup', wf_clone_basecaller_model: historical });
    });
    it('submits the profile through optional construct assembly with the retained assembler controls', async () => {
        const historical = cloneSchema.properties.wf_clone_basecaller_model.enum.at(-1)!;
        await mount({ ...cloneBase, selectedWorkflow: 'constructScreening', inputSource: 'fastq', fastqPath: '/data/reads.fastq',
            wfCloneBasecallerModel: historical, assemblyTool: 'canu', wfCloneCanuFast: true, assemblyMinQuality: 0,
            wfCloneFlyeQuality: 'nano-raw', wfCloneNonUniformCoverage: false });
        expect(container.querySelector<HTMLSelectElement>(profileSelect)!.value).toBe(historical);
        await click('Review and submit'); await until(() => expect(submits()).toHaveLength(1));
        expect(capturedRequest().params).toMatchObject({ run_assembly: true, wf_clone_basecaller_model: historical,
            wf_clone_assembly_tool: 'canu', wf_clone_canu_fast: true, wf_clone_min_quality: 0,
            wf_clone_flye_quality: 'nano-raw', wf_clone_non_uniform_coverage: false });
    });
    it('preserves barcode and sample-sheet controls', async () => {
        await mount({ ...base, selectedWorkflow: 'barcode', barcodeKit: 'SQK-RBK114-96', sampleSheet: '/data/samples.csv' });
        await click('Review and submit'); await until(() => expect(submits()).toHaveLength(1));
        expect(capturedRequest().params).toMatchObject({ barcode_kit: 'SQK-RBK114-96', sample_sheet: '/data/samples.csv' });
    });
    it('retains explicit duplex settings and stereo source identity', async () => {
        await mount({ ...base, selectedWorkflow: 'duplex', doradoMode: 'duplex', duplexPairs: '/data/pairs.txt' });
        expect(container.textContent).toContain(doradoLock.models.stereo.id);
        await click('Review and submit'); await until(() => expect(submits()).toHaveLength(1));
        expect(capturedRequest().params).toMatchObject({ dorado_basecall_mode: 'duplex', duplex_pairs: '/data/pairs.txt', emit_moves: false });
    });
    it.each(['5mC_5hmC', '6mA'])('preserves the native modification branch %s', async modification => {
        await mount({ ...base, selectedWorkflow: 'modified', doradoModel: 'hac', modifiedBases: modification, runModkit: true, ngsReferenceRevisionId: 'reference-revision-1' });
        expect(container.textContent).toContain(doradoLock.models.modified_bases[modification as '5mC_5hmC' | '6mA'].id);
        await click('Review and submit'); await until(() => expect(submits()).toHaveLength(1));
        expect(capturedRequest().params).toMatchObject({ modified_bases: modification, dorado_quality_mode: 'hac', run_modkit: true });
    });

    it('keeps Local reference-free, grouping context and all 11 choices; no remote preparation', async () => {
        await mount({ ...base, doradoModel: 'fast', batchSize: 0, emitSummary: false, emitMoves: false });
        expect(container.querySelectorAll('[data-ngs-workflow-key]')).toHaveLength(11);
        for (const quality of ['Super Accurate (SUP)', 'High Accuracy (HAC)', 'Fast']) {
            expect(Array.from(container.querySelectorAll('button')).some(node => node.textContent?.includes(quality))).toBe(true);
        }
        expect(container.textContent).toContain('Reference-free basecalling is supported');
        expect(button('Local').getAttribute('aria-pressed')).toBe('true');
        await click('Review and submit');
        await until(() => expect(submits()).toHaveLength(1));
        expect(prepares()).toHaveLength(0);
        expect(submits()[0]).toMatchObject({ url: '/api/ont/ngs/ont_basecall_dna/submit', context: 'ambient-context', body: { execution_target_id: null, params: { dorado_quality_mode: 'fast', dorado_batch_size: 0, emit_summary: false, emit_moves: false } } });
        expect(submits()[0].body).not.toHaveProperty('managed_reference');
        expect(submits()[0].body).not.toHaveProperty('execution_plan_approval');
    });

    it('prepares existing POD5 remotely, reuses one review after cancel, and submits exact retained server request/context', async () => {
        await mount({ ...base, pinned_gpu: 2, execution_policy: { remote_result_policy: 'automatic' } });
        await remoteReview();
        expect(prepares()[0].body).toMatchObject({ execution_target_id: 'worker-1', pinned_gpu: null, execution_policy: { remote_result_policy: 'automatic' } });
        expect(document.body.textContent).toContain('transport-fixture-profile');
        expect(submits()).toHaveLength(0);
        await click('Cancel');
        await click('Review and submit');
        await until(() => expect(button('Approve and submit')).toBeTruthy());
        expect(prepares()).toHaveLength(1);
        await click('Approve and submit');
        await until(() => expect(submits()).toHaveLength(1));
        expect(submits()[0].body).toEqual({ ...prepared(prepares()[0].body, 'ont_basecall_dna').request, execution_plan_approval: digest });
        expect(submits()[0].context).toBe('server-context');
    });

    it('invalidates the prepared reference when explicitly switching to reference-free basecalling', async () => {
        await mount({ ...base, ngsReferenceRevisionId: 'reference-revision-1' }); await remoteReview();
        expect(prepares()[0].body.managed_reference?.ngs_reference_revision_id).toBe('reference-revision-1');
        await click('No reference — basecalling only');
        await until(() => expect(button('Approve and submit')).toBeUndefined());
        await click('Review and submit'); await until(() => expect(button('Approve and submit')).toBeTruthy());
        expect(prepares()).toHaveLength(2); expect(prepares()[1].body).not.toHaveProperty('managed_reference');
        expect(prepares()[1].context).toBe('ambient-context');
        expect(submits()).toHaveLength(0); await click('Cancel');
    });

    it('ignores late prepare responses after an actual input edit, then prepares the new request', async () => {
        let resolve!: (review: OntNgsPreparedReview) => void;
        prepare = () => new Promise(done => { resolve = done; });
        await mount(); await click('Vast · Test worker'); await click('Review and submit');
        await until(() => expect(prepares()).toHaveLength(1));
        await input('input[placeholder="my_nanopore_run"]', 'changed-name');
        await act(async () => resolve(prepared(prepares()[0].body, 'ont_basecall_dna')));
        await until(() => expect(button('Review and submit').disabled).toBe(false));
        expect(button('Approve and submit')).toBeUndefined(); expect(submits()).toHaveLength(0);
        prepare = async (body, alias) => prepared(body, alias);
        await click('Review and submit'); await until(() => expect(button('Approve and submit')).toBeTruthy());
        expect(prepares()).toHaveLength(2); expect(prepares()[1].body.name).toBe('changed-name');
        await click('Cancel');
    });

    it('keeps a newer review when an older preparation finishes last', async () => {
        let resolveOld!: (review: OntNgsPreparedReview) => void;
        prepare = () => new Promise(done => { resolveOld = done; });
        await mount(); await click('Vast · Test worker'); await click('Review and submit');
        await until(() => expect(prepares()).toHaveLength(1));
        await input('input[placeholder="my_nanopore_run"]', 'newer-request');
        expect(button('Review and submit').disabled).toBe(false);
        prepare = async (body, alias) => ({ ...prepared(body, alias), request: { ...body, name: 'newer-stable-name' } });
        await click('Review and submit'); await until(() => expect(button('Approve and submit')).toBeTruthy());
        expect(prepares()).toHaveLength(2);
        await act(async () => resolveOld(prepared(prepares()[0].body, 'ont_basecall_dna')));
        await settle(); expect(button('Approve and submit')).toBeTruthy();
        await click('Approve and submit'); await until(() => expect(submits()).toHaveLength(1));
        expect(submits()[0].body.name).toBe('newer-stable-name');
    });

    it('clears a visible review on policy/placement edits but not UI disclosure changes', async () => {
        await mount(); await remoteReview(); await click('Cancel');
        await click('Show advanced controls');
        await click('Review and submit'); await until(() => expect(button('Approve and submit')).toBeTruthy());
        expect(prepares()).toHaveLength(1);
        await input('select[aria-label="Successful remote results"]', 'automatic');
        await until(() => expect(button('Approve and submit')).toBeUndefined());
        expect(submits()).toHaveLength(0);
        await click('Review and submit'); await until(() => expect(button('Approve and submit')).toBeTruthy());
        expect(prepares()).toHaveLength(2);
        expect(prepares()[1].body.execution_policy).toEqual({ remote_result_policy: 'automatic' });
        await click('Local'); await until(() => expect(button('Approve and submit')).toBeUndefined());
        await click('Review and submit'); await until(() => expect(submits()).toHaveLength(1));
        expect(submits()[0].body.execution_target_id).toBeNull(); expect(prepares()).toHaveLength(2);
    });

    it('retains FASTQ and managed-reference replay selectors without reposting preview provenance', async () => {
        await mount({ ...base, selectedWorkflow: 'fastqQc', inputSource: 'fastq', fastqPath: '/data/source.fastq', ngsReferenceRevisionId: 'reference-revision-1' });
        await remoteReview(); await click('Approve and submit'); await until(() => expect(submits()).toHaveLength(1));
        expect(submits()[0].body).toMatchObject({ name: 'server-stable-name', fastq_snapshot: snapshot, managed_reference: { launch_snapshot_id: launchId }, execution_plan_approval: digest });
        expect(submits()[0].url).toBe('/api/ont/ngs/ont_fastq_qc/submit');
        expect(submits()[0].body.params).not.toHaveProperty('selected_native_profile');
        expect(submits()[0].body).not.toHaveProperty('model_id');
    });

    it('reopens serialized cloned optional files and exact molecular/panel selections without old approval/runtime identity', async () => {
        const fileParams = { wf_clone_primers: '/data/primers.fasta', wf_clone_insert_reference: '/data/insert.fasta', wf_clone_host_reference: '/data/host.fasta', wf_clone_regions_bedfile: '/data/regions.bed' };
        const source = { name: 'clone-files', execution_target_id: null, execution_plan_approval: 'old-approval', params: {
            ont_workflow_id: 'wf_clone_validation', fastq_path: '/data/reads.fastq', run_assembly: true, ...fileParams,
            wf_clone_min_quality: 0, wf_clone_canu_fast: false, wf_clone_expected_coverage: 0,
            molbio_revision_binding: { sequence_id: 'sequence-1', revision_id: 'revision-1', receipt_id: 'consumed' },
            comparison_panel_binding: { panel_id: 'panel-1' }, dorado_model: 'obsolete-runtime-identity',
        } } as unknown as Job;
        const saved = JSON.parse(JSON.stringify(normalizeNanoporeCloneState(source)));
        expect(saved).not.toHaveProperty('execution_plan_approval');
        await mount(saved);
        // Clone controls may be inside the ordinary advanced disclosure.
        await click('Show advanced controls');
        await settle();
        for (const [label, value] of [['Primers FASTA', fileParams.wf_clone_primers], ['Insert reference FASTA', fileParams.wf_clone_insert_reference], ['Host reference FASTA', fileParams.wf_clone_host_reference], ['Regions BED file', fileParams.wf_clone_regions_bedfile]]) {
            expect(container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)?.value).toBe(value);
            expect(button(`Browse ${label}`)).toBeTruthy();
        }
        await remoteReview(); await click('Cancel'); await click('Review and submit');
        await until(() => expect(button('Approve and submit')).toBeTruthy());
        expect(calls.filter(call => call.url.endsWith('/ngs-receipts'))).toHaveLength(1);
        expect(prepares()).toHaveLength(1);
        await click('Approve and submit'); await until(() => expect(submits()).toHaveLength(1));
        expect(submits()[0].body.params).toMatchObject({ ...fileParams, wf_clone_min_quality: 0, wf_clone_expected_coverage: 0, wf_clone_canu_fast: false, molbio_ngs_receipt_id: 'new-receipt', ngs_comparison_panel_receipt_id: 'new-panel-receipt' });
        expect(submits()[0].body.comparison_launch_id).toBe(launchId);
        expect(submits()[0].body.params).not.toHaveProperty('dorado_model');
        expect(submits()[0].body.params).not.toHaveProperty('molbio_revision_binding');
    });

    it('does not revive a stale response when edits return to the original value', async () => {
        let resolve!: (review: OntNgsPreparedReview) => void;
        prepare = () => new Promise(done => { resolve = done; });
        await mount(); await click('Vast · Test worker'); await click('Review and submit');
        await until(() => expect(prepares()).toHaveLength(1));
        await input('input[placeholder="my_nanopore_run"]', 'temporary edit');
        await input('input[placeholder="my_nanopore_run"]', 'operator-name');
        await act(async () => resolve(prepared(prepares()[0].body, 'ont_basecall_dna')));
        await until(() => expect(button('Review and submit').disabled).toBe(false));
        expect(button('Approve and submit')).toBeUndefined(); expect(submits()).toHaveLength(0);
    });

    it('ignores a late preparation failure and preserves changed form controls', async () => {
        let reject!: (error: Error) => void;
        prepare = () => new Promise((_done, fail) => { reject = fail; });
        await mount(); await click('Vast · Test worker'); await click('Review and submit');
        await until(() => expect(prepares()).toHaveLength(1));
        await input('input[placeholder="my_nanopore_run"]', 'new generation');
        await act(async () => reject(new Error('stale transport error')));
        await until(() => expect(button('Review and submit').disabled).toBe(false));
        expect(container.textContent).not.toContain('stale transport error');
        expect(container.querySelector<HTMLInputElement>('input[placeholder="my_nanopore_run"]')?.value).toBe('new generation');
        expect(submits()).toHaveLength(0);
    });

    it('uses the managed optional file selector, then preserves its edited value on a fresh clone reopen', async () => {
        await mount({ ...base, selectedWorkflow: 'clone', inputSource: 'fastq', fastqPath: '/data/reads.fastq',
            ngsReferenceRevisionId: 'reference-revision-1', runAssembly: true });
        await click('Show advanced controls'); await click('Browse Primers FASTA');
        await until(() => expect(button('Select')).toBeTruthy()); await click('Select');
        expect(container.querySelector<HTMLInputElement>('input[aria-label="Primers FASTA"]')?.value).toBe('data/picked-primers.fasta');
        await click('Review and submit'); await until(() => expect(submits()).toHaveLength(1));
        const body = submits()[0].body;
        expect(body.params.wf_clone_primers).toBe('data/picked-primers.fasta');
        const saved = JSON.parse(JSON.stringify(normalizeNanoporeCloneState({ name: body.name,
            execution_target_id: null, params: { ...body.params, ont_workflow_id: 'wf_clone_validation',
                ngs_reference_revision_id: 'reference-revision-1' } } as unknown as Job)));
        await act(async () => root.unmount()); root = createRoot(container);
        await mount(saved); await click('Show advanced controls');
        expect(container.querySelector<HTMLInputElement>('input[aria-label="Primers FASTA"]')?.value).toBe('data/picked-primers.fasta');
        await click('Review and submit'); await until(() => expect(submits()).toHaveLength(2));
        expect(submits()[1].body.params.wf_clone_primers).toBe(body.params.wf_clone_primers);
        expect(prepares()).toHaveLength(0);
    });

    it('preserves literal route aliases and typed replay fields through both API calls', async () => {
        const request: OntNgsSubmitRequest = { name: 'alias', params: { fastq_path: '/data/reads.fastq' }, execution_target_id: null,
            fastq_snapshot: snapshot, managed_reference: null, execution_plan_approval: null };
        const response = await prepareOntNgsJob('fastq_qc', request);
        await submitOntNgsJob(response.data.workflow_id, response.data.request);
        expect(prepares()[0].url).toBe('/api/ont/ngs/fastq_qc/prepare');
        expect(submits()[0].url).toBe('/api/ont/ngs/fastq_qc/submit');
        expect(submits()[0].body.fastq_snapshot).toEqual(snapshot);
        expect(submits()[0].body.execution_target_id).toBeNull();
    });
});
