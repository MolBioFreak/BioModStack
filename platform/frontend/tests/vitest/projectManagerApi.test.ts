import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const transport = vi.hoisted(() => ({
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
}));

vi.mock('../../src/lib/api', () => ({ api: transport }));

import {
    archiveProject,
    attachExistingEntity,
    cloneDomainRunIntent,
    createProject,
    createResearchRecord,
    getProjectSummary,
    getResultSurface,
    listDomainAdapters,
    listProjects,
    normalizeProjectManagerReadModel,
    parseLaunchContext,
    restoreProject,
    searchAdapterEntities,
    updateProject,
} from '../../src/lib/projectManager';

beforeEach(() => {
    transport.get.mockReset();
    transport.post.mockReset();
    transport.patch.mockReset();
});

const reconciliation = { state: 'current', last_verified_at: null, reason: null };
const emptyPage = { items: [], next_cursor: null };
const minimalSummary = {
    schema: 'bms.project-manager.read-model.v1', subject_id: 'project-1', subject_generation: 1,
    assembled_at: '2026-08-11T00:00:00Z', source_receipt_ids: [], source_digest_set_sha256: 'a'.repeat(64),
    adapter_versions: [], reconciliation, counts: {}, status_summary: {}, recent_activity: [], result_previews: [],
    pagination: {
        map_next_cursor: null, run_next_cursor: null, result_next_cursor: null, lineage_next_cursor: null,
        note_next_cursor: null, decision_next_cursor: null, dataset_next_cursor: null, activity_next_cursor: null,
        map: { ...emptyPage, repeated_context_node_keys: [] }, runs: emptyPage, results: emptyPage,
        lineage: emptyPage, notes: emptyPage, decisions: emptyPage, datasets: emptyPage, activity: emptyPage,
    },
    project: { id: 'project-1', name: 'Project', objective: '', lifecycle_state: 'active', head_generation: 1, current_revision_id: null, updated_at: '2026-08-11T00:00:00Z' },
    tree: { nodes: [] },
    map: { focus_node_key: 'project:project-1', nodes: [], edges: [], truncated: false, next_cursor: null },
    selection: {
        node_key: 'project:project-1', node_type: 'project', title: 'Project', subtitle: null,
        canonical_identity: {}, summary: {}, relationship: {}, scientific_context: {}, reconciliation,
        available_actions: [], canonical_surface: null,
    },
    runs: emptyPage, warnings: [], allowed_actions: [],
};

const resultSurface = {
    schema: 'bms.result-surface.v1', receipt_id: 'receipt-9', entity_kind: 'design', entity_id: 'job-9',
    contract_id: 'design-v1', content_digest: 'b'.repeat(64), surface_kind: 'protein_design',
    route: { template_id: 'bms.route.design-result.v1', path: '/designs/job-9', query: {} }, readiness: 'ready',
    native_summary: { schema_id: 'bms.result-summary.test.v1', content_sha256: 'c'.repeat(64), canonical_size_bytes: 2, payload: {} },
    scientific_acceptance: { state: 'review', reason: null },
    provenance: { schema_id: 'bms.result-provenance.test.v1', content_sha256: 'd'.repeat(64), canonical_size_bytes: 2, payload: {} },
    comparison: { state: 'not_applicable', reason: null, authority: null },
    available_actions: ['open'],
};

