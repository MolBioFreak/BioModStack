import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@blueprintjs/core', () => ({ HotkeysProvider: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock('../../src/components/Layout', () => ({ Layout: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    GlobalExperimentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('../../src/runtime/installFeatures', () => ({ useResolvedBmsFeatures: () => ({ features: {}, resolved: true }) }));
vi.mock('../../src/components/NGSToolkit', () => ({ NGSToolkit: () => <div data-testid="ngs-toolkit">NGS result toolkit</div> }));
vi.mock('../../src/components/molbio-ngs/NgsMolBioProjectHub', () => ({ default: () => <div data-testid="project-hub">Project hub</div> }));
vi.mock('../../src/components/molbio-ngs/DomainExperimentWorkspace', () => ({ default: () => <div data-testid="domain-workspace">Domain workspace</div> }));
vi.mock('../../src/components/Dashboard', () => ({ Dashboard: () => <div /> }));
vi.mock('../../src/pages/ProjectManager', () => ({ ProjectManager: () => <div /> }));
vi.mock('../../src/components/JobSubmission', () => ({ JobSubmission: () => <div /> }));
vi.mock('../../src/components/ResultsViewer', () => ({ ResultsViewer: () => <div /> }));
vi.mock('../../src/components/JobDetailPage', () => ({ JobDetailPage: () => <div /> }));
vi.mock('../../src/components/MolBioToolkit/indexV2', () => ({ MolBioToolkitV2: () => <div /> }));
vi.mock('../../src/components/BioXpCockpit', () => ({ BioXpCockpit: () => <div /> }));
vi.mock('../../src/components/InfraMonitorPage', () => ({ InfraMonitorPage: () => <div /> }));
vi.mock('../../src/components/StatsToolkitLauncher', () => ({ StatsToolkitLauncher: () => <div /> }));

import App from '../../src/App';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    document.body.replaceChildren();
});

async function mountAt(path: string) {
    await act(async () => {
        root.render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);
    });
}

describe('NGS result routing', () => {
    it('mounts the exact Job result surface without stale project siblings', async () => {
        await mountAt('/ngs?job_id=31f02bd5-830f-4558-aa78-3873c515de68');
        expect(container.querySelector('[data-testid="ngs-toolkit"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="project-hub"]')).toBeNull();
        expect(container.querySelector('[data-testid="domain-workspace"]')).toBeNull();
    });

    it('mounts project and domain navigation for an unbound NGS page', async () => {
        await mountAt('/ngs');
        expect(container.querySelector('[data-testid="ngs-toolkit"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="project-hub"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="domain-workspace"]')).not.toBeNull();
    });
});
