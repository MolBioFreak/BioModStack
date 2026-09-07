import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const nativeApi = vi.hoisted(() => ({
    fetchMolBioNgsDomainState: vi.fn(),
    fetchProjectHub: vi.fn(),
}));

const managerApi = vi.hoisted(() => ({
    listProjects: vi.fn(),
    searchProjects: vi.fn(),
    getProject: vi.fn(),
    getGlobalExperiment: vi.fn(),
    getDomainExperiment: vi.fn(),
    getProjectSummary: vi.fn(),
    listDomainAdapters: vi.fn(),
    listProteinProjectCapabilities: vi.fn(),
    createProjectWorkflowSetup: vi.fn(),
    getProjectWorkflowSetup: vi.fn(),
    saveProjectWorkflowSetupDraft: vi.fn(),
    prepareProjectWorkflowSetup: vi.fn(),
    deleteProjectWorkflowSetup: vi.fn(),
    searchAdapterEntities: vi.fn(),
    issueAdapterReceipt: vi.fn(),
    reverifySourceReceipt: vi.fn(),
    attachExistingEntity: vi.fn(),
    getResultSurface: vi.fn(),
    createLaunchContext: vi.fn(),
    issuePreparedLaunchContext: vi.fn(),
    createProject: vi.fn(),
    createGlobalExperiment: vi.fn(),
    createDomainExperiment: vi.fn(),
    updateProject: vi.fn(),
    updateGlobalExperiment: vi.fn(),
    updateDomainExperiment: vi.fn(),
    archiveProject: vi.fn(),
    restoreProject: vi.fn(),
    archiveGlobalExperiment: vi.fn(),
    restoreGlobalExperiment: vi.fn(),
    archiveDomainExperiment: vi.fn(),
    restoreDomainExperiment: vi.fn(),
    createResearchRecord: vi.fn(),
    listGlobalExperiments: vi.fn(),
    listNgsMolBioShareableResults: vi.fn(),
    linkNgsMolBioProject: vi.fn(),
    listNgsMolBioProjectLinks: vi.fn(),
    projectManagerErrorMessage: vi.fn((error: unknown) => error instanceof Error ? error.message : String(error)),
    isPermissionError: vi.fn(() => false),
}));

vi.mock('../../src/lib/api', async (importOriginal) => ({
    ...(await importOriginal<Record<string, unknown>>()),
    ...nativeApi,
}));

vi.mock('../../src/lib/projectManager', async (importOriginal) => ({
    ...(await importOriginal<Record<string, unknown>>()),
    ...managerApi,
}));

import { ProjectTree } from '../../src/components/project-manager/ProjectTree';
import { RelationshipMap } from '../../src/components/project-manager/RelationshipMap';
import { VirtualFolderPanel } from '../../src/components/project-manager/VirtualFolderPanel';
import { RunPanel } from '../../src/components/project-manager/RunPanel';
import { ProjectManager } from '../../src/pages/ProjectManager';
import { normalizeProjectManagerReadModel } from '../../src/lib/projectManager';

const project = {
    id: 'project-1',
    kind: 'project' as const,
    storage_kind: 'workspace' as const,
    project_id: 'project-1',
    workspace_id: 'project-1',
    parent_id: null,
    current_revision_id: 'revision-project-1',
    head_generation: 3,
    lifecycle_state: 'active',
    status: 'active',
    name: 'DNA Polymerase Design',
    description: 'Cross-domain polymerase campaign',
    payload: { research_objective: 'Improve catalytic stability', owner: 'Research team', tags: ['polymerase'] },
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-09T00:00:00Z',
};

const baseSummary = {
    schema: 'bms.project-manager.read-model.v1' as const,
    subject_id: 'project-1',
    subject_generation: 3,
    assembled_at: '2026-08-09T12:00:00Z',
    source_receipt_ids: ['receipt-9'],
    source_digest_set_sha256: 'a'.repeat(64),
    adapter_versions: [{ adapter_id: 'bms.rfd3.local-redesign-reference.adapter.v1', version: '1' }],
    reconciliation: { state: 'current' as const, last_verified_at: '2026-08-09T11:00:00Z', reason: null },
    counts: { global_experiments: 1, domain_experiments: 1, attached_entities: 1 },
    status_summary: {},
    recent_activity: [],
    result_previews: [],
    pagination: {
        map_next_cursor: null, run_next_cursor: null, result_next_cursor: null,
        lineage_next_cursor: null, note_next_cursor: null, decision_next_cursor: null, dataset_next_cursor: null, activity_next_cursor: 'activity:1',
        map: { items: [], next_cursor: null, repeated_context_node_keys: [] },
        runs: { items: [], next_cursor: null }, results: { items: [], next_cursor: null },
        lineage: { items: [], next_cursor: null }, notes: { items: [], next_cursor: null }, decisions: { items: [], next_cursor: null }, datasets: { items: [], next_cursor: null },
        activity: { items: [{ id: 'event-1', resource_id: 'receipt-9', event_type: 'source_attached', generation: 3, payload: { receipt_id: 'receipt-9' }, created_at: '2026-08-09T11:00:00Z' }], next_cursor: 'activity:1' },
    },
    project: {
        id: 'project-1',
        project_scope: 'global', name: 'DNA Polymerase Design', objective: 'Improve catalytic stability',
        lifecycle_state: 'active', head_generation: 3, current_revision_id: 'revision-project-1', updated_at: '2026-08-09T00:00:00Z',
    },
    tree: { nodes: [
        { node_key: 'project:project-1', node_type: 'project' as const, subject_id: 'project-1', parent_node_key: null, label: 'DNA Polymerase Design', lifecycle_state: 'active', counts: { global_experiments: 1 }, has_children: true, allowed_actions: ['edit'] },
        { node_key: 'global_experiment:global-1', node_type: 'global_experiment' as const, subject_id: 'global-1', parent_node_key: 'project:project-1', label: 'Catalytic-loop redesign', lifecycle_state: 'active', counts: { domain_experiments: 1 }, has_children: true, allowed_actions: ['edit'] },
        { node_key: 'domain_experiment:domain-1', node_type: 'domain_experiment' as const, subject_id: 'domain-1', parent_node_key: 'global_experiment:global-1', label: 'Protein In Silico', lifecycle_state: 'active', counts: {}, has_children: true, allowed_actions: ['attach', 'add_note'] },
        { node_key: 'virtual_folder:domain-1:runs', node_type: 'virtual_folder' as const, subject_id: null, parent_node_key: 'domain_experiment:domain-1', label: 'Runs', lifecycle_state: null, counts: {}, has_children: false, allowed_actions: [] },
        { node_key: 'virtual_folder:domain-1:activity', node_type: 'virtual_folder' as const, subject_id: null, parent_node_key: 'domain_experiment:domain-1', label: 'Activity', lifecycle_state: null, counts: { activity: 1 }, has_children: false, allowed_actions: [] },
    ] },
    map: {
        focus_node_key: 'global_experiment:global-1',
        nodes: [
            { node_key: 'project:project-1', node_type: 'project', label: 'DNA Polymerase Design', normalized_state: 'active', canonical_identity: { store_id: 'global', entity_id: 'project-1' }, counts: { global_experiments: 1 }, reconciliation: { state: 'current', last_verified_at: null, reason: null }, allowed_actions: ['edit'] },
            { node_key: 'global_experiment:global-1', node_type: 'global_experiment', label: 'Catalytic-loop redesign', normalized_state: 'active', canonical_identity: { store_id: 'global', entity_id: 'global-1' }, counts: { domain_experiments: 1 }, reconciliation: { state: 'current', last_verified_at: null, reason: null }, allowed_actions: ['select'] },
            { node_key: 'domain_experiment:domain-1', node_type: 'domain_experiment', label: 'Protein In Silico', normalized_state: 'active', canonical_identity: { store_id: 'global', entity_id: 'domain-1' }, counts: {}, reconciliation: { state: 'current', last_verified_at: null, reason: null }, allowed_actions: ['select', 'attach'] },
            { node_key: 'external_entity_receipt:receipt-9', node_type: 'external_entity_receipt', label: 'PLM-07 result', normalized_state: 'completed', canonical_identity: { store_id: 'core', entity_kind: 'rfd3_local_redesign_request', entity_id: 'request-9', receipt_id: 'receipt-9', content_digest: 'b'.repeat(64) }, counts: {}, reconciliation: { state: 'current', last_verified_at: '2026-08-09T11:00:00Z', reason: null }, allowed_actions: ['open'] },
        ],
        edges: [
            { source_node_key: 'project:project-1', target_node_key: 'global_experiment:global-1', lineage_mode: 'contains', edge_key: 'contains:project-1:global-1', accessible_label: 'Project contains Global Experiment' },
            { source_node_key: 'global_experiment:global-1', target_node_key: 'domain_experiment:domain-1', lineage_mode: 'contains', edge_key: 'contains:global-1:domain-1', accessible_label: 'Global Experiment contains Domain Experiment' },
            { source_node_key: 'domain_experiment:domain-1', target_node_key: 'external_entity_receipt:receipt-9', lineage_mode: 'validated_by', edge_key: 'validated:domain-1:receipt-9', accessible_label: 'Validated by RFD3 result' },
        ],
        truncated: false,
        next_cursor: null,
    },
    selection: {
        node_key: 'domain_experiment:domain-1', node_type: 'domain_experiment', title: 'Protein In Silico', subtitle: 'Design and validation domain',
        canonical_identity: { store_id: 'global', entity_id: 'domain-1' },
        summary: { name: 'Protein In Silico', objective: 'Design stable variants', domain_kind: 'protein_in_silico', status: 'active', tags: ['protein'] },
        relationship: { parent_node_key: 'global_experiment:global-1' }, scientific_context: { experiment_mode: 'redesign', targets: [{ label: 'PLM-07', role: 'target' }] },
        reconciliation: { state: 'current', last_verified_at: null, reason: null }, available_actions: ['attach', 'add_note'], canonical_surface: null,
    },
    runs: { items: [{
        run_id: 'run-1',
        workflow_id: 'workflow-1',
        canonical_job_id: 'job-9',
        workflow_type: 'rfd3',
        target_label: 'PLM-07',
        canonical_state: 'succeeded',
        normalized_state: 'completed',
        stage: 'finalized',
        progress: { kind: 'fraction', value: 1 },
        started_at: '2026-08-09T10:00:00Z',
        elapsed_seconds: 120,
        replica_index: null,
        batch_or_run_group_id: 'batch-1',
        output_count: 12,
        condition: { severity: 'none', code: null, message: null },
        receipt_id: 'receipt-9',
        output_receipt_ids: ['receipt-9'],
        adapter_id: 'bms.rfd3.local-redesign-reference.adapter.v1',
        available_actions: [],
        canonical_surface: null,
        canonical_surfaces: [],
        attempts: [
            { attempt_id: 'attempt-1', attempt_number: 1, canonical_job_id: 'job-8', canonical_state: 'failed', binding_receipt: null, runtime_identity: null, terminal_receipt: { error_message: 'transient failure' } },
            { attempt_id: 'attempt-2', attempt_number: 2, canonical_job_id: 'job-9', canonical_state: 'completed', binding_receipt: { receipt_id: 'receipt-9' }, runtime_identity: { gpu: 'GPU 0' }, terminal_receipt: { output_count: 12 } },
        ],
    }], next_cursor: null },
    warnings: [],
    allowed_actions: ['create_global_experiment', 'edit_project'],
};