describe('Project Manager API contract', () => {
    it('accepts server-owned parent links on workflow and run map nodes', () => {
        const model = structuredClone(minimalSummary);
        model.map.nodes = [{
            node_key: 'workflow:workflow-1', node_type: 'workflow', label: 'Plan',
            normalized_state: 'active', canonical_identity: { store_id: 'global', entity_id: 'workflow-1' },
            counts: {}, reconciliation, allowed_actions: [], parent_node_key: 'domain_experiment:domain-1',
        }];
        expect(normalizeProjectManagerReadModel(model).map.nodes[0]?.parent_node_key).toBe('domain_experiment:domain-1');
    });

    it('accepts the exact clone-lineage key on result previews and rejects other values', () => {
        const model = structuredClone(minimalSummary);
        model.result_previews = [{ ...resultSurface, lineage_edge_key: 'cloned-plan-intent' }];
        expect(normalizeProjectManagerReadModel(model).result_previews[0]?.lineage_edge_key).toBe('cloned-plan-intent');

        model.result_previews = [{ ...resultSurface, lineage_edge_key: 'foreign-lineage-key' }];
        expect(() => normalizeProjectManagerReadModel(model)).toThrow(/lineage_edge_key/);
    });

    it('rejects malformed source digest-set authority', () => {
        const model = structuredClone(minimalSummary);
        model.source_digest_set_sha256 = 'not-a-digest';
        expect(() => normalizeProjectManagerReadModel(model)).toThrow(/source_digest_set_sha256/);
    });
    it('keeps result-surface and reconciliation contracts closed to the frozen schemas', () => {
        const source = readFileSync(resolve(process.cwd(), 'src/lib/projectManager.ts'), 'utf8');
        expect(source).toContain("export type ResultSurfaceKind = 'protein_design' | 'molecular_dynamics' | 'conformational_mapping' | 'frustrampnn' | 'ngs' | 'molbio' | 'artifact' | 'unsupported';");
        expect(source).toContain("export type ResultReadiness = 'running' | 'partial' | 'ready' | 'failed' | 'blocked' | 'unsupported';");
        expect(source).toContain("export type ScientificAcceptanceState = 'passed' | 'failed' | 'review' | 'unavailable' | 'not_applicable';");
        expect(source).toContain("export type ReconciliationState = 'current' | 'pending' | 'stale' | 'source_unavailable' | 'digest_mismatch';");
        expect(source).not.toMatch(/surface_kind:\s*string/);
        expect(source).not.toMatch(/readiness:[^;]+\|\s*string/);
        expect(source).not.toMatch(/scientific_acceptance:[\s\S]{0,160}\|\s*string/);
    });

    it('does not fabricate authority-bearing inspector selections in the browser', () => {
        const pageSource = readFileSync(resolve(process.cwd(), 'src/pages/ProjectManager.tsx'), 'utf8');
        expect(pageSource).not.toContain('localSelection');
        expect(pageSource).not.toContain('setSelection(nodeKey, kind, null, {');
        expect(pageSource).not.toContain("reconciliation: { state: 'current' }");
    });

    it('binds every dedicated launcher through the server return helper', () => {
        const launcherPaths = [
            'src/components/AntibodyDenovoTemplate.tsx',
            'src/components/StructurePredictionTemplate.tsx',
            'src/components/ProteinModificationTemplate.tsx',
            'src/components/ProteinLocalRedesignTemplate.tsx',
            'src/components/MolecularDynamicsTemplate.tsx',
            'src/components/OligoDesignerTemplate.tsx',
            'src/components/conformationalMapping/ConformationalMappingLauncher.tsx',
        ];
        for (const launcherPath of launcherPaths) {
            expect(readFileSync(resolve(process.cwd(), launcherPath), 'utf8')).toContain('completeCurrentLaunchContext');
        }
        const submissionSource = readFileSync(resolve(process.cwd(), 'src/components/JobSubmission.tsx'), 'utf8');
        expect(submissionSource).not.toContain("queryClient.invalidateQueries({ queryKey: ['jobs'] });\n                                            navigate('/');");
    });

    it('uses one shared attachment interaction from Project Manager, CM, and Sequence-QC', () => {
        const projectPage = readFileSync(resolve(process.cwd(), 'src/pages/ProjectManager.tsx'), 'utf8');
        const cmViewer = readFileSync(resolve(process.cwd(), 'src/components/conformationalMapping/ConformationalMappingViewer.tsx'), 'utf8');
        const sequenceQc = readFileSync(resolve(process.cwd(), 'src/components/ngs/SequenceQcManifestPanel.tsx'), 'utf8');
        for (const source of [projectPage, cmViewer, sequenceQc]) {
            expect(source).toContain('ProjectAttachmentDialog');
            expect(source).not.toContain('AddExistingDialog');
            expect(source).not.toContain('AddToProjectDialog');
        }
    });

    it('uses the canonical bounded project and adapter query parameters', async () => {
        const signal = new AbortController().signal;
        transport.get
            .mockResolvedValueOnce({ data: { items: [{ id: 'project-1' }], next_cursor: null } })
            .mockResolvedValueOnce({ data: minimalSummary })
            .mockResolvedValueOnce({ data: { schema: 'bms.global.adapter-registry.v1', adapters: [] } })
            .mockResolvedValueOnce({ data: { schema: 'bms.global.adapter-search.v1', adapter_id: 'core/rfd3', adapter_version: 1, items: [], next_cursor: null } })
            .mockResolvedValueOnce({ data: resultSurface });

        const projects = await listProjects(signal);
        expect(projects.items).toEqual([{ id: 'project-1' }]);
        await getProjectSummary('project / one', {
            focusId: 'global-1',
            selectedNodeKey: 'domain_experiment:domain-1',
            mapCursor: 'map:50',
            runCursor: 'run:25',
            mapLimit: 50,
            runLimit: 25,
            signal,
        });
        await listDomainAdapters(signal);
        await searchAdapterEntities('core/rfd3', 'polymerase alpha', 25, signal);
        await getResultSurface('project / one', 'receipt/9', signal);

        expect(transport.get.mock.calls).toEqual([
            ['/api/projects', { params: { limit: 100 }, signal }],
            ['/api/projects/project%20%2F%20one/summary', {
                params: {
                    focus_id: 'global-1',
                    selected_node_key: 'domain_experiment:domain-1',
                    map_cursor: 'map:50',
                    run_cursor: 'run:25',
                    map_limit: 50,
                    run_limit: 25,
                },
                signal,
            }],
            ['/api/domain-adapters', { signal }],
            ['/api/domain-adapters/core%2Frfd3/entities/search', {
                params: { q: 'polymerase alpha', limit: 25 },
                signal,
            }],
            ['/api/projects/project%20%2F%20one/receipts/receipt%2F9/surface', { signal }],
        ]);
    });

    it('sends receipt-first attachment and generation-checked management mutations', async () => {
        transport.post.mockResolvedValue({
            data: {
                schema: 'bms.global.attachment-receipt.v1',
                attachment_receipt_id: 'attachment-1',
                project_id: 'project-1', global_experiment_id: 'global-1', domain_experiment_id: 'domain-1',
                adapter_id: 'core.rfd3-local-redesign.v1', adapter_version: 1,
                source_receipt_id: 'external-receipt-9', source_receipt: {}, lineage_edge_id: 'lineage-1',
                operation: 'attach_evidence', role: 'validated_by', note: 'Reviewed',
                project_head_generation: 4, normalized_request_sha256: 'c'.repeat(64), attached_at: '2026-08-11T00:00:00Z',
            },
        });
        transport.patch.mockResolvedValue({ data: { id: 'project-1' } });

        const receipt = await attachExistingEntity('project-1', 'global-1', 'domain-1', {
            adapter_id: 'core.rfd3-local-redesign.v1',
            entity_id: 'job-9',
            operation: 'attach_evidence',
            role: 'validated_by',
            note: 'Reviewed',
            expected_head_generation: 3,
        });
        expect(receipt.source_receipt_id).toBe('external-receipt-9');
        expect(receipt.project_head_generation).toBe(4);
        await createProject({
            schema: 'bms.project.v1',
            name: 'Polymerase program',
            research_objective: 'Improve catalytic stability',
        });
        await updateProject('project-1', { expected_head_generation: 3, name: 'Revised program' });
        await archiveProject('project-1', 4);
        await restoreProject('project-1', 5);
        await createResearchRecord({ projectId: 'project-1' }, {
            record_kind: 'decision',
            body: 'Advance PLM-07.',
        });

        expect(transport.post).toHaveBeenNthCalledWith(1,
            '/api/projects/project-1/experiments/global-1/domains/domain-1/attach',
            { adapter_id: 'core.rfd3-local-redesign.v1', entity_id: 'job-9', operation: 'attach_evidence', role: 'validated_by', note: 'Reviewed', expected_head_generation: 3 },
        );
        expect(transport.post).toHaveBeenNthCalledWith(2, '/api/projects', {
            schema: 'bms.project.v1',
            name: 'Polymerase program',
            research_objective: 'Improve catalytic stability',
        });
        expect(transport.patch).toHaveBeenCalledWith('/api/projects/project-1', {
            expected_head_generation: 3,
            name: 'Revised program',
        });
        expect(transport.post).toHaveBeenNthCalledWith(3, '/api/projects/project-1/archive', { expected_head_generation: 4 });
        expect(transport.post).toHaveBeenNthCalledWith(4, '/api/projects/project-1/restore', { expected_head_generation: 5 });
        expect(transport.post).toHaveBeenNthCalledWith(5, '/api/projects/project-1/records', {
            record_kind: 'decision',
            body: 'Advance PLM-07.',
        });
    });

    it('rejects unknown and malformed authority-bearing response fields at runtime', () => {
        expect(() => normalizeProjectManagerReadModel({ ...minimalSummary, unexpected: true })).toThrow(/not permitted/);
        expect(() => normalizeProjectManagerReadModel({ ...minimalSummary, subject_generation: '1' })).toThrow(/finite number/);
        const launch = {
            schema: 'bms.launch-context.v1', launch_context_id: 'launch-1', project_id: 'project-1',
            global_experiment_id: 'global-1', domain_experiment_id: 'domain-1', workflow_id: null,
            workflow_revision_id: null, return_uri: '/project-manager/projects/project-1', source_receipt_id: 'receipt-1',
            pinned_gpu: null,
            state: 'issued', issued_at: '2026-08-11T00:00:00Z', expires_at: '2026-08-11T01:00:00Z',
        };
        expect(parseLaunchContext(launch).state).toBe('issued');
        expect(() => parseLaunchContext({ ...launch, server_only: true })).toThrow(/not permitted/);
    });

    it('sends the frozen body-authoritative clone request', async () => {
        transport.post.mockResolvedValue({ data: { clone_receipt_id: 'clone-1' } });
        await cloneDomainRunIntent('project-1', 'global-1', 'domain-1', 'group-1', {
            expected_run_group_generation: 3,
            source_run_id: 'run-1',
            source_attempt_id: 'attempt-1',
            new_workflow_name: 'Cloned ubiquitin intent',
            change_summary: 'Clone exact immutable intent',
            expected_domain_revision_id: 'domain-revision-1',
        });
        expect(transport.post).toHaveBeenCalledWith(
            '/api/projects/project-1/experiments/global-1/domains/domain-1/run-groups/group-1/clone',
            expect.objectContaining({
                schema: 'bms.run-clone-request.v1',
                new_workflow_name: 'Cloned ubiquitin intent',
                idempotency_key: expect.any(String),
            }),
        );
        expect(transport.post.mock.calls[0]?.[1]).not.toHaveProperty('name');
    });
});
