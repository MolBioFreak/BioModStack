import React, { act } from 'react';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
// Read the actual registry, including every hidden default, through the locked ESLint YAML dependency.
const catalog = require(require.resolve('js-yaml', { paths: [require.resolve('eslint')] })).load(
    readFileSync('../api/config/models/nanopore.yaml', 'utf8'),
);

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ submitJob: vi.fn(), getLaunchContext: vi.fn(), fetchModels: vi.fn(), requests: [] as { url: string; data: any }[] }));
vi.mock('../../src/lib/projectManager', () => ({ getLaunchContext: mocks.getLaunchContext }));
vi.mock('../../src/lib/api', async () => {
    const actual = await vi.importActual<typeof import('../../src/lib/api')>('../../src/lib/api');
    actual.api.defaults.adapter = async (config) => {
        if (config.method === 'post') mocks.requests.push({ url: config.url!, data: JSON.parse(config.data) });
        const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config });
        if (config.url?.endsWith('/settings-contract')) return response({ profile_fixed: {
            wf_clone_basecaller_model: { value: 'dna_r10.4.1_e8.2_400bps_hac@v5.0.0', reason: 'Pinned clone profile.' },
        } });
        if (config.url?.endsWith('/preview')) {
            const request = JSON.parse(config.data);
            return response({ schema: 'bms.ont.launch-preview.v1', workflow_id: config.url.split('/').at(-2),
                requested_settings: request, effective_request: { params: request.params },
                blockers: [], warnings: [], preview_digest: 'c'.repeat(64) });
        }
        return ({ data: config.url?.endsWith('/ngs-receipts') ? { receipt_id: 'fresh-receipt', sequence_id: config.url.split('/').at(-2), revision_id: JSON.parse(config.data).revision_id, revision_sha256: (JSON.parse(config.data).revision_id === 'rev-1' ? '1' : '2').repeat(64) } : config.url?.endsWith('/restore') ? {
        reference_set_id: 'frozen', assignment_job_id: 'owned', manifest_sha256: 'f'.repeat(64),
        targets: [1, 2].map((n) => ({ target_id: `frozen-${n}`, label: `Frozen ${n}`, sequence_id: `seq-${n}`, revision_id: `rev-${n}`, revision_sha256: String(n).repeat(64), indistinguishable_group: null })),
    } : config.method === 'post' ? { id: 'mock-job' } : [], status: 200, statusText: 'OK', headers: {}, config });
    };
    return {
    ...actual,
    api: actual.api,
    fetchMolBioNgsReferences: vi.fn(async () => [{ id: 'reference', name: 'Reference' }]),
    fetchMolBioNgsReferenceRevisions: vi.fn(async () => [{ id: 'reference-revision', revision_number: 1, canonical_fasta_sha256: 'a'.repeat(64), molecule_type: 'dna', topology: 'circular' }]),
    fetchMolBioNgsStateRevision: vi.fn(async () => ({ id: 'exact', members: [{ role: 'ngs_reference', entity_kind: 'ngs_reference_revision', entity_id: 'reference-revision' }] })),
    EXECUTION_TARGET_STORAGE_KEY: 'bms.execution-target-id', VAST_DISCOVERY_QUERY_KEY: ['vast'],
    activateExecutionTarget: vi.fn(), deactivateExecutionTarget: vi.fn(), completeCurrentLaunchContext: vi.fn(async () => null),
    fetchExecutionTargets: vi.fn(async () => ({ data: [] })), refreshVastExecutionTargets: vi.fn(),
    fetchModels: mocks.fetchModels,
    fetchTemplates: vi.fn(async () => ({ data: [] })), fetchInputPresets: vi.fn(async () => ({ data: [] })),
    fetchTemplateById: vi.fn(async () => ({ data: null })), fetchFiles: vi.fn(), uploadFile: vi.fn(), submitJob: mocks.submitJob,
    };
});
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} } }) }));
vi.mock('../../src/components/SequenceManagerModal', () => ({ SequenceManagerModal: () => null }));
vi.mock('../../src/components/SequenceManager', () => ({ SequenceManager: () => null }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: ({ onSelect }: { onSelect: (value: unknown) => void }) => <><button onClick={() => onSelect({ name: 'default-pool', model_id: 'nanopore', mode: 'pooled_reference_assignment', params: { fastq_path: '/inputs/reads.fastq', reference_set_manifest: '/inputs/frozen.json' } })}>Load default pool fixture</button><button onClick={() => onSelect({ name: 'explicit-dna', model_id: 'nanopore', mode: 'basecall_dna', params: { pod5_dir: '/inputs/pod5', ont_molecule_type: 'dna' } })}>Load explicit DNA fixture</button><button onClick={() => onSelect({ name: 'draft-quality-alias', model_id: 'nanopore', mode: 'basecall_dna', params: { pod5_dir: '/inputs/pod5', dorado_quality_mode: 'hac' } })}>Load quality alias fixture</button><button onClick={() => onSelect({ name: 'draft-quality-conflict', model_id: 'nanopore', mode: 'basecall_dna', params: { pod5_dir: '/inputs/pod5', dorado_model: 'hac', dorado_quality_mode: 'sup' } })}>Load quality conflict fixture</button><button onClick={() => onSelect({ name: 'draft-plasmid', model_id: 'nanopore', mode: 'plasmid_qc', params: { ont_workflow_id: 'ont_plasmid_qc', fastq_path: '/inputs/reads.fastq', ngs_reference_revision_id: 'reference-revision', run_assembly: false } })}>Load plasmid fixture</button><button onClick={() => onSelect({ name: 'draft-dna', model_id: 'nanopore', mode: 'basecall_dna', params: { pod5_dir: '/inputs/pod5' } })}>Load DNA fixture</button><button onClick={() => onSelect({ name: 'draft-pool', model_id: 'nanopore', mode: 'pooled_reference_assignment', params: { fastq_path: '/inputs/unsaved.fastq', reference_set_manifest: '/inputs/frozen.json', pooled_assignment_min_mapq: 37, pooled_assignment_min_alignment_score_margin: 1234 } })}>Load draft fixture</button></> }));
vi.mock('../../src/components/MutagenesisTemplate', () => ({ MutagenesisTemplate: () => null }));
vi.mock('../../src/components/AntibodyDenovoTemplate', () => ({ AntibodyDenovoTemplate: () => null }));
vi.mock('../../src/components/OligoDesignerTemplate', () => ({ OligoDesignerTemplate: () => null }));
vi.mock('../../src/components/ProteinModificationTemplate', () => ({ ProteinModificationTemplate: () => null }));
vi.mock('../../src/components/StructurePredictionTemplate', () => ({ StructurePredictionTemplate: () => null }));
vi.mock('../../src/components/MolecularDynamicsTemplate', () => ({ MolecularDynamicsTemplate: () => null }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingLauncher', () => ({ ConformationalMappingLauncher: () => null }));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({ useGlobalExperimentContext: () => ({
    workspaceId: 'workspace', globalExperimentId: 'experiment', domainExperimentId: 'domain', stateRevisionId: 'exact',
    selectedDomainExperiment: { domain_experiment_id: 'domain' }, availability: { canMutateDomain: true },
    contextHref: (path: string) => path, updateQueryParams: vi.fn(),
}) }));
vi.mock('../../src/components/useThemeColors', () => ({ useThemeColors: () => ({}), useThemePlotlyLayout: () => ({}) }));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [] }) }));
import { JobSubmission } from '../../src/components/JobSubmission';
import { NGSToolkit } from '../../src/components/NGSToolkit';

