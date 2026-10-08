import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, type Job } from '../../src/lib/api';
import Viewer from '../../src/components/frustrampnn/FrustraMpnnWorkbench';
import { CANONICAL_FRUSTRAMPNN_SETTINGS } from '../../src/components/frustrampnn/frustraMpnnSettingsState';
import { backendStatistics } from '../fixtures/frustraMpnnBackendContracts';
// Only optional heavyweight rendering is substituted, never the viewer, handoff,
// settings controls, request helpers, result/receipt parsers or network authority.
vi.mock('../../src/structureViewer/StructureWorkbench.js', () => ({ StructureWorkbench: () => null }));
vi.mock('../../src/components/FrustraMpnnPlotlyAnalytics.js', () => ({ default: () => null }));
vi.mock('../../src/components/FrustraMpnnLandscapeOverview.js', () => ({ default: () => null }));
vi.mock('../../src/components/frustrampnn/FrustraMpnnReviewExportPanel.js', () => ({ default: () => null }));
// Existing backend-shaped parity and upload-preview fixtures, not invented aliases.
const hashes = {
    a: 'a'.repeat(64), b: 'b'.repeat(64), c: 'c'.repeat(64), d: 'd'.repeat(64),
    e: 'e'.repeat(64), f: 'f'.repeat(64), g: '0'.repeat(64),
};

const distribution = {
    count: 4,
    mean: 0.25,
    median: 0.2,
    sample_sd: 0.1,
    min: -1,
    max: 1,
    q1: 0,
    q3: 0.5,
    iqr: 0.5,
    denominators: Object.fromEntries(
        ['count', 'mean', 'median', 'sample_sd', 'min', 'max', 'q1', 'q3', 'iqr']
            .map((name) => [name, { kind: name === 'sample_sd' ? 'sample_degrees_of_freedom_n_minus_1' : 'selected_substitution_slots', count: name === 'sample_sd' ? 3 : 4 }]),
    ),
    missingness_reasons: Object.fromEntries(
        ['count', 'mean', 'median', 'sample_sd', 'min', 'max', 'q1', 'q3', 'iqr'].map((name) => [name, null]),
    ),
};

const classBurden = {
    support_count: 4,
    counts: { high: 1, neutral: 2, minimal: 1 },
    fractions: { high: 0.25, neutral: 0.5, minimal: 0.25 },
    denominator: { kind: 'scoreable_substitution_slots', count: 4 },
    missingness_reason: null,
};

const statistics = structuredClone(backendStatistics);

const persistedRequestedSettings = {
    ...CANONICAL_FRUSTRAMPNN_SETTINGS,
    settings_value_origin: 'operator_request',
};
const effectiveSettings = {
    schema_name: 'frustrampnn_effective_settings',
    schema_version: 1,
    requested_settings: persistedRequestedSettings,
    settings_value_origin: 'operator_request',
    resolved_chains: [{
        entity: { entity_instance_id: 'entity-1', source_entity_id: '1', label_asym_id: 'AA', auth_asym_id: 'A' },
        pdb_chain_id: 'A',
        residues: [{
            entity_instance_id: 'entity-1', source_entity_id: '1', label_asym_id: 'AA', label_seq_id: 10,
            auth_asym_id: 'A', auth_seq_id: 42, insertion_code: 'B', sequence_index: 10, wt: 'G',
            pdb_chain_id: 'A', pdb_residue_id: 42, pdb_insertion_code: 'B', model_position: 9,
            residue_name: 'GLY',
        }],
    }],
    normalization_policy_id: 'frustrampnn_structure_normalizer',
    normalization_policy_version: 1,
    threshold_policy_id: 'frustrampnn_class_v1',
    threshold_policy_sha256: hashes.c,
    settings_sha256: hashes.a,
    capability_inventory_byte_sha256: hashes.d,
    resolution_identity: {
        source_artifact_sha256: hashes.a,
        structure_map_schema_name: 'frustrampnn_structure_map',
        structure_map_schema_version: 1,
        structure_map_sha256: hashes.b,
        normalized_pdb_sha256: hashes.c,
    },
    value_sources: {
        protein_selection: { mode: 'operator_request', entities: 'operator_request', regions: 'operator_request', residues: 'operator_request' },
        source_structure: { selected_model_number: 'operator_request', preferred_altloc: 'operator_request' },
        classification_policy: { mode: 'operator_request', high_max: 'operator_request', minimal_min: 'operator_request' },
    },
    effective_settings_sha256: hashes.b,
};

