import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const transport = vi.hoisted(() => ({
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
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
import * as projectManagerContract from '../../src/lib/projectManager';

beforeEach(() => {
    transport.get.mockReset();
    transport.post.mockReset();
    transport.put.mockReset();
    transport.delete.mockReset();
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
    project: { id: 'project-1', project_scope: 'global', name: 'Project', objective: '', lifecycle_state: 'active', head_generation: 1, current_revision_id: null, updated_at: '2026-08-11T00:00:00Z' },
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


















    it('uses one shared attachment interaction from Project Manager and CM', () => {
        const projectPage = readFileSync(resolve(process.cwd(), 'src/pages/ProjectManager.tsx'), 'utf8');
        const cmViewer = readFileSync(resolve(process.cwd(), 'src/components/conformationalMapping/ConformationalMappingViewer.tsx'), 'utf8');
        for (const source of [projectPage, cmViewer]) {
            expect(source).toContain('ProjectAttachmentDialog');
            expect(source).not.toContain('AddExistingDialog');
            expect(source).not.toContain('AddToProjectDialog');
        }
    });








});