it.each(['hac', 'fast'])('real catalog visible quality %s survives generic → native → request', async (quality) => {
    mocks.fetchModels.mockResolvedValue({ data: [catalog] });
    mocks.getLaunchContext.mockResolvedValue(undefined);
    mocks.requests.length = 0;
    const container = document.createElement('div'); document.body.appendChild(container);
    const root = createRoot(container);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const button = (text: string) => [...container.querySelectorAll('button')].find((el) => el.textContent?.trim() === text);
    try {
        await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/submit']}><Routes><Route path="/submit" element={<JobSubmission />} /><Route path="/ngs" element={<Destination />} /></Routes></MemoryRouter></QueryClientProvider>));
        await flush();
        await act(async () => button('Load DNA fixture')!.click()); await flush(); await flush();
        const control = [...container.querySelectorAll('input')].find((el) => el.value === 'sup')!;
        expect(control).toBeTruthy();
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(control, quality);
            control.dispatchEvent(new Event('input', { bubbles: true }));
        });
        await act(async () => button('Continue in NGS')!.click()); await flush(); await flush();
        expect(JSON.parse(container.querySelector('output')!.textContent!).state.ngsHandoff.doradoModel).toBe(quality);
        expect([...container.querySelectorAll('button')].some((el) => el.style.borderColor && el.textContent?.toLowerCase().includes(quality === 'hac' ? 'high' : 'fast'))).toBe(true);
        expect(button('Review and submit')?.disabled).toBe(false);
        await act(async () => button('Review and submit')!.click()); await flush();
        await confirmReviewedLaunch(container);
        expect(mocks.requests.at(-1)?.data.params.dorado_quality_mode).toBe(quality);
        expect(mocks.submitJob).not.toHaveBeenCalled();
    } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});

