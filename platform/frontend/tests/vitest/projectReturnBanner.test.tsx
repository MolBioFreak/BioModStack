import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ProjectReturnBanner } from '../../src/components/project-manager/ProjectReturnBanner';
import { getLaunchContext } from '../../src/lib/projectManager';

vi.mock('../../src/lib/projectManager', () => ({
    getLaunchContext: vi.fn(),
}));

const mockedGetLaunchContext = vi.mocked(getLaunchContext);

describe('ProjectReturnBanner', () => {
    let root: Root | undefined;
    let container: HTMLDivElement | undefined;

    beforeEach(() => mockedGetLaunchContext.mockReset());

    afterEach(async () => {
        if (root) await act(async () => root?.unmount());
        container?.remove();
        root = undefined;
        container = undefined;
    });

    async function renderAt(url: string) {
        container = document.createElement('div');
        document.body.appendChild(container);
        root = createRoot(container);
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        await act(async () => {
            root?.render(
                <QueryClientProvider client={client}>
                    <MemoryRouter initialEntries={[url]}>
                        <ProjectReturnBanner />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        return container;
    }

    it('restores only the exact server-resolved Project context', async () => {
        const returnUri = '/projects/project-1?focus=global-1&selected=domain_experiment%3Adomain-1';
        mockedGetLaunchContext.mockResolvedValue({
            schema: 'bms.launch-context.v1',
            launch_context_id: 'launch-1',
            project_id: 'project-1',
            global_experiment_id: 'global-1',
            domain_experiment_id: 'domain-1',
            workflow_id: null,
            workflow_revision_id: null,
            return_uri: returnUri,
            issued_at: '2026-08-09T00:00:00Z',
            expires_at: '2026-08-09T00:30:00Z',
        });
        const rendered = await renderAt('/designs/job-9?launch_context_id=launch-1');
        await vi.waitFor(() => {
            const link = rendered.querySelector<HTMLAnchorElement>('a[aria-label="Return to Project context"]');
            expect(link?.getAttribute('href')).toBe(returnUri);
        });
    });

    it('fails closed for unknown context and caller-authored return_uri', async () => {
        mockedGetLaunchContext.mockResolvedValue({
            schema: 'bms.launch-context.v1',
            launch_context_id: 'unknown',
            project_id: 'project-1',
            global_experiment_id: 'global-1',
            domain_experiment_id: 'domain-1',
            workflow_id: null,
            workflow_revision_id: null,
            return_uri: 'https://evil.example/projects/project-1?focus=x&selected=y',
            issued_at: '2026-08-09T00:00:00Z',
            expires_at: '2026-08-09T00:30:00Z',
        });
        const rendered = await renderAt('/designs/job-9?launch_context_id=unknown');
        await vi.waitFor(() => expect(mockedGetLaunchContext).toHaveBeenCalledWith('unknown', expect.any(AbortSignal)));
        expect(rendered.querySelector('a')).toBeNull();

        await act(async () => {
            root?.render(
                <QueryClientProvider client={new QueryClient()}>
                    <MemoryRouter initialEntries={['/designs/job-9?return_uri=%2Fprojects%2Fproject-1']}>
                        <ProjectReturnBanner />
                    </MemoryRouter>
                </QueryClientProvider>,
            );
        });
        expect(rendered.querySelector('a')).toBeNull();
    });
});
