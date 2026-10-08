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
    createProject,
    createResearchRecord,
    getProjectSummary,
    getResultSurface,
    listDomainAdapters,
    listProjects,
    restoreProject,
    searchAdapterEntities,
    updateProject,
} from '../../src/lib/projectManager';

beforeEach(() => {
    transport.get.mockReset();
    transport.post.mockReset();
    transport.patch.mockReset();
});

describe('Project Manager API contract', () => {
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
            .mockResolvedValueOnce({ data: { subject_id: 'project-1' } })
            .mockResolvedValueOnce({ data: { adapters: [] } })
            .mockResolvedValueOnce({ data: { items: [] } })
            .mockResolvedValueOnce({ data: { route: '/designs/job-9' } });

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
                source_receipt_id: 'external-receipt-9',
                project_head_generation: 4,
            },
        });
        transport.patch.mockResolvedValue({ data: { id: 'project-1' } });

        const receipt = await attachExistingEntity('project-1', 'global-1', 'domain-1', {
            adapter_id: 'core.rfd3-local-redesign.v1',
            entity_id: 'job-9',
            role: 'validated_by',
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
            { adapter_id: 'core.rfd3-local-redesign.v1', entity_id: 'job-9', role: 'validated_by', expected_head_generation: 3 },
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
});