it('saved plasmid draft → changed visible workflow → native construct-screening request', async () => {
    mocks.fetchModels.mockResolvedValue({ data: [catalog] });
    mocks.getLaunchContext.mockResolvedValue(undefined);
    mocks.requests.length = 0;
    const container = document.createElement('div'); document.body.appendChild(container);
    const root = createRoot(container);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const button = (text: string) => [...container.querySelectorAll('button')].find((el) => el.textContent?.trim() === text);
    try {
        await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/submit?domain_experiment_id=domain&state_revision_id=exact']}><Routes><Route path="/submit" element={<JobSubmission />} /><Route path="/ngs" element={<Destination />} /></Routes></MemoryRouter></QueryClientProvider>));
        await flush();
        await act(async () => button('Load plasmid fixture')!.click()); await flush(); await flush();
        const workflow = [...container.querySelectorAll('select')].find((el) => el.value === 'plasmid_qc')!;
        expect(workflow).toBeTruthy();
        await act(async () => {
            workflow.value = 'construct_screening';
            workflow.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await act(async () => button('Continue in NGS')!.click()); await flush(); await flush();
        expect(JSON.parse(container.querySelector('output')!.textContent!).state.ngsHandoff.selectedWorkflow).toBe('constructScreening');
        expect(button('Review and submit')?.disabled).toBe(false);
        await act(async () => button('Review and submit')!.click()); await flush();
        await confirmReviewedLaunch(container);
        expect(mocks.requests.at(-1)?.url).toBe('/api/ont/ngs/ont_construct_screening/submit');
        expect(mocks.requests.at(-1)?.data.params.ont_workflow_id).not.toBe('ont_plasmid_qc');
        expect(mocks.submitJob).not.toHaveBeenCalled();
    } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});

it.each(['alias', 'conflict'])('real catalog saved quality %s distinguishes explicit choices from defaults', async (variant) => {
    mocks.fetchModels.mockResolvedValue({ data: [catalog] });
    mocks.getLaunchContext.mockResolvedValue(undefined);
    mocks.requests.length = 0;
    const container = document.createElement('div'); document.body.appendChild(container);
    const root = createRoot(container);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const button = (text: string) => [...container.querySelectorAll('button')].find((el) => el.textContent?.trim() === text);
    try {
        await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/submit']}><Routes><Route path="/submit" element={<JobSubmission />} /><Route path="/ngs" element={<Destination />} /></Routes></MemoryRouter></QueryClientProvider>));
        await flush();
        await act(async () => button(`Load quality ${variant} fixture`)!.click()); await flush(); await flush();
        await act(async () => button('Continue in NGS')!.click()); await flush(); await flush();
        if (variant === 'conflict') {
            expect(container.textContent).toContain('one exact choice');
            expect(button('Review and submit')).toBeUndefined();
            expect(mocks.requests).toHaveLength(0);
        } else {
            expect(button('Review and submit')?.disabled).toBe(false);
            await act(async () => button('Review and submit')!.click()); await flush();
            await confirmReviewedLaunch(container);
            expect(mocks.requests.at(-1)?.data.params.dorado_quality_mode).toBe('hac');
        }
    } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});

function Destination() {
    const location = useLocation();
    const navigate = useNavigate();
    return <><output>{JSON.stringify({ search: location.search, state: location.state })}</output><button onClick={() => navigate('/ngs' + location.search, { state: { ngsHandoff: { ...location.state.ngsHandoff, jobName: 'replacement-draft', pooledAssignmentMinAlignmentScoreMargin: 17 } } })}>Replace handoff</button><NGSToolkit /></>;
}
async function confirmReviewedLaunch(container: HTMLElement) {
    const button = [...container.querySelectorAll('button')].find((el) => el.textContent?.trim() === 'Confirm reviewed launch');
    expect(button).toBeTruthy();
    const preview = mocks.requests.at(-1)!;
    expect(preview.url).toMatch(/\/preview$/);
    await act(async () => button!.click()); await flush();
    expect(mocks.requests.at(-1)?.data).toEqual({ ...preview.data, preview_digest: 'c'.repeat(64) });
}
const flush = async () => { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); }); };
it.each([false, true])('hands the unsaved generic NGS draft to native launch visibly without generic submission (prepared=%s)', async (prepared) => {
    mocks.fetchModels.mockResolvedValue({ data: [catalog] });
    mocks.getLaunchContext.mockResolvedValue(prepared ? { launch_context_id: 'prepared-ngs', state: 'reserved', pinned_gpu: 2, pinned_scheduler: {
        name: 'draft-pool', model_id: 'nanopore', mode: 'pooled_reference_assignment', params: { fastq_path: '/inputs/unsaved.fastq', reference_set_manifest: '/inputs/frozen.json', pooled_assignment_min_mapq: 37, pooled_assignment_min_alignment_score_margin: 1234 },
    } } : undefined);
    const container = document.createElement('div'); document.body.appendChild(container);
    const root = createRoot(container);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    try {
        await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/submit?domain_experiment_id=domain&state_revision_id=exact' + (prepared ? '&launch_context_id=prepared-ngs' : '')]}><Routes><Route path="/submit" element={<JobSubmission />} /><Route path="/ngs" element={<Destination />} /></Routes></MemoryRouter></QueryClientProvider>));
        await flush();
        const button = (text: string) => [...container.querySelectorAll('button')].find((el) => el.textContent === text);
        if (!prepared) await act(async () => button('Load draft fixture')!.click());
        await flush(); await flush();
        const thresholdInput = [...container.querySelectorAll('input[type=number]')].find((input) => (input as HTMLInputElement).value === '37') as HTMLInputElement;
        expect(thresholdInput).toBeTruthy();
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(thresholdInput, '38');
            thresholdInput.dispatchEvent(new Event('input', { bubbles: true }));
        });
        const handoff = button('Continue in NGS');
        expect(handoff).toBeTruthy();
        await act(async () => handoff!.click()); await flush();
        const result = JSON.parse(container.querySelector('output')!.textContent!);
        expect(result.search).toContain('state_revision_id=exact');
        if (prepared) {
            expect(result.search).toContain('launch_context_id=prepared-ngs');
            expect(result.state.ngsHandoff.pinnedGpus).toEqual([2]);
        }
        expect(result.state.ngsHandoff.selectedWorkflow).toBe('pooledAssignment');
        expect(result.state.ngsHandoff.jobName).toBe('draft-pool');
        expect(result.state.ngsHandoff.referenceSetManifest).toBe('/inputs/frozen.json');
        expect(result.state.ngsHandoff.pooledAssignmentMinMapq).toBe(38);
        expect(result.state.ngsHandoff.fastqPath).toBe('/inputs/unsaved.fastq');
        expect(mocks.submitJob).not.toHaveBeenCalled();
        await flush(); await flush();
        expect(container.textContent).toContain('rev-1');
        expect(container.textContent).toContain('/inputs/frozen.json');
        expect([...container.querySelectorAll('input')].some((input) => input.value === '1234')).toBe(true);
        await act(async () => button('Replace handoff')!.click()); await flush();
        expect([...container.querySelectorAll('input')].some((input) => input.value === 'replacement-draft')).toBe(true);
        expect([...container.querySelectorAll('input')].some((input) => input.value === '17')).toBe(true);
    } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});

