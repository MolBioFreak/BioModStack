import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const managerMocks = vi.hoisted(() => ({
    searchProjects: vi.fn(),
    listGlobalExperiments: vi.fn(),
    listDomainExperiments: vi.fn(),
    listNgsMolBioProjectLinks: vi.fn(),
    listNgsMolBioShareableResults: vi.fn(),
    createProject: vi.fn(),
    createGlobalExperiment: vi.fn(),
    createDomainExperiment: vi.fn(),
    linkNgsMolBioProject: vi.fn(),
}));

vi.mock('../../src/lib/projectManager', async (importOriginal) => ({
    ...(await importOriginal<Record<string, unknown>>()),
    ...managerMocks,
}));

vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    useGlobalExperimentContext: () => ({
        workspaceId: 'local-project-1',
        globalExperimentId: null,
        domainExperimentId: null,
        stateRevisionId: null,
        selectedDomainExperiment: null,
        availability: { canMutateDomain: false, reason: 'No domain selected.' },
        contextHref: (path: string) => path,
        updateQueryParams: vi.fn(),
    }),
}));

vi.mock('../../src/components/molbio-ngs/DomainExperimentWorkspace', () => ({
    default: () => <div data-testid="domain-experiment-workspace">Domain Experiment Workspace</div>,
}));

import NgsMolBioProjectHub from '../../src/components/molbio-ngs/NgsMolBioProjectHub';

const localProject = {
    id: 'local-project-1',
    name: 'Local sequencing project',
    description: 'Local project objective',
    payload: { research_objective: 'Local project objective' },
};
const globalProject = {
    id: 'global-project-1',
    name: 'Broader project',
    description: 'Broader project objective',
    payload: { research_objective: 'Broader project objective' },
};

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

beforeEach(() => {
    vi.clearAllMocks();
    managerMocks.searchProjects.mockImplementation(async ({ projectScope }: { projectScope: string }) => ({
        items: projectScope === 'global' ? [globalProject] : [localProject],
    }));
    managerMocks.listGlobalExperiments.mockResolvedValue([]);
    managerMocks.listDomainExperiments.mockResolvedValue([]);
    managerMocks.listNgsMolBioProjectLinks.mockResolvedValue([]);
    managerMocks.listNgsMolBioShareableResults.mockResolvedValue([]);
    managerMocks.createProject.mockResolvedValue(localProject);
    managerMocks.createGlobalExperiment.mockResolvedValue({ id: 'experiment-1', name: 'Experiment' });
    managerMocks.createDomainExperiment.mockResolvedValue({ id: 'domain-1', name: 'Domain' });
    managerMocks.linkNgsMolBioProject.mockResolvedValue({});
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
});

async function renderHub() {
    await act(async () => {
        root.render(
            <QueryClientProvider client={queryClient}>
                <NgsMolBioProjectHub presentation="launcher-dialog" />
            </QueryClientProvider>,
        );
        await Promise.resolve();
    });
}

function buttonNamed(name: string) {
    return Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim() === name) ?? null;
}

async function enterValue(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
    await act(async () => {
        const prototype = input instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype, 'value')?.set?.call(input, value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
    });
}