const resultDetail = {
    invocation_id: 'invoke-1',
    parent_job_id: 'job-1',
    parent_workflow_id: 'structure_prediction',
    candidate_id: 'candidate-1',
    operator_label: 'Candidate 1',
    source_identity: {
        design_id: null,
        artifact_id: null,
        artifact_sha256: hashes.a,
        candidate_id: 'candidate-1',
    },
    design_id: null,
    requiredness: 'required',
    source_artifact_id: null,
    source_artifact_sha256: hashes.a,
    request_sha256: hashes.b,
    manifest_sha256: hashes.c,
    summary_sha256: hashes.d,
    created_at: '2026-08-09T00:00:00Z',
    authority_version: 'v2',
    availability: true,
    statistics_available: true,
    missing_fields: [],
    settings_sha256: hashes.a,
    effective_settings_sha256: hashes.b,
    effective_settings_json: effectiveSettings,
    capability_inventory_sha256: hashes.d,
    statistics_sha256: hashes.f,
    statistics_json: statistics,
    comparison_compatibility_id: hashes.e,
    status: 'succeeded',
    component_contract_version: '2.0',
    runtime_identity: { runtime_identity_sha256: hashes.g },
    runtime_identity_sha256: hashes.g,
    gpu_provenance: { physical_device_id: '0', task_visible_device_index: 0 },
    failure_class: null,
    reopen_destination: { surface: 'frustrampnn-workbench', params: { job_id: 'job-1', invocation_id: 'invoke-1' } },
    summary: {
        schema_name: 'frustrampnn_summary', schema_version: 2,
        execution_configuration_id: 'frustrampnn_execution_configuration_v2',
        execution_configuration_sha256: hashes.a, requested_settings_sha256: hashes.a,
        effective_settings_sha256: hashes.b, runtime_identity_sha256: hashes.g,
        target_id: 'candidate-1', parent_job_id: 'job-1', candidate_id: 'candidate-1',
        source_artifact_sha256: hashes.a, structure_map_sha256: hashes.b,
        normalized_pdb_sha256: hashes.c, landscape_sha256: hashes.d,
        threshold_policy_id: 'frustrampnn_class_v1',
        threshold_policy: { mode: 'canonical', high_max: -1, minimal_min: 0.58 },
        threshold_policy_sha256: hashes.c,
        residue_support: { expected: 1, mapped: 1, scoreable: 1, excluded: 0, ambiguous: 0 },
        slot_support: { expected: 20, observed: 20, scoreable: 20 },
        missingness_by_reason: {},
        native_slot_counts: { high: 0, neutral: 1, minimal: 0 },
        native_slot_fractions: { high: 0, neutral: 1, minimal: 0 },
        complete_landscape_counts: { high: 1, neutral: 18, minimal: 1 },
        complete_landscape_fractions: { high: 0.05, neutral: 0.9, minimal: 0.05 },
        support_by_entity_chain: [{
            entity_instance_id: 'entity-1', auth_asym_id: 'A', expected_residues: 1,
            mapped_residues: 1, scoreable_residues: 1, expected_slots: 20,
            observed_slots: 20, scoreable_slots: 20,
        }],
    },
    terminal_result: {
        schema_name: 'workflow_component_result', schema_version: 2, request_sha256: hashes.b,
        invocation_id: 'invoke-1', component_id: 'frustrampnn', component_contract_version: '2.0',
        candidate_id: 'candidate-1', parent_job_id: 'job-1', parent_workflow_id: 'structure_prediction',
        status: 'succeeded', failure_class: null,
        source_artifact: { artifact_id: null, sha256: hashes.a, media_type: 'chemical/x-pdb', producer_stage: 'structure_prediction' },
        runtime_identity: { runtime_identity_sha256: hashes.g }, artifacts: [],
        result_payload: { schema_name: 'frustrampnn_summary', schema_version: 2, sha256: hashes.d },
        started_at: '2026-08-09T00:00:00Z', ended_at: '2026-08-09T00:00:01Z', duration_seconds: 1,
        gpu_provenance: { physical_device_id: '0', task_visible_device_index: 0 },
    },
    execution_receipt: {
        schema_name: 'frustrampnn_execution_receipt', schema_version: 2, invocation_id: 'invoke-1',
        execution_configuration_sha256: hashes.a, requested_settings_sha256: hashes.a,
        effective_settings_sha256: hashes.b, runtime_identity_sha256: hashes.g,
        source_artifact_sha256: hashes.a, structure_map_sha256: hashes.b, normalized_pdb_sha256: hashes.c,
        command_count: 1, gpu_provenance: { physical_device_id: '0', task_visible_device_index: 0 },
        started_at: '2026-08-09T00:00:00Z', ended_at: '2026-08-09T00:00:01Z', duration_seconds: 1,
    },
};