// Complete catalog defaults enter through JobSubmission, not a hand-written subset.
// The API's pure contract functions are the oracle; no HTTP, jobs or database.
// Use the preinstalled locked API environment without syncing/building dependencies
// inside a frontend test (these suites must also run in an offline namespace).
it.each([...catalog.modes.map((mode: { id: string }) => mode.id), 'return_dna', 'explicit_conflict'])(
    'real catalog default authority → native request: %s', async (variant) => {
    mocks.fetchModels.mockResolvedValue({ data: [catalog] });
    mocks.getLaunchContext.mockResolvedValue(undefined);
    mocks.requests.length = 0;
    const mode = variant === 'return_dna' ? 'basecall_dna' : variant === 'explicit_conflict' ? 'basecall_rna' : variant;
    const container = document.createElement('div'); document.body.appendChild(container);
    const root = createRoot(container);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const button = (text: string) => [...container.querySelectorAll('button')].find((el) => el.textContent?.trim() === text);
    try {
        await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/submit?domain_experiment_id=domain&state_revision_id=exact']}><Routes><Route path="/submit" element={<JobSubmission />} /><Route path="/ngs" element={<Destination />} /></Routes></MemoryRouter></QueryClientProvider>));
        await flush();
        const fixture = variant === 'explicit_conflict' ? 'explicit DNA' : mode === 'pooled_reference_assignment' ? 'default pool' : mode === 'fastq_qc' ? 'plasmid' : 'DNA';
        await act(async () => button(`Load ${fixture} fixture`)!.click()); await flush(); await flush();
        const workflow = [...container.querySelectorAll('select')].find((el) => [...el.options].some((o) => o.value === 'basecall_rna'))!;
        expect(workflow).toBeTruthy();
        for (const selection of variant === 'return_dna' ? ['basecall_rna', 'basecall_dna'] : [mode]) {
            await act(async () => { workflow.value = selection; workflow.dispatchEvent(new Event('change', { bubbles: true })); });
            await flush();
        }
        await act(async () => button('Continue in NGS')!.click()); await flush(); await flush();
        if (variant === 'explicit_conflict') {
            expect(container.textContent).toContain('ont_molecule_type');
            expect(button('Review and submit')).toBeUndefined();
            expect(mocks.requests).toHaveLength(0);
            return;
        }
        const handoff = JSON.parse(container.querySelector('output')!.textContent!).state.ngsHandoff;
        expect(handoff.doradoMolecule).toBe(mode === 'basecall_rna' ? 'rna' : 'dna');
        const submitLabel = mode === 'pooled_reference_assignment' ? 'Submit pooled assignment' : 'Review and submit';
        expect(button(submitLabel)?.disabled, container.textContent!).toBe(false);
        await act(async () => button(submitLabel)!.click()); await flush();
        if (mode !== 'pooled_reference_assignment') await confirmReviewedLaunch(container);
        const request = mocks.requests.at(-1)!;
        const canonical = mode === 'clone_validation' ? 'wf_clone_validation' : `ont_${mode}`;
        if (mode === 'pooled_reference_assignment') {
            expect(request.url).toBe('/api/ont/ngs/pooled-reference-assignment/submit');
            expect(request.data).not.toHaveProperty('dorado_quality_mode');
            expect(request.data).not.toHaveProperty('ont_molecule_type');
            const accepted = JSON.parse(execFileSync('uv', ['run', '--frozen', '--no-sync', '--group', 'dev', 'python', '-c', `
import json, sys
from services.ont_pooled_reference_assignment import PooledReferenceAssignmentRequest
print(PooledReferenceAssignmentRequest.model_validate(json.load(sys.stdin)).model_dump_json())
`], { cwd: '../api', env: { ...process.env, DATABASE_URL: 'sqlite+aiosqlite:///:memory:' }, input: JSON.stringify(request.data), encoding: 'utf8' }));
            expect(accepted.min_mapq).toBe(20);
            expect(accepted.min_alignment_score_margin).toBe(10);
            return;
        }
        expect(request.url).toBe(`/api/ont/ngs/${canonical}/submit`);
        if (mode === 'basecall_rna' || mode === 'basecall_dna') expect(request.data.params.ont_molecule_type).toBe(mode === 'basecall_rna' ? 'rna' : 'dna');
        const normalized = JSON.parse(execFileSync('uv', ['run', '--frozen', '--no-sync', '--group', 'dev', 'python', '-c', `
import json, sys
from services.ont_ngs_contract import normalize_ont_launch_params, validate_ont_operator_params
request = json.load(sys.stdin)
validate_ont_operator_params(request['workflow'], request['mode'], request['params'])
print(json.dumps(normalize_ont_launch_params(request['workflow'], request['params'])))
`], { cwd: '../api', env: { ...process.env, DATABASE_URL: 'sqlite+aiosqlite:///:memory:' }, input: JSON.stringify({ workflow: canonical, mode, params: request.data.params }), encoding: 'utf8' }));
        expect(normalized.ont_workflow_id).toBe(canonical);
        expect(mocks.submitJob).not.toHaveBeenCalled();
    } finally { await act(async () => root.unmount()); client.clear(); container.remove(); }
});
