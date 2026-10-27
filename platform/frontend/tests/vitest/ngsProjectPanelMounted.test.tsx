import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const contextState = vi.hoisted(() => ({
    current: {
        workspaceId: null as string | null,
        globalExperimentId: null as string | null,
        domainExperimentId: null as string | null,
        stateRevisionId: null as string | null,
        selectedDomainExperiment: null as null | { domain_experiment_id: string },
        selectedWorkspace: null as null | { name: string },
    },
}));

vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    useGlobalExperimentContext: () => ({
        ...contextState.current,
        availability: { canMutateDomain: false, reason: 'No domain selected.' },
        contextHref: (path: string) => path,
        updateQueryParams: vi.fn(),
    }),
}));

vi.mock('../../src/components/molbio-ngs/DomainExperimentWorkspace', () => ({
    default: () => <div data-testid="domain-experiment-workspace">Scientific NGS/MolBio workspace</div>,
}));

import NgsMolBioProjectHub from '../../src/components/molbio-ngs/NgsMolBioProjectHub';

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

beforeEach(() => {
    vi.clearAllMocks();
    contextState.current = {
        workspaceId: null,
        globalExperimentId: null,
        domainExperimentId: null,
        stateRevisionId: null,
        selectedDomainExperiment: null,
        selectedWorkspace: null,
    };
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
});

async function renderHub(presentation: 'inline' | 'launcher-dialog' | 'viewer' = 'inline') {
    await act(async () => {
        root.render(<QueryClientProvider client={queryClient}><NgsMolBioProjectHub presentation={presentation} /></QueryClientProvider>);
        await Promise.resolve();
    });
}

describe('NGS/MolBio Project context bridge', () => {

    it('shows selected Project context, dedicated-manager navigation, and the scientific workspace', async () => {
        contextState.current = {
            workspaceId: 'local-project-1',
            globalExperimentId: 'experiment-1',
            domainExperimentId: 'domain-1',
            stateRevisionId: 'state-1',
            selectedDomainExperiment: { domain_experiment_id: 'domain-1' },
            selectedWorkspace: { name: 'Syenex New Plasmids' },
        };
        await renderHub();
        expect(container.textContent).toContain('Syenex New Plasmids');
        expect(container.querySelector<HTMLAnchorElement>('a[href^="/projects/local-project-1"]')?.textContent).toBe('Open in Project Manager');
        expect(container.querySelector('[data-testid="domain-experiment-workspace"]')).not.toBeNull();
        expect(container.textContent).not.toContain('Create governed link');
    });

});


it('uses one compact Project row in the viewer and keeps linked data behind an expandable disclosure', async () => {
    await renderHub('viewer');
    expect(container.querySelectorAll('a')).toHaveLength(1);
    expect(container.textContent).toContain('No Project selected');
    expect(container.textContent).not.toContain('Choose an NGS/MolBio Project');
    expect(container.querySelector('details')).toBeNull();
    contextState.current = {workspaceId:'p',globalExperimentId:'e',domainExperimentId:'d',stateRevisionId:'s',selectedDomainExperiment:{domain_experiment_id:'d'},selectedWorkspace:{name:'Plasmids'}};
    await renderHub('viewer');
    const details=container.querySelector('details')!;
    expect(details.open).toBe(false);
    await act(()=>details.querySelector('summary')!.click());
    expect(details.open).toBe(true);
    expect(details.querySelector('[data-testid="domain-experiment-workspace"]')).not.toBeNull();
    expect(container.querySelector('a')!.getAttribute('href')).toContain('state_revision_id=s');
});
