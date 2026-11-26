// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const mockGet = vi.hoisted(() => vi.fn());
const mockFetchMolBioNgsProjectExperiments = vi.hoisted(() => vi.fn());
const mockGetNgsMolBioBinding = vi.hoisted(() => vi.fn());

vi.mock('../../src/lib/api', () => ({
    api: { get: (...args: unknown[]) => mockGet(...args) },
    fetchMolBioNgsProjectExperiments: (...args: unknown[]) => mockFetchMolBioNgsProjectExperiments(...args),
    VisibleApiError: class VisibleApiError extends Error {
        status: number | null;
        detail: string;
        constructor(message: string, status: number | null, detail: string) {
            super(message);
            this.status = status;
            this.detail = detail;
        }
    },
}));

vi.mock('../../src/lib/projectManager', () => ({
    getNgsMolBioBinding: (...args: unknown[]) => mockGetNgsMolBioBinding(...args),
}));

import { GlobalExperimentProvider, useGlobalExperimentContext } from '../../src/components/experiments/GlobalExperimentContext';

function Probe() {
    const ctx = useGlobalExperimentContext();
    return (
        <div>
            <div data-testid="status">{ctx.availability.status}</div>
            <div data-testid="can-mutate">{String(ctx.availability.canMutateDomain)}</div>
            <div data-testid="workspace">{ctx.selectedWorkspace?.name ?? ''}</div>
            <div data-testid="experiment">{ctx.selectedGlobalExperiment?.name ?? ''}</div>
            <div data-testid="domain">{ctx.selectedDomainExperiment?.title ?? ''}</div>
        </div>
    );
}

async function flushAsync(turns = 10) {
    for (let i = 0; i < turns; i += 1) {
        await act(async () => {
            await Promise.resolve();
            await new Promise((r) => setTimeout(r, 10));
        });
    }
}

describe('GlobalExperimentContext (/api/projects migration)', () => {
    let container: HTMLDivElement | null = null;
    let root: Root | null = null;

    beforeEach(() => {
        mockGet.mockReset();
        mockFetchMolBioNgsProjectExperiments.mockReset();
        mockGetNgsMolBioBinding.mockReset();
    });

    afterEach(async () => {
        if (root) {
            await act(async () => { root?.unmount(); });
            root = null;
        }
        container?.remove();
        container = null;
    });

    it('loads projects and global experiments from /api/projects without calling /api/experiment-workspaces', async () => {
        mockGet.mockImplementation(async (url: string) => {
            if (url === '/api/projects') {
                return {
                    data: {
                        items: [{
                            id: 'proj-1',
                            project_scope: 'global',
                            name: 'Project One',
                            objective: 'Obj',
                            lifecycle_state: 'active',
                            head_generation: 1,
                            current_revision_id: 'rev-p1',
                            updated_at: '2026-11-26T00:00:00Z',
                        }],
                        next_cursor: null,
                    },
                };
            }
            if (url === '/api/projects/proj-1/experiments') {
                return {
                    data: {
                        items: [{
                            id: 'exp-1',
                            kind: 'global_experiment',
                            project_id: 'proj-1',
                            parent_id: 'proj-1',
                            name: 'Experiment One',
                            lifecycle_state: 'active',
                            head_generation: 1,
                            current_revision_id: 'rev-e1',
                            updated_at: '2026-11-26T00:00:00Z',
                        }],
                        next_cursor: null,
                    },
                };
            }
            throw new Error(`Unexpected URL: ${url}`);
        });
        mockFetchMolBioNgsProjectExperiments.mockResolvedValue([{
            domain_experiment_id: 'dom-1',
            project_id: 'proj-1',
            global_experiment_id: 'exp-1',
            global_domain_experiment_revision_id: 'rev-d1',
            title: 'Domain One',
            domain_key: 'ngs_molbio',
            status: 'active',
            lifecycle_state: 'active',
            current_state_revision_id: 'state-1',
            created_at: '2026-11-26T00:00:00Z',
            updated_at: '2026-11-26T00:00:00Z',
        }]);
        mockGetNgsMolBioBinding.mockResolvedValue({
            schema: 'bms.ngs-molbio-binding.v1',
            project_id: 'proj-1',
            global_experiment_id: 'exp-1',
            domain_experiment_id: 'dom-1',
            domain_revision_id: 'rev-d1',
            provisioning_state: 'ready',
            command_state: 'applied',
            global_receipt_id: 'rec-1',
            acknowledgement_id: 'ack-1',
        });

        const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        container = document.createElement('div');
        document.body.appendChild(container);
        root = createRoot(container);
        const amp = String.fromCharCode(38);
        await act(async () => {
            root!.render(
                <QueryClientProvider client={queryClient}>
                    <MemoryRouter initialEntries={[`/molbio-ngs?workspace_id=proj-1${amp}global_experiment_id=exp-1${amp}domain_experiment_id=dom-1`]}>
                        <GlobalExperimentProvider>
                            <Probe />
                        </GlobalExperimentProvider>
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        await flushAsync();

        expect(container.querySelector('[data-testid="status"]')?.textContent).toBe('available');
        expect(container.querySelector('[data-testid="can-mutate"]')?.textContent).toBe('true');
        expect(container.querySelector('[data-testid="workspace"]')?.textContent).toBe('Project One');
        expect(container.querySelector('[data-testid="experiment"]')?.textContent).toBe('Experiment One');
        expect(container.querySelector('[data-testid="domain"]')?.textContent).toBe('Domain One');
        const calledUrls = mockGet.mock.calls.map((c) => String(c[0]));
        expect(calledUrls).toContain('/api/projects');
        expect(calledUrls).toContain('/api/projects/proj-1/experiments');
        expect(calledUrls.some((u) => u.includes('experiment-workspaces'))).toBe(false);
    });
});