const handoffReceipt = {
    job_id: 'child-job-1',
    child_job_id: 'child-job-1',
    result_job_id: 'child-job-1',
    name: 'FrustraMPNN external candidate handoff',
    parent_job_id: 'job-1',
    source_parent_job_id: 'job-1',
    trigger: 'external_candidate_handoff',
    status: 'queued',
    created_at: '2026-08-09T00:00:00Z',
    started_at: null,
    completed_at: null,
    settings_value_origin: 'operator_request',
    requested_settings: persistedRequestedSettings,
    requested_settings_sha256: hashes.a,
    candidates: [],
    results: [],
    handoff: {
        parent_landscape_sha256: hashes.d,
        parent_candidate_id: 'candidate-1',
        guidance_id: 'guidance-1',
        producer_id: 'producer-1',
    },
};

const inspection = {
    source_models: [1],
    selected_source_model: 1,
    observed_altlocs: [''],
    selected_altloc: '',
    protein_entities: [{
        entity_instance_id: 'entity-1', source_entity_id: '1', label_asym_id: 'A', auth_asym_id: 'A', pdb_chain_id: 'A',
    }],
    mapped_residues: [{
        entity_instance_id: 'entity-1', source_entity_id: '1', label_asym_id: 'A', auth_asym_id: 'A',
        auth_seq_id: 1, insertion_code: '', sequence_index: 1, wt: 'M',
    }],
};

