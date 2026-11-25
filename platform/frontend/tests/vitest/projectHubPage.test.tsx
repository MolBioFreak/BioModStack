import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ProjectManager } from '../../src/pages/ProjectManager';
import * as pm from '../../src/lib/projectManager';
import * as membership from '../../src/lib/molecularProjectMembership';

vi.mock('../../src/lib/projectManager', async () => {
    const actual = await vi.importActual<typeof import('../../src/lib/projectManager')>('../../src/lib/projectManager');
    return {
        ...actual,
        searchProjects: vi.fn(),
        getProjectHub: vi.fn(),
        getProject: vi.fn(),
        archiveProject: vi.fn(),
        restoreProject: vi.fn(),
        createProject: vi.fn(),
        updateProject: vi.fn(),
        listDomainAdapters: vi.fn().mockResolvedValue({ adapters: [] }),
        listProjects: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
        listGlobalExperiments: vi.fn().mockResolvedValue([]),
    };
});

vi.mock('../../src/lib/molecularProjectMembership', async () => {
    const actual = await vi.importActual<typeof import('../../src/lib/molecularProjectMembership')>('../../src/lib/molecularProjectMembership');
    return {
        ...actual,
        removeMolecularProjectMember: vi.fn(),
    };
});

let container: HTMLDivElement;
let root: Root;

async function flush() {
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
    });
}

async function waitUntil(assertion: () => void) {
    for (let i = 0; i < 40; i += 1) {
        try {
            assertion();
            return;
        } catch {
            await flush();
        }
    }
    assertion();
}

function findButtonByText(pattern: RegExp | string): HTMLButtonElement | null {
    const buttons = Array.from(container.querySelectorAll('button'));
    return (
        buttons.find((btn) => {
            const text = (btn.textContent ?? '').trim();
            return typeof pattern === 'string' ? text === pattern : pattern.test(text);
        }) ?? null
    );
}

function findLinkByText(pattern: RegExp | string): HTMLAnchorElement | null {
    const links = Array.from(container.querySelectorAll('a'));
    return (
        links.find((a) => {
            const text = (a.textContent ?? '').trim();
            return typeof pattern === 'string' ? text === pattern : pattern.test(text);
        }) ?? null
    );
}

async function renderAt(initialEntry: string) {
    const client = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    await act(async () => {
        root.render(
            <QueryClientProvider client={client}>
                <MemoryRouter initialEntries={[initialEntry]}>
                    <Routes>
                        <Route path="/projects" element={<ProjectManager />} />
                        <Route path="/projects/:projectId" element={<ProjectManager />} />
                    </Routes>
                </MemoryRouter>
            </QueryClientProvider>,
        );
    });
}