describe('mounted NGS Projects launcher', () => {
    it('does not move focus to the launcher on initial mount', async () => {
        const existingControl = document.createElement('button');
        existingControl.textContent = 'Existing control';
        document.body.appendChild(existingControl);
        existingControl.focus();

        await renderHub();

        expect(document.activeElement).toBe(existingControl);
        existingControl.remove();
    });

    it('opens the approved compact two-tab panel with advanced workspace behind disclosure', async () => {
        await renderHub();
        const launcher = buttonNamed('Projects');
        expect(launcher).not.toBeNull();
        expect(container.querySelector('[role="dialog"]')).toBeNull();

        await act(async () => {
            launcher?.click();
            await Promise.resolve();
        });

        const dialog = container.querySelector<HTMLElement>('[role="dialog"]');
        expect(dialog).not.toBeNull();
        expect(dialog?.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('Local Projects');
        expect(dialog?.textContent).toContain('Broader Projects');
        expect(dialog?.textContent).toContain('Open selected Project');
        expect(dialog?.querySelector('[data-testid="ngs-project-advanced-disclosure"]')).not.toBeNull();
        expect(dialog?.querySelector('details')?.open).toBe(false);
        expect(dialog?.querySelector('[data-testid="domain-experiment-workspace"]')).not.toBeNull();
        expect(dialog?.className).toContain('h-[100dvh]');
        expect(dialog?.querySelectorAll('a[href="/projects"]').length).toBe(1);
        expect(managerMocks.createProject).not.toHaveBeenCalled();

        await act(async () => {
            dialog?.querySelector<HTMLButtonElement>('[role="tab"][aria-controls="ngs-project-panel-broader"]')?.click();
        });
        expect(dialog?.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('Broader Projects');
        expect(dialog?.querySelector('#ngs-project-panel-broader')?.textContent).toContain('Owning broader Project');
    });

    it('keeps advanced metadata and governed exposure controls behind disclosure', async () => {
        managerMocks.listGlobalExperiments.mockResolvedValue([{
            id: 'experiment-1',
            name: 'Local Experiment',
        }]);
        managerMocks.listDomainExperiments.mockResolvedValue([{
            id: 'domain-1',
            name: 'Local Domain',
            domain_kind: 'ngs_molbio',
        }]);
        managerMocks.listNgsMolBioShareableResults.mockResolvedValue([{
            result_receipt_id: 'result-1',
            entity_kind: 'ngs_job',
            entity_id: 'job-1',
            content_digest: '0123456789abcdef',
            store_id: 'jobs',
        }]);

        await renderHub();
        await act(async () => buttonNamed('Projects')?.click());

        const disclosure = container.querySelector<HTMLDetailsElement>('[data-testid="ngs-project-advanced-disclosure"]');
        expect(disclosure?.open).toBe(false);
        await act(async () => disclosure?.querySelector('summary')?.click());
        expect(disclosure?.open).toBe(true);
        expect(disclosure?.textContent).toContain('Description');
        expect(disclosure?.textContent).toContain('Owner');
        expect(disclosure?.textContent).toContain('Contributors, comma separated');
        expect(disclosure?.textContent).toContain('Start date');
        expect(disclosure?.textContent).toContain('Target end date');
        expect(disclosure?.textContent).toContain('Tags, comma separated');
        expect(disclosure?.textContent).toContain('Scientific question');
        expect(disclosure?.textContent).toContain('Hypothesis');
        expect(disclosure?.textContent).toContain('Priority');
        expect(disclosure?.textContent).toContain('Success criteria, one per line');
        expect(disclosure?.textContent).toContain('Optional: expose local Experiments and Results to a broader Project');
        expect(disclosure?.querySelector('[data-testid="domain-experiment-workspace"]')).not.toBeNull();
    });

    it('preserves adding an Experiment to the selected local Project', async () => {
        await renderHub();
        await act(async () => buttonNamed('Projects')?.click());

        const addExperiment = buttonNamed('Add Experiment to selected local Project');
        expect(addExperiment).not.toBeNull();
        await act(async () => addExperiment?.click());

        const experimentName = container.querySelector<HTMLInputElement>('input[placeholder="Validation run"]');
        const objective = container.querySelector<HTMLInputElement>('input[placeholder="Define the sequencing objective…"]');
        expect(experimentName).not.toBeNull();
        expect(objective).not.toBeNull();
        expect(container.querySelector<HTMLInputElement>('input[placeholder="Focused sequencing Project"]')).toBeNull();

        await enterValue(experimentName as HTMLInputElement, 'Existing Project run');
        await enterValue(objective as HTMLInputElement, 'Validate the selected local Project sample');
        await act(async () => buttonNamed('Add contained Experiment')?.click());
        await act(async () => Promise.resolve());

        expect(managerMocks.createProject).not.toHaveBeenCalled();
        expect(managerMocks.createGlobalExperiment).toHaveBeenCalledWith(
            'local-project-1',
            expect.objectContaining({ name: 'Existing Project run' }),
        );
        expect(managerMocks.createDomainExperiment).toHaveBeenCalledWith(
            'local-project-1',
            'experiment-1',
            expect.objectContaining({ name: 'Existing Project run', domain_kind: 'ngs_molbio' }),
        );
    });

    it('preserves draft fields across tabs and discards them after explicit close', async () => {
        await renderHub();
        const launcher = buttonNamed('Projects');
        await act(async () => launcher?.click());

        const projectNameInput = container.querySelector<HTMLInputElement>('input[placeholder="Focused sequencing Project"]');
        const objectiveInput = container.querySelector<HTMLInputElement>('input[placeholder="Define the sequencing objective…"]');
        expect(projectNameInput).not.toBeNull();
        expect(objectiveInput).not.toBeNull();
        await enterValue(projectNameInput as HTMLInputElement, 'Draft local project');
        await enterValue(objectiveInput as HTMLInputElement, 'Draft sequencing objective');

        const disclosure = container.querySelector<HTMLDetailsElement>('[data-testid="ngs-project-advanced-disclosure"]');
        await act(async () => disclosure?.querySelector('summary')?.click());
        const ownerInput = container.querySelector<HTMLInputElement>('input[placeholder="Exact authenticated principal only"]');
        expect(ownerInput).not.toBeNull();
        await enterValue(ownerInput as HTMLInputElement, 'draft-owner');

        await act(async () => container.querySelector<HTMLButtonElement>('[aria-controls="ngs-project-panel-broader"]')?.click());
        await act(async () => container.querySelector<HTMLButtonElement>('[aria-controls="ngs-project-panel-local"]')?.click());
        expect(container.querySelector<HTMLInputElement>('input[placeholder="Focused sequencing Project"]')?.value).toBe('Draft local project');
        expect(container.querySelector<HTMLInputElement>('input[placeholder="Define the sequencing objective…"]')?.value).toBe('Draft sequencing objective');
        expect(container.querySelector<HTMLInputElement>('input[placeholder="Exact authenticated principal only"]')?.value).toBe('draft-owner');

        await act(async () => buttonNamed('Close ×')?.click());
        expect(container.querySelector('[role="dialog"]')).toBeNull();
        expect(document.activeElement).toBe(launcher);

        await act(async () => launcher?.click());
        expect(container.querySelector<HTMLInputElement>('input[placeholder="Focused sequencing Project"]')?.value).toBe('');
        expect(container.querySelector<HTMLInputElement>('input[placeholder="Define the sequencing objective…"]')?.value).toBe('');
        const reopenedDisclosure = container.querySelector<HTMLDetailsElement>('[data-testid="ngs-project-advanced-disclosure"]');
        await act(async () => reopenedDisclosure?.querySelector('summary')?.click());
        expect(container.querySelector<HTMLInputElement>('input[placeholder="Exact authenticated principal only"]')?.value).toBe('');
    });

    it('moves focus into the dialog and traps forward and reverse Tab navigation', async () => {
        await renderHub();
        await act(async () => {
            buttonNamed('Projects')?.click();
            await Promise.resolve();
        });

        const closeButton = buttonNamed('Close ×');
        const globalManager = container.querySelector<HTMLAnchorElement>('a[href="/projects"]');
        expect(document.activeElement).toBe(closeButton);
        expect(globalManager).not.toBeNull();

        globalManager?.focus();
        await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true })));
        expect(document.activeElement).toBe(closeButton);

        closeButton?.focus();
        await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true })));
        expect(document.activeElement).toBe(globalManager);
    });

    it('closes on Escape and returns focus to the launcher', async () => {
        await renderHub();
        const launcher = buttonNamed('Projects');
        await act(async () => launcher?.click());
        expect(container.querySelector('[role="dialog"]')).not.toBeNull();

        await act(async () => {
            document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
            await Promise.resolve();
        });

        expect(container.querySelector('[role="dialog"]')).toBeNull();
        expect(document.activeElement).toBe(launcher);
    });
});