const validationPreview = {
    validation_scope: 'preview_only',
    queue_resolution_requirement: 'submission_must_re_resolve_governed_source',
    normalized_requested_settings: persistedRequestedSettings,
    effective_settings: effectiveSettings,
    execution_configuration: {
        configuration_id: 'frustrampnn_execution_configuration_v2',
        schema_name: 'frustrampnn_execution_configuration',
        schema_version: 2,
        tool_id: 'frustrampnn',
        tool_version: 'MegaScale',
        effective_settings: effectiveSettings,
        settings_value_origin: 'operator_request',
        requested_settings_sha256: '2'.repeat(64),
        effective_settings_sha256: '7'.repeat(64),
        capability_inventory_byte_sha256: '3'.repeat(64),
        classification_policy_sha256: '8'.repeat(64),
        runtime: {
            sif_name: 'frustrampnn.sif', sif_sha256: '9'.repeat(64), executable_sha256: 'a'.repeat(64),
            checkpoint_id: 'MegaScale', checkpoint_sha256: 'b'.repeat(64), package_version: '1.0',
            source_commit: 'deadbeef', python_version: '3.11', pytorch_version: '2.7', image_version: '1',
        },
        runtime_identity_sha256: 'c'.repeat(64),
        normalization_policy_id: 'frustrampnn_structure_normalizer', normalization_policy_version: 1,
        threshold_policy_id: 'frustrampnn_class_v1', source_artifact_sha256: '4'.repeat(64),
        structure_map_sha256: '5'.repeat(64), normalized_pdb_sha256: '6'.repeat(64),
        configuration_sha256: 'd'.repeat(64),
    },
    hashes: {
        settings_sha256: '2'.repeat(64), effective_settings_sha256: '7'.repeat(64),
        configuration_sha256: 'd'.repeat(64), capability_inventory_byte_sha256: '3'.repeat(64),
        structure_map_sha256: '5'.repeat(64),
    },
};


Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true });
let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
let detail: any;
let post: ReturnType<typeof vi.spyOn>;
const open = vi.fn();
const settle = async (ms = 30) => { await act(async () => { await new Promise(resolve => setTimeout(resolve, ms)); }); };
const panel = () => container.querySelector<HTMLElement>('[aria-label="FrustraMPNN external candidate handoff"]');
const button = (label: string, scope: ParentNode = panel()!) => Array.from(scope.querySelectorAll('button')).find(b => b.textContent?.includes(label))!;
const setInput = (input: HTMLInputElement, value: string) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
};
const render = async (jobId = 'job-1', invocation = 'invoke-1') => {
    const job = { id: jobId, model_id: 'structure_prediction', status: 'completed', created_at: '2026-08-09T00:00:00Z', params: { run_frustrampnn: true } } as Job;
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter><Viewer job={job} preferredInvocationId={invocation} onBack={() => {}} onOpenJob={open} /></MemoryRouter></QueryClientProvider>));
    // Global Workbench lazy-loads the actual viewer; wait for that consumer, not a fixed import delay.
    for (let i = 0; i < 80 && !container.textContent?.includes('External candidate reanalysis requires') && !panel(); i++) await settle(25);
    await settle(); await settle();
};
const fill = async () => {
    expect(panel()).not.toBeNull();
    const file = new File(['ATOM'], 'candidate.pdb', { type: 'chemical/x-pdb' });
    await act(async () => {
        setInput(panel()!.querySelector('input[placeholder="variant-1"]')!, ' variant-2 ');
        setInput(panel()!.querySelector('input[placeholder="external-redesign"]')!, ' producer-1 ');
        const input = panel()!.querySelector<HTMLInputElement>('input[type="file"]')!;
        Object.defineProperty(input, 'files', { configurable: true, value: [file] });
        input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await settle(250);
    return file;
};
const submit = async () => { await act(async () => button('Queue FrustraMPNN reanalysis').click()); await settle(); };
const refresh = async () => {
    await act(async () => { await client.invalidateQueries({ queryKey: ['frustrampnn-result'] }); });
    await settle();
};
beforeEach(() => {
    detail = structuredClone(resultDetail);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
    vi.spyOn(api, 'get').mockImplementation(async (url: string) => {
        if (/\/results\/invoke-[12]$/.test(url)) return { data: structuredClone(detail) };
        if (/\/jobs\/[^/]+\/results$/.test(url)) {
            const { summary: _summary, terminal_result: _terminal, execution_receipt: _receipt, ...item } = structuredClone(resultDetail);
            return { data: { items: [item, { ...item, invocation_id: 'invoke-2', operator_label: 'Candidate 2' }], total: 2, limit: 50, offset: 0 } };
        }
        throw new Error('Optional read unavailable: '+url);
    });
    post = vi.spyOn(api, 'post').mockImplementation(async (url: string) => {
        if (url.endsWith('/sources/inspect/upload')) return { data: inspection };
        if (url.endsWith('/settings/validate/upload')) return { data: validationPreview };
        if (url.endsWith('/candidates/handoff')) return { data: handoffReceipt };
        throw new Error('Unexpected mutation: '+url);
    });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); vi.restoreAllMocks(); open.mockReset(); document.body.replaceChildren(); });
describe('global result viewer external candidate join', () => {
    it('mounts real typed controls, submits exact identity/settings and opens queued child without replacing base science', async () => {
        await render(); const file = await fill();
        await act(async () => {
            const batching = panel()!.querySelector<HTMLInputElement>('[data-frustrampnn-batching-enabled]')!;
            if (!batching.checked) batching.click();
        });
        await act(async () => setInput(panel()!.querySelector('[data-frustrampnn-structures-number]')!, '7'));
        await act(async () => {
            const inputs = panel()!.querySelectorAll<HTMLInputElement>('input');
            const sequence = Array.from(inputs).find(i => i.parentElement?.textContent?.startsWith('Protein sequence SHA-256'))!;
            setInput(sequence, hashes.f);
        });
        await submit();
        const call = post.mock.calls.find(c => String(c[0]).endsWith('/candidates/handoff'))!;
        const form = call[1] as FormData;
        expect(form.get('structure_file')).toBe(file);
        expect(Object.fromEntries(Array.from(form.entries()).filter(([k]) => k !== 'structure_file'))).toEqual({
            candidate_id: 'variant-2', producer_id: 'producer-1', parent_job_id: 'job-1', parent_invocation_id: 'invoke-1',
            parent_landscape_sha256: hashes.d, nucleotide_edit_set: '[]', protein_sequence_sha256: hashes.f,
            frustrampnn_settings: JSON.stringify({ ...CANONICAL_FRUSTRAMPNN_SETTINGS, batching_enabled: true, structures_per_job: 7 }),
        });
        expect(panel()!.textContent).toContain('Child queued: child-job-1');
        expect(panel()!.textContent).toContain('not scientific completion');
        expect(container.textContent).toContain('Native-slot classes');
        expect(container.textContent).toContain('Full-landscape classes');
        await act(async () => button('Open child results').click());
        expect(open).toHaveBeenCalledWith('child-job-1');
        expect(container.textContent).toContain('Native-slot classes');
    });
    it.each(['3.0', '2.0', '1.0'])('supports persisted %s source without imposing a v3 settings gate', async version => {
        detail.component_contract_version = version; detail.terminal_result.component_contract_version = version;
        if (version === '3.0') {
            detail.authority_version = 'v3';
            detail.summary.schema_version = 3;
            detail.summary.execution_configuration_id = 'frustrampnn_execution_configuration_v3';
            detail.terminal_result.result_payload.schema_version = 3;
            detail.execution_receipt.schema_version = 3;
        }
        if (version === '1.0') Object.assign(detail, { authority_version: 'historical_v1', availability: false, statistics_available: false,
            missing_fields: ['settings_sha256', 'effective_settings_json', 'statistics_json'], settings_sha256: null,
            effective_settings_sha256: null, effective_settings_json: null, capability_inventory_sha256: null,
            statistics_sha256: null, statistics_json: null, comparison_compatibility_id: null, execution_receipt: null });
        await render(); await fill(); await submit();
        expect(panel()!.textContent).toContain('Child queued: child-job-1');
    });
    it.each(['job', 'invocation', 'terminal', 'candidate', 'hash', 'missing'])('rejects unavailable/foreign %s source without blanking viewer', async kind => {
        if (kind === 'job') detail.parent_job_id = 'foreign-job';
        if (kind === 'invocation') detail.invocation_id = 'foreign-invocation';
        if (kind === 'terminal') detail.terminal_result.parent_job_id = 'foreign-job';
        if (kind === 'candidate') detail.terminal_result.candidate_id = 'foreign-candidate';
        if (kind === 'hash') detail.summary.landscape_sha256 = 'bad-hash';
        if (kind === 'missing') detail = null;
        await render(); expect(panel()).toBeNull();
        expect(container.textContent).toContain('FrustraMPNN Results Viewer');
        expect(container.textContent).toContain('External candidate reanalysis requires');
        expect(post).not.toHaveBeenCalled();
    });
    it('removes cached handoff authority after detail readback fails without blanking base results', async () => {
        await render(); await fill(); await submit();
        vi.mocked(api.get).mockRejectedValue(new Error('Selected source no longer available'));
        await refresh();
        expect(panel()).toBeNull();
        expect(container.textContent).not.toContain('Child queued');
        expect(container.textContent).toContain('Selected source no longer available');
        expect(container.textContent).toContain('Native-slot classes');
    });
    it('rejects a malformed child receipt through the real parser', async () => {
        await render(); await fill();
        const previous = post.getMockImplementation()!;
        post.mockImplementation(async (...args: any[]) => String(args[0]).endsWith('/candidates/handoff')
            ? { data: { ...handoffReceipt, handoff: { ...handoffReceipt.handoff, parent_landscape_sha256: 'invalid' } } }
            : previous(...args));
        await submit();
        expect(panel()!.querySelector('[role="alert"]')?.textContent).toContain('parent_landscape_sha256');
        expect(panel()!.textContent).not.toContain('Child queued');
        expect(container.textContent).toContain('Native-slot classes');
    });
    it.each(['hash', 'job', 'invocation'])('resets form and receipt on %s authority change', async kind => {
        await render(); await fill(); await submit();
        expect(panel()!.textContent).toContain('Child queued');
        if (kind === 'hash') { detail.summary.landscape_sha256 = hashes.e; await refresh(); }
        else if (kind === 'job') { detail.parent_job_id = 'job-2'; detail.summary.parent_job_id = 'job-2'; detail.terminal_result.parent_job_id = 'job-2'; await render('job-2'); }
        else {
            detail.invocation_id = 'invoke-2'; detail.terminal_result.invocation_id = 'invoke-2';
            await act(async () => {
                const selector = container.querySelector<HTMLSelectElement>('select[aria-label="Structure"]')!;
                selector.value = 'invoke-2'; selector.dispatchEvent(new Event('change', { bubbles: true }));
            });
            await settle(); await settle();
            expect(panel()).not.toBeNull();
            expect(container.querySelector<HTMLSelectElement>('select[aria-label="Structure"]')!.value).toBe('invoke-2');
        }
        expect(container.textContent).not.toContain('Child queued');
        if (panel()) {
            expect(panel()!.querySelector<HTMLInputElement>('input[placeholder="variant-1"]')!.value).toBe('');
            expect(button('Queue FrustraMPNN reanalysis').disabled).toBe(true);
        }
    });
    it.each(['validation', 'handoff'])('shows %s failure and retains base result', async stage => {
        await render(); await fill();
        const previous = post.getMockImplementation()!;
        post.mockImplementation(async (...args: any[]) => {
            if (String(args[0]).endsWith(stage === 'validation' ? '/settings/validate/upload' : '/candidates/handoff')) throw new Error(stage+' denied');
            return previous(...args);
        });
        await submit(); expect(panel()!.textContent).toContain(stage+' denied');
        expect(panel()!.textContent).not.toContain('Child queued');
        expect(container.textContent).toContain('Native-slot classes');
        if (stage === 'validation') expect(post.mock.calls.filter(c => String(c[0]).endsWith('/candidates/handoff'))).toHaveLength(0);
    });
    it.each([
        ['validation', 'hash'], ['validation', 'job'], ['validation', 'invocation'],
        ['handoff', 'hash'], ['handoff', 'job'], ['handoff', 'invocation'],
    ])('discards late %s after %s change and never submits after stale validation', async (stage, authority) => {
        await render(); await fill();
        let resolve!: (value: any) => void;
        const deferred = new Promise(r => { resolve = r; });
        const previous = post.getMockImplementation()!;
        post.mockImplementation(async (...args: any[]) => {
            if (String(args[0]).endsWith(stage === 'validation' ? '/settings/validate/upload' : '/candidates/handoff')) return deferred;
            return previous(...args);
        });
        await submit();
        if (authority === 'hash') { detail.summary.landscape_sha256 = hashes.e; await refresh(); }
        else if (authority === 'job') {
            detail.parent_job_id = 'job-2'; detail.summary.parent_job_id = 'job-2'; detail.terminal_result.parent_job_id = 'job-2';
            await render('job-2');
        } else {
            detail.invocation_id = 'invoke-2'; detail.terminal_result.invocation_id = 'invoke-2';
            await act(async () => {
                const selector = container.querySelector<HTMLSelectElement>('select[aria-label="Structure"]')!;
                selector.value = 'invoke-2'; selector.dispatchEvent(new Event('change', { bubbles: true }));
            });
            await settle(); await settle();
        }
        const activeCall = post.mock.calls.filter(c => String(c[0]).endsWith(stage === 'validation' ? '/settings/validate/upload' : '/candidates/handoff')).at(-1)!;
        expect((activeCall[2] as { signal: AbortSignal }).signal.aborted).toBe(true);
        await act(async () => resolve({ data: stage === 'validation' ? validationPreview : handoffReceipt })); await settle();
        expect(panel()!.textContent).not.toContain('Child queued');
        expect(button('Queue FrustraMPNN reanalysis').disabled).toBe(true);
        expect(container.textContent).toContain('Native-slot classes');
        if (stage === 'validation') expect(post.mock.calls.filter(c => String(c[0]).endsWith('/candidates/handoff'))).toHaveLength(0);
    });
});