function summaryFor(selectedNodeKey?: string) {
    if (selectedNodeKey === 'global_experiment:global-1') {
        const value = structuredClone(baseSummary);
        value.selection = {
            node_key: 'global_experiment:global-1', node_type: 'global_experiment', title: 'Catalytic-loop redesign', subtitle: 'Improve catalytic stability',
            canonical_identity: { store_id: 'global', entity_id: 'global-1' }, summary: { name: 'Catalytic-loop redesign', status: 'active' }, relationship: { parent_node_key: 'project:project-1' }, scientific_context: {},
            reconciliation: { state: 'current', last_verified_at: null, reason: null }, available_actions: ['edit'], canonical_surface: null,
        };
        return value;
    }
    if (selectedNodeKey === 'workflow_run:run-1') {
        const value = structuredClone(baseSummary);
        value.selection = {
            node_key: 'workflow_run:run-1', node_type: 'workflow_run', title: 'Canonical run A', subtitle: 'Selected Workflow Run',
            canonical_identity: { store_id: 'global', entity_kind: 'workflow_run', entity_id: 'run-1' },
            summary: { canonical_state: 'completed', output_count: 12 }, relationship: { parent_node_key: 'workflow:workflow-1' }, scientific_context: {},
            reconciliation: { state: 'current', last_verified_at: '2026-08-09T11:00:00Z', reason: null }, available_actions: ['open'], canonical_surface: null,
        };
        return value;
    }
    if (selectedNodeKey !== 'external_entity_receipt:receipt-9') return structuredClone(baseSummary);
    const value = structuredClone(baseSummary);
    value.selection = {
        node_key: 'external_entity_receipt:receipt-9', node_type: 'external_entity_receipt', title: 'PLM-07 result', subtitle: 'Canonical redesign result',
        canonical_identity: { store_id: 'core', entity_kind: 'rfd3_local_redesign_request', entity_id: 'request-9', receipt_id: 'receipt-9', content_digest: 'b'.repeat(64) },
        summary: {}, relationship: { parent_node_key: 'domain_experiment:domain-1' }, scientific_context: {},
        reconciliation: { state: 'current', last_verified_at: '2026-08-09T11:00:00Z', reason: null }, available_actions: ['open'],
        canonical_surface: {
            schema: 'bms.result-surface.v1', receipt_id: 'receipt-9', entity_kind: 'rfd3_local_redesign_request', entity_id: 'request-9',
            contract_id: 'rfd3_local_redesign_v1', content_digest: 'b'.repeat(64), surface_kind: 'protein_design',
            route: { template_id: 'bms.route.design-result.v1', path: '/designs/job-9', query: {} }, readiness: 'ready',
            native_summary: { schema_id: 'bms.result-summary.test.v1', content_sha256: 'c'.repeat(64), canonical_size_bytes: 17, payload: { candidates: 12 } },
            scientific_acceptance: { state: 'review', reason: null },
            provenance: { schema_id: 'bms.result-provenance.test.v1', content_sha256: 'd'.repeat(64), canonical_size_bytes: 2, payload: {} },
            comparison: { state: 'not_applicable', reason: null, authority: null }, available_actions: ['open'],
        },
    };
    return value;
}

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

async function flush() {
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
        await Promise.resolve();
    });
}

async function waitUntil(assertion: () => void) {
    for (let attempt = 0; attempt < 30; attempt += 1) {
        try { assertion(); return; } catch { await flush(); }
    }
    assertion();
}

function LocationProbe() {
    const location = useLocation();
    return <output data-testid="location">{location.pathname}{location.search}</output>;
}

async function renderAt(initialEntry: string) {
    await act(async () => {
        root.render(
            <QueryClientProvider client={client}>
                <MemoryRouter initialEntries={[initialEntry]}>
                    <LocationProbe />
                    <Routes>
                        <Route path="/projects/:projectId?" element={<ProjectManager />} />
                        <Route path="/projects/:projectId/experiments/:experimentId" element={<ProjectManager />} />
                        <Route path="/projects/:projectId/experiments/:experimentId/domains/:domainId" element={<ProjectManager />} />
                        <Route path="*" element={<p>Canonical destination</p>} />
                    </Routes>
                </MemoryRouter>
            </QueryClientProvider>,
        );
    });
}

beforeEach(() => {
    Object.defineProperty(window, 'innerWidth', { value: 1600, writable: true, configurable: true });
    Object.values(nativeApi).forEach((mock) => mock.mockReset());
    Object.values(managerApi).forEach((mock) => mock.mockReset());
    managerApi.projectManagerErrorMessage.mockImplementation((error: unknown) => error instanceof Error ? error.message : String(error));
    managerApi.isPermissionError.mockReturnValue(false);
    managerApi.listProjects.mockResolvedValue({ items: [project], next_cursor: null });
    managerApi.searchProjects.mockResolvedValue({ items: [project], next_cursor: null });
    managerApi.getProject.mockResolvedValue(project);
    managerApi.getGlobalExperiment.mockResolvedValue({
        id: 'global-1',
        parent_id: 'project-1',
        current_revision_id: 'revision-global-1',
        name: 'Catalytic-loop redesign',
        payload: {},
    });
    managerApi.getDomainExperiment.mockResolvedValue({
        id: 'domain-1',
        parent_id: 'global-1',
        current_revision_id: 'revision-domain-1',
        name: 'Protein In Silico',
        payload: {
            domain_kind: 'protein_in_silico',
            domain_payload: {
                schema: 'bms.protein-in-silico-experiment.v3',
                experiment_mode: 'redesign',
                scientific_objective: 'Design stable variants',
                targets: [{
                    target_id: 'PLM-07',
                    label: 'PLM-07',
                    role: 'target',
                    source_receipt_ids: ['receipt-9'],
                    dataset_member_refs: [],
                }],
                planned_capability_ids: [],
                validation_capability_ids: [],
                comparison_groups: [],
            },
        },
    });
    managerApi.listGlobalExperiments.mockResolvedValue([{ id: 'global-1', name: 'Validation experiment' }]);
    managerApi.listNgsMolBioShareableResults.mockResolvedValue([]);
    managerApi.linkNgsMolBioProject.mockResolvedValue({});
    managerApi.listNgsMolBioProjectLinks.mockResolvedValue([{ link_id: 'link-1' }]);
    managerApi.getProjectSummary.mockImplementation((_projectId: string, options: { selectedNodeKey?: string; activityCursor?: string }) => {
        const value = summaryFor(options.selectedNodeKey);
        if (options.activityCursor) {
            value.pagination.activity_next_cursor = null;
            value.pagination.activity = {
                items: [{ id: 'event-2', resource_id: 'run-1', event_type: 'run_completed', generation: 4, payload: { run_id: 'run-1' }, created_at: '2026-08-09T12:00:00Z' }],
                next_cursor: null,
            };
        }
        return Promise.resolve(normalizeProjectManagerReadModel(value));
    });
    managerApi.listDomainAdapters.mockResolvedValue({ schema: 'bms.global.adapter-registry.v1', adapters: [{ adapter_id: 'bms.rfd3.local-redesign-reference.adapter.v1', adapter_version: '1', domain_kind: 'protein_in_silico', entity_kind: 'rfd3_local_redesign_request' }] });
    managerApi.listProteinProjectCapabilities.mockResolvedValue({
        schema: 'bms.protein-project.capability-inventory.v1',
        capabilities: [],
    });
    managerApi.searchAdapterEntities.mockResolvedValue({ schema: 'bms.global.adapter-search.v1', adapter_id: 'bms.rfd3.local-redesign-reference.adapter.v1', adapter_version: '1', items: [{ adapter_id: 'bms.rfd3.local-redesign-reference.adapter.v1', entity_kind: 'rfd3_local_redesign_request', entity_id: 'request-10', label: 'PLM-07 redesign', canonical_state: 'completed', attachable: true, reason: null, reopen_uri: '/designs/job-9', metadata: { created_at: '2026-08-09' } }], next_cursor: null });
    managerApi.attachExistingEntity.mockResolvedValue({ source_receipt_id: 'receipt-9' });
    managerApi.reverifySourceReceipt.mockResolvedValue({
        schema: 'bms.global.source-reverification-receipt.v1',
        source_receipt_id: 'receipt-9',
        source_digest: 'b'.repeat(64),
        verified_at: '2026-08-30T23:00:00Z',
        valid_until: '2026-08-31T23:00:00Z',
    });
    managerApi.getResultSurface.mockResolvedValue(summaryFor('external_entity_receipt:receipt-9').selection.canonical_surface);
    managerApi.createLaunchContext.mockResolvedValue({
        schema: 'bms.launch-context.v1', launch_context_id: 'launch-1', project_id: 'project-1',
        global_experiment_id: 'global-1', domain_experiment_id: 'domain-1', workflow_id: null,
        workflow_revision_id: null, return_uri: '/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1',
        issued_at: '2026-08-09T00:00:00Z', expires_at: '2026-08-09T00:30:00Z',
    });
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    client.clear();
    document.body.replaceChildren();
});

