import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const managerApi = vi.hoisted(() => ({
    listProjects: vi.fn(),
    searchProjects: vi.fn(),
    getProjectSummary: vi.fn(),
    listDomainAdapters: vi.fn(),
    searchAdapterEntities: vi.fn(),
    issueAdapterReceipt: vi.fn(),
    attachExistingEntity: vi.fn(),
    getResultSurface: vi.fn(),
    createLaunchContext: vi.fn(),
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
    projectManagerErrorMessage: vi.fn((error: unknown) => error instanceof Error ? error.message : String(error)),
    isPermissionError: vi.fn(() => false),
}));

vi.mock('../../src/lib/projectManager', async (importOriginal) => ({
    ...(await importOriginal<Record<string, unknown>>()),
    ...managerApi,
}));

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
        id: 'project-1', name: 'DNA Polymerase Design', objective: 'Improve catalytic stability',
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
    Object.values(managerApi).forEach((mock) => mock.mockReset());
    managerApi.projectManagerErrorMessage.mockImplementation((error: unknown) => error instanceof Error ? error.message : String(error));
    managerApi.isPermissionError.mockReturnValue(false);
    managerApi.listProjects.mockResolvedValue({ items: [project], next_cursor: null });
    managerApi.searchProjects.mockResolvedValue({ items: [project], next_cursor: null });
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
    managerApi.searchAdapterEntities.mockResolvedValue({ schema: 'bms.global.adapter-search.v1', adapter_id: 'bms.rfd3.local-redesign-reference.adapter.v1', adapter_version: '1', items: [{ adapter_id: 'bms.rfd3.local-redesign-reference.adapter.v1', entity_kind: 'rfd3_local_redesign_request', entity_id: 'request-10', label: 'PLM-07 redesign', canonical_state: 'completed', attachable: true, reason: null, reopen_uri: '/designs/job-9', metadata: { created_at: '2026-08-09' } }], next_cursor: null });
    managerApi.attachExistingEntity.mockResolvedValue({ source_receipt_id: 'receipt-9' });
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
        await waitUntil(() => expect(container.textContent).toContain('Add existing'));

        const addButton = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Add existing');
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
        const search = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Search Protein sources');
        await act(async () => search?.click());
        await waitUntil(() => expect(container.textContent).toContain('Ubiquitin 1UBQ'));
        const choice = container.querySelector<HTMLInputElement>('input[type="radio"][value="job-1ubq"]');
        await act(async () => choice?.click());
        const issue = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Verify and use receipt');
        await act(async () => issue?.click());
        await waitUntil(() => expect(managerApi.issueAdapterReceipt).toHaveBeenCalledWith(
            'bms.core-job.esmfold2.adapter.v1', 'job-1ubq', 'project-1',
        ));
        expect(container.querySelector<HTMLInputElement>('[aria-label="Protein source receipt IDs"]')?.value).toBe('receipt-1ubq');
        expect(container.querySelector<HTMLInputElement>('[aria-label="Protein expected content SHA-256"]')?.value).toBe('d'.repeat(64));
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
        expect(container.textContent).not.toContain('First bounded page');
        await waitUntil(() => expect(container.textContent).toContain('Source Attached'));
        await waitUntil(() => expect(container.textContent).toContain('Load more activity'));
        const loadMore = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Load more activity');
        await act(async () => loadMore?.click());
        await waitUntil(() => expect(container.textContent).toContain('Run Completed'));
        expect(container.textContent).toContain('Source Attached');
        expect(managerApi.getProjectSummary).toHaveBeenCalledWith('project-1', expect.objectContaining({ activityCursor: 'activity:1' }));
    });
});