describe('ProjectManager Hub & Index', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        container = document.createElement('div');
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(async () => {
        await act(async () => {
            root.unmount();
        });
        container.remove();
    });

    it('renders single-column projects index with counts and hides ngs_molbio_local in full BMS', async () => {
        vi.mocked(pm.searchProjects).mockResolvedValue({
            capabilities: { has_in_silico: true, has_molbio: true, has_ngs_ont: true, has_stats: false },
            items: [
                {
                    id: 'proj-global-1',
                    kind: 'project',
                    storage_kind: 'workspace',
                    project_id: 'proj-global-1',
                    workspace_id: 'proj-global-1',
                    parent_id: null,
                    current_revision_id: 'rev-1',
                    head_generation: 2,
                    lifecycle_state: 'active',
                    status: 'active',
                    project_scope: 'global',
                    name: 'TNF-alpha Binder Work',
                    description: 'Binder campaign',
                    payload: null,
                    counts: { in_silico: 7, molecular_sequences: 2, molecular_operations: 1, ngs_ont_and_alignments: 3 },
                    created_at: '2026-11-01T00:00:00Z',
                    updated_at: '2026-11-24T00:00:00Z',
                },
                {
                    id: 'proj-local-1',
                    kind: 'project',
                    storage_kind: 'workspace',
                    project_id: 'proj-local-1',
                    workspace_id: 'proj-local-1',
                    parent_id: null,
                    current_revision_id: 'rev-2',
                    head_generation: 1,
                    lifecycle_state: 'active',
                    status: 'active',
                    project_scope: 'ngs_molbio_local',
                    name: 'Hidden Local Project',
                    description: 'Linked inside global',
                    payload: null,
                    counts: { in_silico: 0, molecular_sequences: 1, molecular_operations: 0, ngs_ont_and_alignments: 0 },
                    created_at: '2026-11-01T00:00:00Z',
                    updated_at: '2026-11-24T00:00:00Z',
                },
            ],
            next_cursor: null,
        });

        await renderAt('/projects');
        await waitUntil(() => {
            expect(container.textContent).toContain('TNF-alpha Binder Work');
        });
        expect(container.textContent).not.toContain('Hidden Local Project');
        expect(container.textContent).toContain('In silico');
        expect(container.textContent).toContain('7');
    });

    it('renders collapsed-by-default By type view, switches to By experiment, and detaches molecular sequences', async () => {
        vi.mocked(pm.getProjectHub).mockResolvedValue({
            capabilities: { has_in_silico: true, has_molbio: true, has_ngs_ont: true, has_stats: false },
            project: {
                id: 'proj-1',
                name: 'TNF-alpha Binder Work',
                description: 'Main campaign',
                lifecycle_state: 'active',
                project_scope: 'global',
                created_at: '2026-11-01T00:00:00Z',
                updated_at: '2026-11-24T00:00:00Z',
                experiments: [
                    { id: 'exp-1', name: 'Round 1', status: 'active', item_count: 2 },
                    { id: 'exp-2', name: 'Round 2', status: 'active', item_count: 1 },
                ],
                linked_local_projects: [],
            },
            counts: { in_silico: 1, molecular_sequences: 1, molecular_operations: 0, ngs_ont: 1, alignments: 0 },
            sections: {
                in_silico: [
                    {
                        id: 'job-1',
                        title: 'BindCraft2 Run A',
                        subtitle: '12 designs',
                        kind: 'in_silico',
                        badges: ['bindcraft2'],
                        status: 'completed',
                        experiment_id: 'exp-1',
                        updated_at: '2026-11-20T00:00:00Z',
                        open_href: '/results/job-1',
                        detach: null,
                    },
                ],
                molecular_sequences: [
                    {
                        id: 'seq-1',
                        title: 'pTNF-VHH1',
                        subtitle: '4200 bp · 51.2% GC · 6 features',
                        kind: 'molecular_sequence',
                        badges: ['DNA', 'circular'],
                        status: 'head',
                        experiment_id: 'exp-1',
                        updated_at: '2026-11-21T00:00:00Z',
                        open_href: '/designer?project_id=proj-1&molbio_sequence_id=seq-1',
                        detach: {
                            method: 'remove_molecular_project_member',
                            target_id: 'seq-1',
                            domain_experiment_id: 'dom-1',
                            expected_state_revision_id: 'srev-1',
                        },
                    },
                ],
                molecular_operations: [],
                ngs_ont_and_alignments: [
                    {
                        id: 'aln-1',
                        title: 'ONT Read Verification',
                        subtitle: 'Sample S1',
                        kind: 'ngs_sample',
                        badges: ['ONT sample'],
                        status: 'completed',
                        experiment_id: 'exp-2',
                        updated_at: '2026-11-22T00:00:00Z',
                        open_href: '/ngs?project_id=proj-1',
                        detach: null,
                    },
                ],
            },
        });
        vi.mocked(membership.removeMolecularProjectMember).mockResolvedValue({} as never);

        await renderAt('/projects/proj-1');
        await waitUntil(() => {
            expect(container.textContent).toContain('TNF-alpha Binder Work');
        });

        // Sections collapsed by default
        expect(container.textContent).not.toContain('pTNF-VHH1');

        // Clicking full header bar expands Molecular section
        const molHeader = findButtonByText(/Molecular sequences & constructs/i);
        expect(molHeader).not.toBeNull();
        await act(async () => {
            molHeader?.click();
        });
        expect(container.textContent).toContain('pTNF-VHH1');
        const openToolset = findLinkByText('Open in Molecular Toolset');
        expect(openToolset?.getAttribute('href')).toBe('/designer?project_id=proj-1');

        // Clicking Remove calls removeMolecularProjectMember
        const removeBtn = findButtonByText('Remove');
        expect(removeBtn).not.toBeNull();
        await act(async () => {
            removeBtn?.click();
        });
        await waitUntil(() => {
            expect(membership.removeMolecularProjectMember).toHaveBeenCalledWith('proj-1', 'exp-1', 'dom-1', 'seq-1', 'srev-1');
        });

        // Switch to By experiment view
        const byExpBtn = findButtonByText('By experiment');
        expect(byExpBtn).not.toBeNull();
        await act(async () => {
            byExpBtn?.click();
        });
        expect(findButtonByText(/Round 1/i)).not.toBeNull();
        expect(findButtonByText(/Round 2/i)).not.toBeNull();
    });

    it('capability-gates BNS standalone mode without edition flags', async () => {
        vi.mocked(pm.getProjectHub).mockResolvedValue({
            capabilities: { has_in_silico: false, has_molbio: true, has_ngs_ont: true, has_stats: false },
            project: {
                id: 'proj-bns',
                name: 'Standalone Plasmid Verification',
                description: 'BNS-only project',
                lifecycle_state: 'active',
                project_scope: 'ngs_molbio_local',
                created_at: '2026-11-01T00:00:00Z',
                updated_at: '2026-11-24T00:00:00Z',
                experiments: [{ id: 'exp-1', name: 'Run 1', status: 'active', item_count: 0 }],
                linked_local_projects: [],
            },
            counts: { in_silico: 0, molecular_sequences: 0, molecular_operations: 0, ngs_ont: 0, alignments: 0 },
            sections: { in_silico: [], molecular_sequences: [], molecular_operations: [], ngs_ont_and_alignments: [] },
        });

        await renderAt('/projects/proj-bns');
        await waitUntil(() => {
            expect(container.textContent).toContain('Standalone Plasmid Verification');
        });
        expect(container.textContent).not.toContain('In silico');
        expect(container.textContent).not.toContain('Local NGS/MolBio');
        expect(container.textContent).toContain('Molecular sequences & constructs');
        expect(container.textContent).toContain('NGS / ONT & alignments');
    });
});