describe('ProjectManager', () => {
    it('keeps explicit tree collapse on mounted rerender and defaults new branches open', async () => {
        const nodes = normalizeProjectManagerReadModel(baseSummary).tree.nodes;
        const render = (next = nodes) => <ProjectTree nodes={next} selectedNodeKey="project:project-1" onSelect={() => undefined} />;
        await act(async () => root.render(render()));
        await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="Collapse Protein In Silico"]')?.click());
        await act(async () => root.render(render(structuredClone(nodes))));
        expect(container.querySelector('[aria-label="Expand Protein In Silico"]')?.getAttribute('aria-expanded')).toBe('false');
        expect(container.querySelector('[aria-label="Expand Activity"]')).toBeNull();
        await act(async () => root.render(render([...nodes, { ...nodes[2], node_key: 'domain_experiment:new', label: 'New domain' }])));
        expect(container.querySelector('[aria-label="Collapse New domain"]')).not.toBeNull();
    });

    it('retains the same tree through a deferred page selection and disables stale actions', async () => {
        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.querySelector('[aria-label="Project tree"]')).not.toBeNull());
        const tree = container.querySelector('[aria-label="Project tree"]');
        await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="Collapse Protein In Silico"]')?.click());
        let resolve!: (value: ReturnType<typeof normalizeProjectManagerReadModel>) => void;
        managerApi.getProjectSummary.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
        await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="Select Catalytic-loop redesign"]')?.click());
        expect(container.querySelector('[aria-label="Project tree"]')).toBe(tree);
        expect(container.querySelector('[aria-label="Expand Protein In Silico"]')).not.toBeNull();
        expect(container.querySelector<HTMLFieldSetElement>('fieldset')?.disabled).toBe(true);
        expect(container.textContent).toContain('Validating selected item');
        await act(async () => resolve(normalizeProjectManagerReadModel(summaryFor('global_experiment:global-1'))));
        await waitUntil(() => expect(container.querySelector('[aria-label="Selected node inspector"] h2')?.textContent).toBe('Catalytic-loop redesign'));
        expect(container.querySelector('[aria-label="Project tree"]')).toBe(tree);
        expect(container.querySelector('[aria-label="Expand Protein In Silico"]')).not.toBeNull();
    });

    it('expands mapped Activity inline with exact timestamp, payload and an honest fallback', async () => {
        const summary = normalizeProjectManagerReadModel(baseSummary);
        summary.pagination.activity.items = [
            { ...summary.pagination.activity.items[0], event_type: 'domain_connector_event_applied', payload: { event_type: 'molbio_ngs.domain_state.revision_saved', generation: 7, stream: 'state' } },
            { ...summary.pagination.activity.items[0], id: 'event-unknown', event_type: 'unknown_event', payload: {} },
        ];
        const select = vi.fn();
        await act(async () => root.render(<VirtualFolderPanel folder="activity" summary={summary} onSelectRecord={select} onLoadMore={() => undefined} />));
        const event = container.querySelector<HTMLButtonElement>('button')!;
        expect(event.textContent).toContain('Domain state saved');
        expect(event.getAttribute('aria-expanded')).toBe('false');
        expect(container.querySelector('time')?.dateTime).toBe('2026-08-09T11:00:00Z');
        await act(async () => event.click());
        expect(event.getAttribute('aria-expanded')).toBe('true');
        expect(container.textContent).toContain('Generation 7');
        expect(container.textContent).toContain('Event stream: state');
        expect(container.querySelector('details')?.open).toBe(false);
        expect(container.querySelector('pre')?.textContent).toContain('molbio_ngs.domain_state.revision_saved');
        const unknown = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Unknown Event'))!;
        await act(async () => unknown.click());
        expect(container.textContent).toContain('A project activity event was recorded.');
        expect(select).not.toHaveBeenCalled();
    });

    it('keeps map B ownership, compact groups, warnings and precise selected-item controls', async () => {
        const summary = normalizeProjectManagerReadModel(baseSummary);
        const global = { ...summary.map.nodes[1], node_key: 'global_experiment:second', label: 'Second experiment' };
        const domain = { ...summary.map.nodes[2], node_key: 'domain_experiment:second', label: 'Second domain' };
        const plan = { ...summary.map.nodes[3], node_key: 'workflow:plan', node_type: 'workflow' as const, label: 'Review plan' };
        summary.map.nodes.push(global, domain, plan);
        summary.map.edges.push({ ...summary.map.edges[1], edge_key: 'second-domain', source_node_key: global.node_key, target_node_key: domain.node_key }, { ...summary.map.edges[2], edge_key: 'plan', source_node_key: domain.node_key, target_node_key: plan.node_key });
        summary.map.nodes[3].reconciliation = { state: 'stale', reason: 'Source verification expired', last_verified_at: null };
        summary.map.truncated = true;
        const load = vi.fn(); const select = vi.fn();
        const render = (selected: string) => <RelationshipMap summary={summary} selectedNodeKey={selected} onSelect={select} onLoadMore={load} />;
        await act(async () => root.render(render('virtual_folder:domain-1:activity')));
        const second = container.querySelector('[aria-label="Select Second experiment"]')?.closest('section');
        expect(second?.textContent).toContain('Second domain');
        expect(second?.textContent).toContain('Plans · 1');
        expect(second?.textContent).not.toContain('Protein In Silico');
        expect(container.textContent).toContain('Attached evidence · 1');
        expect(container.textContent).toContain('Source verification expired');
        const show = Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Show selected item')!;
        expect(show.disabled).toBe(true);
        expect(container.textContent).toContain('Activity is shown in the records panel below.');
        expect(container.innerHTML).not.toContain('min-w-[34rem]');
        await act(async () => root.render(render(plan.node_key)));
        expect(show.disabled).toBe(false);
        await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="Select Review plan"]')?.click());
        expect(select).toHaveBeenCalledWith(plan);
        await act(async () => Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Load next map page')?.click());
        expect(load).toHaveBeenCalledOnce();
    });

    it('starts narrow rails closed and opens one dismissible drawer with focus return', async () => {
        window.innerWidth = 390;
        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.querySelector('[aria-label="Relationship map"]')).not.toBeNull());
        expect(container.querySelector('[role="dialog"]')).toBeNull();
        expect(container.querySelector('[aria-label="Project tree"]')).toBeNull();
        expect(container.querySelector('[aria-label="Selected node inspector"]')).toBeNull();
        const view = Array.from(container.querySelectorAll('summary')).find((item) => item.textContent === 'View')!;
        const button = (name: string) => Array.from(container.querySelectorAll('button')).find((item) => item.textContent === name)!;
        await act(async () => { view.click(); button('Show tree').focus(); button('Show tree').click(); });
        expect(container.querySelectorAll('[role="dialog"]')).toHaveLength(1);
        expect(container.querySelector('[aria-label="Project tree drawer"]')).not.toBeNull();
        await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
        expect(container.querySelector('[role="dialog"]')).toBeNull();
        expect(document.activeElement).toBe(view);
        await act(async () => { view.click(); button('Show inspector').click(); });
        expect(container.querySelectorAll('[role="dialog"]')).toHaveLength(1);
        expect(container.querySelector('[aria-label="Project tree"]')).toBeNull();
        await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="Close panel backdrop"]')?.click());
        expect(container.querySelector('[role="dialog"]')).toBeNull();
    });

    it('locks repeat run requests synchronously and shows pending then error recovery', async () => {
        managerApi.getProjectSummary.mockImplementation(() => {
            const value = normalizeProjectManagerReadModel(baseSummary);
            value.tree.nodes.push({ ...value.tree.nodes[2], node_key: 'workflow:workflow-1', node_type: 'workflow', subject_id: 'workflow-1', parent_node_key: 'domain_experiment:domain-1' });
            value.runs.items[0].available_actions = ['open_results', 'clone'];
            value.runs.items[0].canonical_surface = normalizeProjectManagerReadModel(summaryFor('external_entity_receipt:receipt-9')).selection.canonical_surface;
            return Promise.resolve(value);
        });
        let reject!: (error: Error) => void;
        managerApi.createLaunchContext.mockImplementation(() => new Promise((_resolve, fail) => { reject = fail; }));
        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.querySelector('[aria-label="Actions for run run-1"]')).not.toBeNull());
        const actions = container.querySelector('[aria-label="Actions for run run-1"]')!;
        const open = actions.querySelector<HTMLButtonElement>('button')!;
        await act(async () => { open.click(); open.click(); });
        await waitUntil(() => expect(open.textContent).toBe('Opening results…'));
        expect(managerApi.createLaunchContext).toHaveBeenCalledOnce();
        expect(Array.from(actions.querySelectorAll('button')).every((button) => button.disabled)).toBe(true);
        expect(actions.closest('article')?.getAttribute('aria-busy')).toBe('true');
        await act(async () => reject(new Error('Result service unavailable')));
        await waitUntil(() => expect(open.disabled).toBe(false));
        expect(container.textContent).toContain('Run action unavailable: Result service unavailable');
        expect(actions.closest('article')?.querySelector('details')?.open).toBe(false);
    });

    it('shows cloning only on the affected run and keeps technical data collapsed', async () => {
        const runs = normalizeProjectManagerReadModel(baseSummary).runs.items;
        runs[0].available_actions = ['clone'];
        const action = vi.fn();
        await act(async () => root.render(<RunPanel runs={[runs[0], { ...runs[0], run_id: 'run-2' }]} pendingAction={{ runId: 'run-1', action: 'clone' }} onSelect={() => undefined} onAction={action} />));
        const articles = container.querySelectorAll('article');
        expect(articles[0].textContent).toContain('Cloning…');
        expect(articles[1].textContent).not.toContain('Cloning…');
        expect(articles[0].querySelector('details')?.open).toBe(false);
        expect(articles[0].querySelector('details')?.textContent).toContain('job-9');
        await act(async () => articles[0].querySelector<HTMLButtonElement>('[aria-label="Actions for run run-1"] button')?.click());
        expect(action).not.toHaveBeenCalled();
    });

    it('does not accumulate a previous focus into the newly loaded focus', async () => {
        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.querySelector('[aria-label="Select PLM-07 result"]')).not.toBeNull());
        let resolve!: (value: ReturnType<typeof normalizeProjectManagerReadModel>) => void;
        managerApi.getProjectSummary.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
        const next = normalizeProjectManagerReadModel(baseSummary);
        const second = { ...next.map.nodes[1], node_key: 'global_experiment:second', canonical_identity: { store_id: 'global', entity_id: 'second' }, label: 'Second experiment' };
        // Make a second global reachable in the already validated snapshot.
        await act(async () => client.setQueriesData({ queryKey: ['project-manager', 'summary', 'project-1'] }, (data: unknown) => {
            if (!data || typeof data !== 'object' || !('map' in data)) return data;
            const value = data as typeof next;
            return { ...value, map: { ...value.map, nodes: [...value.map.nodes, second] } };
        }));
        await waitUntil(() => expect(container.querySelector('[aria-label="Select Second experiment"]')).not.toBeNull());
        await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="Select Second experiment"]')?.click());
        await waitUntil(() => expect(managerApi.getProjectSummary).toHaveBeenLastCalledWith('project-1', expect.objectContaining({ focusId: 'second' })));
        next.map = { ...next.map, focus_node_key: second.node_key, nodes: [next.map.nodes[0], second], edges: [] };
        next.runs.items = [];
        next.selection = { ...next.selection, node_key: second.node_key, node_type: 'global_experiment', title: 'Second experiment' };
        await act(async () => resolve(next));
        await waitUntil(() => expect(container.querySelector('[aria-label="Selected node inspector"] h2')?.textContent).toBe('Second experiment'));
        expect(container.querySelector('[aria-label="Select PLM-07 result"]')).toBeNull();
        expect(container.querySelector('[aria-label="Inspect run run-1"]')).toBeNull();
    });

    it('leads inspector with science and preserves exact metadata and warnings in their proper places', async () => {
        managerApi.getProjectSummary.mockImplementation(() => {
            const value = normalizeProjectManagerReadModel(baseSummary);
            value.selection.summary = { ...value.selection.summary, schema: 'bms.protein-in-silico-experiment.v3', priority: 'high', scientific_question: 'Which variant advances?', success_criteria: 'Review exact evidence', domain_payload: { scientific_objective: 'Compare structures', revision_id: 'nested-revision' } };
            value.selection.reconciliation = { state: 'stale', reason: 'Source verification expired', last_verified_at: null };
            return Promise.resolve(value);
        });
        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.querySelector('[aria-label="Selected node inspector"]')).not.toBeNull());
        const inspector = container.querySelector('[aria-label="Selected node inspector"]')!;
        const technical = inspector.querySelector('details')!;
        expect(technical.open).toBe(false);
        expect(technical.textContent).toContain('nested-revision');
        const science = inspector.querySelector('[aria-label="Research summary"]')!;
        expect(science.textContent).toContain('Which variant advances?');
        expect(science.textContent).toContain('Review exact evidence');
        expect(science.textContent).toContain('Compare structures');
        expect(science.textContent).not.toContain('nested-revision');
        const warning = Array.from(inspector.querySelectorAll('p')).find((item) => item.textContent === 'Source verification expired')!;
        expect(warning.closest('details')).toBeNull();
        expect(container.querySelector('[aria-label="Relationship map"]')?.textContent).toContain('Open workspace');
        await act(async () => Array.from(container.querySelectorAll('button')).find((button) => button.textContent === 'Open workspace')?.click());
        expect(container.querySelector('[data-testid="location"]')?.textContent).toContain('workspace=protein');
    });

    it('exposes both creation paths under one chooser and plain sort copy', async () => {
        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.querySelector('[data-project-manager]')).not.toBeNull());
        const chooser = Array.from(container.querySelectorAll('summary')).find((item) => item.textContent === 'New experiment')!;
        expect(chooser).toBeDefined();
        const choices = chooser.parentElement!;
        expect(choices.querySelectorAll('button')).toHaveLength(2);
        expect(choices.textContent).toContain('Choose a Protein workflow');
        expect(choices.textContent).toContain('Create a Global Experiment');
        expect(Array.from(container.querySelectorAll('button')).filter((item) => item.textContent === 'New Global Experiment')).toHaveLength(0);
        await act(async () => chooser.click());
        await act(async () => Array.from(choices.querySelectorAll('button')).find((item) => item.textContent?.startsWith('Empty experiment group'))?.click());
        await waitUntil(() => expect(container.querySelector('[role="dialog"]')?.textContent).toContain('Create Global Experiment'));
        expect((choices as HTMLDetailsElement).open).toBe(false);
    });

    it('renders the Protein workspace task-first and keeps authority data under Technical details', async () => {
        managerApi.getProjectSummary.mockImplementation(() => {
            const value = summaryFor('domain_experiment:domain-1') as ReturnType<typeof summaryFor> & { tasks?: unknown[] };
            value.tasks = [{
                setup_context_id: 'workflow-setup:reload-1', global_experiment_id: 'global-1', experiment_name: 'Fold target',
                workflow_id: 'workflow-setup-1', workflow_name: 'Fold target — ESMFold2 structure prediction',
                relationship_kind: 'primary', workflow_label: 'ESMFold2 structure prediction', setup_state: 'open',
                validation_state: 'incomplete', latest_run_state: null, result_count: 0,
                reopen_route: '/submit?template=structure_prediction&pred_method=esmfold2&setup_context_id=workflow-setup%3Areload-1&project_id=project-1',
                allowed_actions: ['resume', 'edit', 'delete'],
            }];
            return Promise.resolve(normalizeProjectManagerReadModel(value));
        });
        await renderAt('/projects/project-1/experiments/global-1/domains/domain-1?workspace=protein&section=overview');
        await waitUntil(() => expect(container.textContent).toContain('Add workflow'));
        expect(container.textContent).toContain('Setup incomplete');
        expect(container.textContent).toContain('Resume');
        expect(container.textContent).toContain('Delete draft');
        expect(container.textContent).toContain('Project');
        expect(container.textContent).toContain('Experiment');
        expect(container.textContent).not.toContain('revision-project-1');
        expect(container.textContent).not.toContain('receipt-9');
        expect(container.textContent).not.toContain('a'.repeat(64));
        expect(container.textContent).toContain('Technical details');
    });

    it('preserves exact selected Domain-state identity when opening NGS/MolBio', async () => {
        managerApi.getProjectSummary.mockImplementation(() => {
            const value = summaryFor('domain_experiment:domain-1');
            return Promise.resolve({ ...value, selection: { ...value.selection, summary: { ...value.selection.summary, schema: 'bms.ngs-molbio-experiment.v2', domain_kind: 'ngs_molbio' } } });
        });
        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1&state_revision_id=state-saved');
        await waitUntil(() => expect(container.textContent).toContain('Open Plans & Runs workspace'));
        const open = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim() === 'Open Plans & Runs workspace');
        await act(async () => open?.click());
        expect(container.querySelector('[data-testid="location"]')?.textContent).toContain('state_revision_id=state-saved');
    });

    it('labels linked NGS/MolBio Projects from server-derived relationships', async () => {
        managerApi.searchProjects.mockResolvedValue({ items: [{ ...project, payload: { ...project.payload, project_scope: 'ngs_molbio_local' } }], next_cursor: null });
        await renderAt('/projects?project_scope=ngs_molbio_local');
        await waitUntil(() => expect(container.textContent).toContain('Linked'));
        expect(managerApi.listNgsMolBioProjectLinks).toHaveBeenCalledWith('project-1');
    });
    it('renders standalone NGS/MolBio Projects as DNA-sequence-centered current workspaces', async () => {
        managerApi.listNgsMolBioProjectLinks.mockResolvedValue([]);
        managerApi.getProjectSummary.mockImplementation(() => {
            const value = summaryFor('domain_experiment:domain-1');
            value.project.project_scope = 'ngs_molbio_local';
            value.project.name = 'Syenex New Plasmids';
            value.tree.nodes[0].label = 'Syenex New Plasmids';
            return Promise.resolve(normalizeProjectManagerReadModel(value));
        });
        nativeApi.fetchMolBioNgsDomainState.mockResolvedValue({
            current_state_revision_id: 'state-current',
            head_generation: 1,
        });
        nativeApi.fetchProjectHub.mockResolvedValue({
            schema: 'bms.project-hub.v1',
            project: {
                id: 'project-1', name: 'Syenex New Plasmids', objective: 'Verify DNA sequences', lifecycle_state: 'active',
                created_at: '2026-08-01T00:00:00Z', plasmid_count: 1, settings_href: '/projects/project-1',
                add_plasmid_href: '/designer?workspace_id=project-1&action=add-plasmid',
            },
            identity: {
                workspace_id: 'project-1', global_experiment_id: 'global-1', domain_experiment_id: 'domain-1',
                selected_state_revision_id: 'state-current', current_state_revision_id: 'state-current', state_head_generation: 1,
                global_domain_revision_id: 'domain-revision-1', membership_graph_sha256: 'a'.repeat(64),
                binding_status: 'acknowledged', adapter_status: 'available',
            },
            plasmids: [{
                sequence_id: 'sequence-pl1480', revision_id: 'revision-3', receipt_id: 'receipt-pl1480',
                receipt_sha256: 'b'.repeat(64), content_digest: 'c'.repeat(64), current_content_sha256: 'd'.repeat(64), source_store_id: 'molbio',
                schema_name: 'bms.molecular-revision-receipt.v1', revision_number: 3, name: 'PL1480',
                description: 'Current editable DNA sequence', availability: 'available', unavailable_reason: null,
                length_bp: 5512, gc_percent: 48.2, feature_count: 17, feature_labels: ['CMV promoter'],
                cmv_promoter: true, neor_kanr: true, replication_origin_count: 1, saved_experiment_count: 1,
                molecule_type: 'dna', topology: 'circular', organism_host_context: null, project_tags: [], project_notes: '',
                reopen_href: '/designer?workspace_id=project-1&molbio_sequence_id=sequence-pl1480', map_segments: [],
            }],
            sequence_data: {
                items: [{ id: 'run-1', plasmid_sequence_id: 'sequence-pl1480', plasmid_name: 'PL1480', kind: 'run', title: 'ONT verification', summary: 'Clone sequencing', status: 'ready', created_at: '2026-08-28T00:00:00Z', reopen_href: '/ngs?job_id=run-1' }],
                import_href: '/ngs?action=import-ont', launcher_href: '/ngs?section=sequence-data',
            },
            experiments: [{
                id: 'digest-1', persistence: 'saved', kind: 'restriction_digest', plasmid_sequence_id: 'sequence-pl1480',
                plasmid_sequence_ids: ['sequence-pl1480'], input_sequence_ids: ['sequence-pl1480'], output_sequence_ids: [],
                plasmid_name: 'PL1480', title: 'EcoRI digest', status: 'saved', created_at: '2026-08-28T00:00:00Z', reopen_href: '/designer?molbio_operation_id=digest-1',
            }, {
                id: 'digest-unassigned', persistence: 'saved', kind: 'restriction_digest', plasmid_sequence_id: '',
                plasmid_sequence_ids: [], input_sequence_ids: [], output_sequence_ids: [],
                plasmid_name: 'Unassigned DNA sequence', title: 'Detached saved digest', status: 'saved', created_at: '2026-08-28T01:00:00Z', reopen_href: '/designer?molbio_operation_id=digest-unassigned',
            }],
            results: [{
                id: 'result-1', plasmid_sequence_id: 'sequence-pl1480', plasmid_name: 'PL1480', type: 'Clone assessment',
                status: 'ready', owner: 'run-1', created_at: '2026-08-28T00:00:00Z', summary: 'Sequence matches', reopen_href: '/ngs?job_id=run-1',
            }, {
                id: 'result-unassigned', plasmid_sequence_id: '', plasmid_name: 'Unassigned sequence', type: 'Unassigned QC result',
                status: 'ready', owner: 'run-2', created_at: '2026-08-28T00:00:00Z', summary: null, reopen_href: '/ngs?job_id=run-2',
            }],
            activity: [],
        });

        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.textContent).toContain('DNA sequence workspace'));

        expect(container.textContent).toContain('1 current DNA sequences');
        expect(container.textContent).toContain('A sequence can be a plasmid');
        expect(container.textContent).toContain('Latest editable · Revision 3');
        expect(container.textContent).toContain('ONT verification');
        expect(container.textContent).toContain('EcoRI digest');
        expect(container.textContent).toContain('Clone assessment');
        expect(container.textContent).toContain('Unassigned Project records');
        expect(container.textContent).toContain('Detached saved digest');
        expect(container.textContent).toContain('Unassigned QC result');
        expect(container.textContent).not.toContain('relationship map');
        expect(container.textContent).not.toContain('Hide tree');
        expect(container.textContent).not.toContain('Hide inspector');
        expect(managerApi.listNgsMolBioProjectLinks).toHaveBeenCalledWith('project-1', 1);
        const openLatest = Array.from(container.querySelectorAll<HTMLAnchorElement>('a')).find((anchor) => anchor.textContent?.trim() === 'Open latest');
        expect(openLatest?.getAttribute('href')).toContain('molbio_sequence_id=sequence-pl1480');
        expect(openLatest?.getAttribute('href')).not.toContain('molbio_revision_id');
    });

    it('offers optional local-to-Global Project linking in the shared Add current work flow', async () => {
        managerApi.getProjectSummary.mockImplementation(() => {
            const value = summaryFor('project:project-1');
            value.project.project_scope = 'ngs_molbio_local';
            return Promise.resolve(normalizeProjectManagerReadModel(value));
        });
        await renderAt('/projects/project-1?selected=project%3Aproject-1');
        await waitUntil(() => expect(container.textContent).toContain('NGS/MolBio Project'));
        const add = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim() === 'Attach existing record');
        await waitUntil(() => expect(add?.disabled).toBe(false));
        await act(async () => add?.click());
        const linkProject = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim() === 'Link this NGS/MolBio Project to a Global Project');
        expect(linkProject).not.toBeUndefined();
        await act(async () => linkProject?.click());
        await waitUntil(() => expect(container.textContent).toContain('Link NGS/MolBio Project'));
        expect(container.textContent).toContain('Native data stays in this NGS/MolBio Project.');
        expect(container.textContent).toContain('Experiments to expose');
        expect(container.textContent).toContain('Results to expose');
    });
    it('opens a completed Design through the server-issued preparation-bound v2 MD context', async () => {
        managerApi.getProjectSummary.mockImplementation(() => {
            const value = summaryFor('workflow_run:run-1');
            const mutable = value as unknown as {
                runs: { items: Array<{
                    available_actions: string[];
                    attempts: Array<{ binding_receipt: Record<string, unknown> | null }>;
                }> };
            };
            mutable.runs.items[0].available_actions = ['launch_molecular_dynamics'];
            mutable.runs.items[0].attempts[1].binding_receipt = {
                receipt_id: 'receipt-9',
                md_preparation: {
                    preparation_id: 'prep-md-1',
                    source_design_id: '77777777-7777-4777-8777-777777777777',
                    workflow_id: 'molecular_dynamics',
                    workflow_revision_id: 'md-revision-1',
                },
            };
            return Promise.resolve(normalizeProjectManagerReadModel(value));
        });
        managerApi.issuePreparedLaunchContext.mockResolvedValue({
            schema: 'bms.launch-context.v2',
            launch_context_id: '88888888-8888-4888-8888-888888888888',
            project_id: 'project-1',
            global_experiment_id: 'global-1',
            domain_experiment_id: 'domain-1',
            workflow_id: 'molecular_dynamics',
            workflow_revision_id: 'md-revision-1',
            preparation_id: 'prep-md-1',
            return_uri: '/projects/project-1?focus=global-1&selected=workflow_run%3Arun-1',
        });
        await renderAt('/projects/project-1?focus=global-1&selected=workflow_run%3Arun-1');
        await waitUntil(() => expect(container.textContent).toContain('Launch Molecular Dynamics'));
        const launch = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Launch Molecular Dynamics');
        await act(async () => launch?.click());
        await waitUntil(() => expect(managerApi.issuePreparedLaunchContext).toHaveBeenCalledTimes(1));
        expect(managerApi.issuePreparedLaunchContext).toHaveBeenCalledWith(
            'project-1',
            'global-1',
            'domain-1',
            'prep-md-1',
            '/projects/project-1?focus=global-1&selected=workflow_run%3Arun-1',
        );
        expect(managerApi.createLaunchContext).not.toHaveBeenCalled();
        await waitUntil(() => expect(container.querySelector('[data-testid="location"]')?.textContent).toBe(
            '/submit?template=molecular_dynamics&launch_context_id=88888888-8888-4888-8888-888888888888&source_design_id=77777777-7777-4777-8777-777777777777',
        ));
    });

    it('renders each canonical run once and labels retry provenance only as attempts', async () => {
        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.textContent).toContain('DNA Polymerase Design'));

        expect(container.querySelector('[aria-label="Project tree"]')).not.toBeNull();
        expect(container.querySelector('[aria-label="Relationship map"]')).not.toBeNull();
        expect(container.querySelector('[aria-label="Selected node inspector"]')).not.toBeNull();
        expect(container.querySelector('[data-node-region="global-experiment"]')).not.toBeNull();
        expect(container.querySelector('[data-node-region="domain-experiment"]')).not.toBeNull();
        expect(container.textContent).toContain('Workflow runs');
        expect(container.textContent).toContain('PLM-07');
        expect(container.textContent).toContain('Canonical Succeeded');
        expect(container.textContent).toContain('Global Completed');
        expect(container.textContent).toContain('Attempt 1');
        expect(container.textContent).toContain('Attempt 2');
        expect(container.textContent).not.toContain('Replica / attempt');
        expect(container.querySelectorAll('[aria-label="Inspect run run-1"]')).toHaveLength(1);
        expect(container.querySelectorAll('[role="separator"][aria-orientation="vertical"]')).toHaveLength(2);
    });

    it('synchronizes map selection, URL, inspector, and server-issued canonical navigation', async () => {
        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.textContent).toContain('Design stable variants'));

        const resultNode = container.querySelector<HTMLButtonElement>('[aria-label="Select PLM-07 result"]');
        expect(resultNode).not.toBeNull();
        await act(async () => resultNode?.click());
        await waitUntil(() => expect(container.querySelector('[data-testid="location"]')?.textContent).toContain('selected=external_entity_receipt%3Areceipt-9'));
        await waitUntil(() => expect(container.querySelector('[aria-label="Selected node inspector"]')?.textContent).toContain('Canonical redesign result'));

        const openButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Open canonical source'));
        expect(openButton).not.toBeUndefined();
        await act(async () => openButton?.click());
        await waitUntil(() => expect(managerApi.createLaunchContext, container.textContent ?? '').toHaveBeenCalledTimes(1));
        expect(managerApi.createLaunchContext).toHaveBeenCalledWith(
            'project-1',
            'global-1',
            'domain-1',
            expect.objectContaining({
                return_uri: expect.stringContaining('selected=external_entity_receipt%3Areceipt-9'),
            }),
        );
        await waitUntil(() => expect(container.querySelector('[data-testid="location"]')?.textContent).toBe('/designs/job-9?launch_context_id=launch-1'));
        expect(container.textContent).toContain('Canonical destination');
    });

    it('attaches an adapter search result from the selected Domain Experiment with an explicit lineage role', async () => {
        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.textContent).toContain('Attach existing record'));

        const addButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Attach existing record');
        await act(async () => addButton?.click());
        await waitUntil(() => expect(container.querySelector('[role="dialog"]')).not.toBeNull());

        const operation = container.querySelector<HTMLSelectElement>('[aria-label="Attachment operation mode"]');
        expect(operation).not.toBeNull();
        const note = container.querySelector<HTMLInputElement>('[aria-label="Optional attachment note"]');
        expect(note?.disabled).toBe(false);
        expect(container.textContent).toContain('Source revision');
        expect(container.textContent).toContain('Content digest');
        expect(container.textContent).toContain('Already attached');
        expect(container.querySelector<HTMLOptionElement>('option[value="clone_import_revision"]')?.disabled).toBe(false);

        const searchInput = container.querySelector<HTMLInputElement>('[aria-label="Search canonical records"]');
        expect(searchInput).not.toBeNull();
        await act(async () => {
            if (searchInput) {
                const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                valueSetter?.call(searchInput, 'PLM-07');
                searchInput.dispatchEvent(new Event('input', { bubbles: true }));
            }
        });
        const searchButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Search');
        await act(async () => searchButton?.click());
        await waitUntil(() => expect(container.textContent).toContain('PLM-07 redesign'));
        expect(managerApi.searchAdapterEntities).toHaveBeenCalledWith('bms.rfd3.local-redesign-reference.adapter.v1', 'PLM-07', 25);

        const resultChoice = container.querySelector<HTMLInputElement>('input[type="radio"][value="request-10"]');
        await act(async () => resultChoice?.click());
        await act(async () => {
            if (operation) {
                operation.value = 'attach_evidence';
                operation.dispatchEvent(new Event('change', { bubbles: true }));
            }
            if (note) {
                const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                valueSetter?.call(note, 'Reviewed evidence');
                note.dispatchEvent(new Event('input', { bubbles: true }));
            }
        });
        const role = container.querySelector<HTMLSelectElement>('[aria-label="Lineage role"]');
        expect(role?.value).toBe('validated_by');
        const attachButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Verify receipt and attach');
        await act(async () => attachButton?.click());
        await waitUntil(() => expect(managerApi.attachExistingEntity).toHaveBeenCalledTimes(1));

        expect(managerApi.attachExistingEntity).toHaveBeenCalledWith('project-1', 'global-1', 'domain-1', {
            adapter_id: 'bms.rfd3.local-redesign-reference.adapter.v1',
            entity_id: 'request-10',
            operation: 'attach_evidence',
            role: 'validated_by',
            note: 'Reviewed evidence',
            expected_head_generation: 3,
        });
        await waitUntil(() => expect(container.querySelector('[data-testid="location"]')?.textContent).toContain('selected=external_entity_receipt%3Areceipt-9'));
    });

    it('lists Projects at the optional route without inventing selection state', async () => {
        await renderAt('/projects');
        await waitUntil(() => expect(container.textContent).toContain('DNA Polymerase Design'));
        const link = container.querySelector<HTMLAnchorElement>('a[href="/projects/project-1"]');
        expect(link).not.toBeNull();
        expect(managerApi.getProjectSummary).not.toHaveBeenCalled();
    });

    it('owns the All, Global, and NGS/MolBio discovery scope in the URL and API query', async () => {
        const localProject = {
            ...project,
            id: 'local-project-1', project_id: 'local-project-1', workspace_id: 'local-project-1',
            name: 'Syenex New Plasmids',
            payload: { ...project.payload, project_scope: 'ngs_molbio_local' },
        };
        managerApi.searchProjects.mockImplementation(({ projectScope }: { projectScope?: string }) => Promise.resolve({
            items: projectScope === 'global' ? [project] : projectScope === 'ngs_molbio_local' ? [localProject] : [project, localProject],
            next_cursor: null,
        }));

        await renderAt('/projects');
        await waitUntil(() => expect(container.textContent).toContain('Syenex New Plasmids'));
        expect(managerApi.searchProjects).toHaveBeenLastCalledWith(expect.objectContaining({ projectScope: 'all' }));
        expect(container.querySelector('[role="tablist"][aria-label="Project scope"]')).not.toBeNull();
        expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('All');
        expect(container.textContent).toContain('Global');
        expect(container.textContent).toContain('NGS/MolBio');

        const localScope = Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]')).find((button) => button.textContent === 'NGS/MolBio');
        await act(async () => localScope?.click());
        await waitUntil(() => expect(container.querySelector('[data-testid="location"]')?.textContent).toBe('/projects?scope=ngs-molbio'));
        await waitUntil(() => expect(managerApi.searchProjects).toHaveBeenLastCalledWith(expect.objectContaining({ projectScope: 'ngs_molbio_local' })));
        await waitUntil(() => expect(container.textContent).toContain('Syenex New Plasmids'));
        expect(container.textContent).not.toContain('DNA Polymerase Design');
    });

    it('creates either a Global or standalone NGS/MolBio Project from the shared dialog', async () => {
        managerApi.createProject.mockResolvedValue({ id: 'local-new', head_generation: 1 });
        await renderAt('/projects?scope=ngs-molbio');
        await waitUntil(() => expect(container.textContent).toContain('Create Project'));
        const create = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim() === 'Create Project');
        await act(async () => create?.click());
        const type = container.querySelector<HTMLSelectElement>('[aria-label="Project type"]');
        expect(type).not.toBeNull();
        expect(Array.from(type?.options ?? []).map((option) => option.textContent)).toEqual(['Global Project', 'Standalone NGS/MolBio Project']);
        await act(async () => {
            if (type) {
                type.value = 'ngs_molbio_local';
                type.dispatchEvent(new Event('change', { bubbles: true }));
            }
        });
        const name = container.querySelector<HTMLInputElement>('[aria-label="Project name"]');
        await act(async () => {
            if (name) {
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                setter?.call(name, 'Standalone validation');
                name.dispatchEvent(new Event('input', { bubbles: true }));
            }
        });
        await waitUntil(() => expect(container.querySelector<HTMLInputElement>('[aria-label="Project name"]')?.value).toBe('Standalone validation'));
        const save = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim() === 'Create Project' && button.closest('[role="dialog"]'));
        await act(async () => save?.click());
        await waitUntil(() => expect(managerApi.createProject).toHaveBeenCalledTimes(1));
        expect(managerApi.createProject).toHaveBeenCalledWith(expect.objectContaining({
            schema: 'bms.project.v2', project_scope: 'ngs_molbio_local', name: 'Standalone validation',
        }));
    });

    it('filters and orders the Projects index while failing closed for unavailable aggregate indicators', async () => {
        const archived = { ...project, id: 'project-old', project_id: 'project-old', workspace_id: 'project-old', name: 'Archived enzyme', status: 'archived', lifecycle_state: 'archived', updated_at: '2026-07-01T00:00:00Z', payload: { owner: 'Archive team', tags: ['enzyme'] } };
        const blocked = { ...project, id: 'project-blocked', project_id: 'project-blocked', workspace_id: 'project-blocked', name: 'Blocked polymerase', status: 'active', lifecycle_state: 'active', updated_at: '2026-08-10T00:00:00Z', payload: { research_objective: 'Recover polymerase', owner: 'Research team', tags: ['polymerase'] }, unresolved_failure_count: 2, active_experiment_count: 3 };
        managerApi.searchProjects.mockImplementation(({ archive }: { archive?: string }) => Promise.resolve({
            items: archive === 'archived' ? [archived] : [blocked, project],
            next_cursor: null,
        }));
        await renderAt('/projects');
        await waitUntil(() => expect(container.textContent).toContain('Blocked polymerase'));
        const cards = Array.from(container.querySelectorAll<HTMLAnchorElement>('a[data-project-card]'));
        expect(cards[0]?.textContent).toContain('Blocked polymerase');
        expect(cards[0]?.textContent).toContain('3 active experiments');
        expect(cards[0]?.textContent).toContain('2 unresolved failures');
        expect(container.textContent).toContain('Active experiments unavailable');

        const archiveFilter = container.querySelector<HTMLSelectElement>('[aria-label="Archive filter"]');
        await act(async () => {
            if (archiveFilter) {
                archiveFilter.value = 'archived';
                archiveFilter.dispatchEvent(new Event('change', { bubbles: true }));
            }
        });
        await waitUntil(() => expect(container.textContent).toContain('Archived enzyme'));
        expect(container.textContent).not.toContain('Blocked polymerase');
        expect(managerApi.searchProjects).toHaveBeenLastCalledWith(expect.objectContaining({
            archive: 'archived', limit: 50,
        }));
    });

    it('uses hierarchy routes to establish exact focus and selected query context', async () => {
        await renderAt('/projects/project-1/experiments/global-1/domains/domain-1');
        await waitUntil(() => expect(managerApi.getProjectSummary).toHaveBeenCalled());
        expect(managerApi.getProjectSummary).toHaveBeenCalledWith('project-1', expect.objectContaining({
            focusId: 'global-1', selectedNodeKey: 'domain_experiment:domain-1',
        }));
        await waitUntil(() => expect(container.querySelector('[data-testid="location"]')?.textContent).toContain('?focus=global-1&selected=domain_experiment%3Adomain-1'));
    });

    it('exposes every exact protein experiment mode and no invalid evaluation mode', async () => {
        await renderAt('/projects/project-1?focus=global-1&selected=global_experiment%3Aglobal-1');
        await waitUntil(() => expect(container.textContent).toContain('New Domain Experiment'));
        const create = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'New Domain Experiment');
        await act(async () => create?.click());
        const mode = container.querySelector<HTMLSelectElement>('[aria-label="Protein experiment mode"]');
        expect(Array.from(mode?.options ?? []).map((option) => option.value)).toEqual([
            'exploration', 'design', 'redesign', 'prediction', 'validation', 'comparison', 'simulation', 'analysis',
        ]);
        expect(container.textContent).not.toContain('Evaluation');
    });

    it('issues a verified producer receipt before creating the first Protein Domain', async () => {
        managerApi.listDomainAdapters.mockResolvedValue({
            schema: 'bms.global.adapter-registry.v1',
            adapters: [{
                adapter_id: 'bms.core-job.esmfold2.adapter.v1', adapter_version: 1,
                domain_kind: 'protein_in_silico', entity_kind: 'typed_core_job_result',
                display_name: 'Typed core Job result: esmfold2',
            }],
        });
        managerApi.searchAdapterEntities.mockResolvedValue({
            schema: 'bms.global.adapter-search.v1', adapter_id: 'bms.core-job.esmfold2.adapter.v1', adapter_version: 1,
            items: [{
                adapter_id: 'bms.core-job.esmfold2.adapter.v1', entity_kind: 'typed_core_job_result',
                entity_id: 'job-1ubq', label: 'Ubiquitin 1UBQ', canonical_state: 'completed', attachable: true,
                reason: null, reopen_uri: '/designs/job-1ubq', metadata: { content_digest: 'd'.repeat(64) },
            }], next_cursor: null,
        });
        managerApi.issueAdapterReceipt.mockResolvedValue({ receipt_id: 'receipt-1ubq', receipt: { content_digest: 'd'.repeat(64) } });

        await renderAt('/projects/project-1?focus=global-1&selected=global_experiment%3Aglobal-1');
        await waitUntil(() => expect(container.textContent).toContain('New Domain Experiment'));
        const create = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'New Domain Experiment');
        await act(async () => create?.click());
        await waitUntil(() => expect(container.querySelector('[aria-label="Protein source adapter"]')).not.toBeNull());

        const searchInput = container.querySelector<HTMLInputElement>('[aria-label="Search Protein source records"]');
        await act(async () => {
            if (searchInput) {
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                setter?.call(searchInput, '1UBQ');
                searchInput.dispatchEvent(new Event('input', { bubbles: true }));
            }
        });
        const search = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Search');
        await act(async () => search?.click());
        await waitUntil(() => expect(container.textContent).toContain('Ubiquitin 1UBQ'));
        const choice = container.querySelector<HTMLInputElement>('input[type="radio"][value="job-1ubq"]');
        await act(async () => choice?.click());
        const issue = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Verify and add to target');
        await act(async () => issue?.click());
        await waitUntil(() => expect(managerApi.issueAdapterReceipt).toHaveBeenCalledWith(
            'bms.core-job.esmfold2.adapter.v1', 'job-1ubq', 'project-1',
        ));
        await waitUntil(() => expect(container.querySelector<HTMLInputElement>('[aria-label="Protein source receipt IDs 1"]')?.value).toBe('receipt-1ubq'));
        expect(container.querySelector<HTMLInputElement>('[aria-label="Protein expected content SHA-256 1"]')?.value).toBe('d'.repeat(64));
    });

    it('reverifies stale Protein source receipts from the Overview', async () => {
        managerApi.getProjectSummary.mockImplementation(() => {
            const value = summaryFor('domain_experiment:domain-1');
            value.reconciliation = {
                state: 'stale',
                last_verified_at: '2026-08-01T00:00:00Z',
                reason: 'source verification expired',
            };
            return Promise.resolve(normalizeProjectManagerReadModel(value));
        });
        await renderAt('/projects/project-1/experiments/global-1/domains/domain-1?workspace=protein&section=overview');
        await waitUntil(() => expect(container.textContent).toContain('Reverify sources'));
        const reverify = Array.from(container.querySelectorAll('button')).find(
            (button) => button.textContent?.trim() === 'Reverify sources',
        );
        await act(async () => reverify?.click());
        await waitUntil(() => expect(managerApi.reverifySourceReceipt).toHaveBeenCalledWith(
            'project-1', 'global-1', 'domain-1', 'receipt-9',
        ));
    });

    it('edits typed Protein entity-map display rows in the browser', async () => {
        const sourceDigest = 'd'.repeat(64);
        managerApi.getProjectSummary.mockImplementation(() => {
            const value = summaryFor('domain_experiment:domain-1');
            value.selection.available_actions = ['edit'];
            const domainNode = value.tree.nodes.find((node) => node.node_key === 'domain_experiment:domain-1');
            if (domainNode) domainNode.allowed_actions = ['edit'];
            return Promise.resolve(normalizeProjectManagerReadModel(value));
        });
        managerApi.getDomainExperiment.mockResolvedValue({
            id: 'domain-1',
            parent_id: 'global-1',
            current_revision_id: 'revision-domain-1',
            head_generation: 2,
            name: 'Protein In Silico',
            payload: {
                domain_kind: 'protein_in_silico',
                objective: 'Design stable variants',
                domain_payload: {
                    schema: 'bms.protein-in-silico-experiment.v3',
                    experiment_mode: 'redesign',
                    scientific_objective: 'Design stable variants',
                    targets: [{
                        target_id: 'PLM-07',
                        label: 'PLM-07',
                        role: 'target',
                        source_receipt_ids: ['receipt-9'],
                        dataset_member_refs: [],
                        entity_map_reference: {
                            schema: 'bms.protein-entity-map-reference.v1',
                            authority_kind: 'governed_artifact_receipt',
                            receipt_id: 'map-receipt-1',
                            receipt_sha256: 'a'.repeat(64),
                            content_sha256: 'b'.repeat(64),
                            canonical_size_bytes: 200,
                            entity_count: 1,
                            residue_mapping_count: 540,
                            display_entities: [{
                                entity_instance_id: 'A',
                                source_entity_id: '1',
                                entity_type: 'protein',
                                label_asym_id: 'A',
                                auth_asym_id: 'A',
                            }],
                        },
                        expected_content_sha256: sourceDigest,
                    }],
                    planned_capability_ids: [],
                    validation_capability_ids: [],
                    comparison_groups: [],
                    acceptance_criteria: [],
                    evidence_plan: [],
                },
            },
        });
        managerApi.updateDomainExperiment.mockResolvedValue({ id: 'domain-1' });

        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.textContent).toContain('Edit revision'));
        const edit = Array.from(container.querySelectorAll('button')).find(
            (button) => button.textContent?.trim() === 'Edit revision',
        );
        await act(async () => edit?.click());
        await waitUntil(() => expect(container.querySelector<HTMLInputElement>('[aria-label="Protein entity auth chain 1.1"]')?.value).toBe('A'));
        const authChain = container.querySelector<HTMLInputElement>('[aria-label="Protein entity auth chain 1.1"]');
        await act(async () => {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
            setter?.call(authChain, 'B');
            authChain?.dispatchEvent(new Event('input', { bubbles: true }));
        });
        const save = Array.from(container.querySelectorAll('button')).find(
            (button) => button.textContent?.trim() === 'Save immutable revision',
        );
        await act(async () => save?.click());
        await waitUntil(() => expect(managerApi.updateDomainExperiment).toHaveBeenCalled());
        expect(managerApi.updateDomainExperiment.mock.calls[0][3].domain_payload.targets[0].entity_map_reference.display_entities).toEqual([{
            entity_instance_id: 'A',
            source_entity_id: '1',
            entity_type: 'protein',
            label_asym_id: 'A',
            auth_asym_id: 'B',
        }]);
    });

    it('fails closed when Protein entity-map display rows use unsupported data', async () => {
        const sourceDigest = 'd'.repeat(64);
        managerApi.getProjectSummary.mockImplementation(() => {
            const value = summaryFor('domain_experiment:domain-1');
            value.selection.available_actions = ['edit'];
            const domainNode = value.tree.nodes.find((node) => node.node_key === 'domain_experiment:domain-1');
            if (domainNode) domainNode.allowed_actions = ['edit'];
            return Promise.resolve(normalizeProjectManagerReadModel(value));
        });
        managerApi.getDomainExperiment.mockResolvedValue({
            id: 'domain-1',
            parent_id: 'global-1',
            current_revision_id: 'revision-domain-1',
            head_generation: 2,
            name: 'Protein In Silico',
            payload: {
                domain_kind: 'protein_in_silico',
                objective: 'Design stable variants',
                domain_payload: {
                    schema: 'bms.protein-in-silico-experiment.v3',
                    experiment_mode: 'redesign',
                    scientific_objective: 'Design stable variants',
                    targets: [{
                        target_id: 'PLM-07',
                        label: 'PLM-07',
                        role: 'target',
                        source_receipt_ids: ['receipt-9'],
                        dataset_member_refs: [],
                        entity_map_reference: {
                            schema: 'bms.protein-entity-map-reference.v1',
                            authority_kind: 'governed_artifact_receipt',
                            receipt_id: 'map-receipt-1',
                            receipt_sha256: 'a'.repeat(64),
                            content_sha256: 'b'.repeat(64),
                            canonical_size_bytes: 200,
                            entity_count: 1,
                            residue_mapping_count: 540,
                            display_entities: [{
                                entity_instance_id: 'A',
                                source_entity_id: '1',
                                entity_type: 'future-polymer',
                                label_asym_id: 'A',
                                auth_asym_id: 'A',
                            }],
                        },
                        expected_content_sha256: sourceDigest,
                    }],
                    planned_capability_ids: [],
                    validation_capability_ids: [],
                    comparison_groups: [],
                    acceptance_criteria: [],
                    evidence_plan: [],
                },
            },
        });

        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.textContent).toContain('Edit revision'));
        const edit = Array.from(container.querySelectorAll('button')).find(
            (button) => button.textContent?.trim() === 'Edit revision',
        );
        await act(async () => edit?.click());
        await waitUntil(() => expect(container.textContent).toContain('Entity rows use unsupported data'));
        const save = Array.from(container.querySelectorAll('button')).find(
            (button) => button.textContent?.trim() === 'Save immutable revision',
        );
        expect(save?.hasAttribute('disabled')).toBe(true);
    });

    it('accumulates and deduplicates map pages while preserving stable root and focus context', async () => {
        const first = structuredClone(baseSummary);
        first.map.nodes = first.map.nodes.slice(0, 3);
        first.map.edges = first.map.edges.slice(0, 2);
        first.map.truncated = true;
        first.map.next_cursor = 'map:3';
        const second = structuredClone(baseSummary);
        second.map.nodes = [second.map.nodes[0], second.map.nodes[1], second.map.nodes[3]];
        second.map.edges = [second.map.edges[2]];
        second.map.truncated = false;
        second.map.next_cursor = null;
        managerApi.getProjectSummary.mockImplementation((_projectId: string, options: { mapCursor?: string }) => Promise.resolve(options.mapCursor ? second : first));

        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.textContent).toContain('Load next map page'));
        const loadMore = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Load next map page'));
        await act(async () => loadMore?.click());
        await waitUntil(() => expect(container.textContent).toContain('PLM-07 result'));
        expect(container.querySelectorAll('[aria-label="Select DNA Polymerase Design"]')).toHaveLength(1);
        expect(container.querySelectorAll('[aria-label="Select Catalytic-loop redesign"]')).toHaveLength(1);
    });

    it('accumulates bounded run pages and keeps both pages selectable', async () => {
        const first = structuredClone(baseSummary);
        first.runs.next_cursor = 'run:1';
        const second = structuredClone(baseSummary);
        second.runs.items = [{
            ...second.runs.items[0],
            run_id: 'run-2',
            canonical_job_id: 'job-10',
            normalized_state: 'running',
            canonical_state: 'running',
            batch_or_run_group_id: 'batch-2',
        }];
        second.runs.next_cursor = null;
        managerApi.getProjectSummary.mockImplementation((_projectId: string, options: { runCursor?: string }) => Promise.resolve(normalizeProjectManagerReadModel(options.runCursor ? second : first)));

        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.textContent).toContain('Load next run page'));
        const loadMore = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('Load next run page'));
        await act(async () => loadMore?.click());
        await waitUntil(() => expect(container.querySelector('[aria-label="Inspect run run-2"]')).not.toBeNull());
        expect(container.querySelector('[aria-label="Inspect run run-1"]')).not.toBeNull();
        expect(managerApi.getProjectSummary).toHaveBeenCalledWith('project-1', expect.objectContaining({ runCursor: 'run:1' }));
    });

    it('keeps the canonical run contract free of legacy replica aliases', () => {
        const normalized = normalizeProjectManagerReadModel(structuredClone(baseSummary));
        const run = normalized.runs.items[0] as unknown as Record<string, unknown>;
        for (const legacyKey of ['workflow_run_id', 'run_group_id', 'node_id', 'requiredness', 'state', 'generation', 'replicas']) {
            expect(legacyKey in run).toBe(false);
        }
        expect(run.attempts).toHaveLength(2);
        expect(run.replica_index).toBeNull();
    });

    it('synchronizes a keyboard-focusable canonical run with the URL and inspector', async () => {
        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.textContent).toContain('run-1'));
        const run = container.querySelector<HTMLButtonElement>('[aria-label="Inspect run run-1"]');
        expect(run).not.toBeNull();
        await act(async () => {
            run?.focus();
            run?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        });
        await waitUntil(() => expect(container.querySelector('[data-testid="location"]')?.textContent).toContain('selected=workflow_run%3Arun-1'));
        await waitUntil(() => expect(container.querySelector('[aria-label="Selected node inspector"]')?.textContent).toContain('Selected Workflow Run'));
        expect(container.querySelector('[aria-label="Selected node inspector"]')?.textContent).toContain('run-1');
    });

    it('expands a virtual folder as bounded navigation without persisting hierarchy', async () => {
        await renderAt('/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1');
        await waitUntil(() => expect(container.textContent).toContain('Activity'));
        const folder = container.querySelector<HTMLButtonElement>('[aria-label="Expand Activity"]');
        expect(folder).not.toBeNull();
        await act(async () => folder?.click());
        expect(folder?.getAttribute('aria-expanded')).toBe('true');
        await waitUntil(() => expect(container.querySelector('[data-testid="location"]')?.textContent).toContain('selected=virtual_folder%3Adomain-1%3Aactivity'));
        expect(container.querySelector('[aria-label="Collapse Activity"]')?.getAttribute('aria-expanded')).toBe('true');
        await waitUntil(() => expect(container.textContent).toContain('Source attached'));
        await waitUntil(() => expect(container.textContent).toContain('Load more activity'));
        const loadMore = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Load more activity');
        await act(async () => loadMore?.click());
        await waitUntil(() => expect(container.textContent).toContain('Run completed'));
        expect(container.textContent).toContain('Source attached');
        expect(managerApi.getProjectSummary).toHaveBeenCalledWith('project-1', expect.objectContaining({ activityCursor: 'activity:1' }));
    });
});
