import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
    fetchProjectHub: vi.fn(),
    updateProjectHubPlasmidInfo: vi.fn(),
    fetchMolBioNgsDomainState: vi.fn(),
    fetchMolBioNgsStateRevisions: vi.fn(),
    fetchMolBioNgsStateRevision: vi.fn(),
    fetchMolBioNgsSamples: vi.fn(),
    fetchMolBioNgsReferences: vi.fn(),
    fetchMolBioNgsEvidence: vi.fn(),
}));
const managerMocks = vi.hoisted(() => ({ getProject: vi.fn(), listProjects: vi.fn(), listDomainAdapters: vi.fn() }));
const contextMocks = vi.hoisted(() => ({
    updateQueryParams: vi.fn(),
    setStateRevisionId: vi.fn(),
}));

vi.mock('../../src/lib/api', async (importOriginal) => ({
    ...(await importOriginal<Record<string, unknown>>()),
    ...apiMocks,
}));
vi.mock('../../src/lib/projectManager', async (importOriginal) => ({
    ...(await importOriginal<Record<string, unknown>>()),
    ...managerMocks,
}));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({
    useGlobalExperimentContext: () => ({
        workspaceId: 'project-1',
        globalExperimentId: 'experiment-1',
        domainExperimentId: 'domain-1',
        stateRevisionId: new URLSearchParams(window.location.search).get('state_revision_id') || 'state-current',
        selectedDomainExperiment: {
            project_id: 'project-1',
            global_experiment_id: 'experiment-1',
            domain_experiment_id: 'domain-1',
            global_domain_experiment_revision_id: 'domain-revision-1',
            local_state_revision_id: 'state-current',
            local_state_head_generation: 4,
            local_counts: { samples: 0, references: 0, evidence_assessments: 0 },
        },
        availability: {
            status: 'available',
            canMutateDomain: true,
            localBinding: 'acknowledged',
            globalAdapter: 'available',
            reason: 'Ready.',
            error: null,
        },
        setStateRevisionId: contextMocks.setStateRevisionId,
        updateQueryParams: contextMocks.updateQueryParams,
        contextHref: (pathname: string, updates: Record<string, string | null | undefined> = {}) => {
            const query = new URLSearchParams(window.location.search);
            for (const [key, value] of Object.entries(updates)) {
                if (value === null || value === undefined || value === '') query.delete(key);
                else query.set(key, value);
            }
            const search = query.toString();
            return `${pathname}${search ? `?${search}` : ''}`;
        },
    }),
}));

import DomainExperimentWorkspace from '../../src/components/molbio-ngs/DomainExperimentWorkspace';
import { projectHubPlasmidsToConstructShelf } from '../../src/components/MolBioToolkit/utils/projectConstructShelf';

const plasmids = [
    {
        sequence_id: 'sequence-pl1480', revision_id: 'revision-pl1480', revision_number: 1,
        receipt_id: 'receipt-pl1480', receipt_sha256: 'receipt-sha-pl1480', content_digest: 'content-sha-pl1480',
        source_store_id: 'molbio', schema_name: 'bms.molecular-revision.v1',
        name: 'PL1480', description: 'Synthetic circular DNA', availability: 'available', unavailable_reason: null,
        length_bp: 5512, gc_percent: 53.52, feature_count: 10,
        feature_labels: ['NeoR/KanR', 'CMV promoter', 'f1 ori', 'SV40 ori'],
        cmv_promoter: true, neor_kanr: true, replication_origin_count: 3,
        saved_experiment_count: 0, organism_host_context: null, project_tags: ['new plasmid'], project_notes: '',
        reopen_href: '/designer?workspace_id=project-1&global_experiment_id=experiment-1&domain_experiment_id=domain-1&state_revision_id=state-current&section=plasmids&molbio_sequence_id=sequence-pl1480&molbio_revision_id=revision-pl1480',
        map_segments: [{ start: 5300, end: 120, tone: 'accent', label: 'NeoR/KanR', feature_type: 'CDS', strand: 'reverse' }],
    },
    {
        sequence_id: 'sequence-pl2190', revision_id: 'revision-pl2190', revision_number: 1,
        receipt_id: 'receipt-pl2190', receipt_sha256: 'receipt-sha-pl2190', content_digest: 'content-sha-pl2190',
        source_store_id: 'molbio', schema_name: 'bms.molecular-revision.v1',
        name: 'PL2190', description: 'Synthetic circular DNA', availability: 'available', unavailable_reason: null,
        length_bp: 5759, gc_percent: 47.32, feature_count: 8,
        feature_labels: ['CMV promoter', 'ori', 'NeoR/KanR'],
        cmv_promoter: true, neor_kanr: true, replication_origin_count: 1,
        saved_experiment_count: 1, organism_host_context: null, project_tags: [], project_notes: '',
        reopen_href: '/designer?workspace_id=project-1&global_experiment_id=experiment-1&domain_experiment_id=domain-1&state_revision_id=state-current&section=plasmids&molbio_sequence_id=sequence-pl2190&molbio_revision_id=revision-pl2190',
        map_segments: [{ start: 700, end: 1400, tone: 'success' }],
    },
];

const readModel = {
    schema: 'bms.project-hub.v1',
    project: {
        id: 'project-1', name: 'Syenex New Plasmids', objective: 'Routine new plasmid onboarding.',
        lifecycle_state: 'active', created_at: '2026-08-24T12:00:00Z', plasmid_count: 2,
        settings_href: '/projects/project-1', add_plasmid_href: '/designer?workspace_id=project-1&section=plasmids&action=add-plasmid',
    },
    identity: {
        workspace_id: 'project-1', global_experiment_id: 'experiment-1', domain_experiment_id: 'domain-1',
        selected_state_revision_id: 'state-current', current_state_revision_id: 'state-current', state_head_generation: 4,
        global_domain_revision_id: 'domain-revision-1', membership_graph_sha256: 'membership-digest',
        binding_status: 'acknowledged', adapter_status: 'available',
    },
    plasmids,
    sequence_data: {
        items: [],
        import_href: '/ngs?workspace_id=project-1&global_experiment_id=experiment-1&domain_experiment_id=domain-1&state_revision_id=state-current&section=sequence-data&action=import-ont',
        launcher_href: '/ngs?workspace_id=project-1&global_experiment_id=experiment-1&domain_experiment_id=domain-1&state_revision_id=state-current&section=sequence-data',
    },
    experiments: [
        { id: 'pcr-1', persistence: 'saved', kind: 'pcr', plasmid_sequence_id: 'sequence-pl2190', plasmid_sequence_ids: ['sequence-pl2190'], plasmid_name: 'PL2190', title: 'Validation PCR', status: 'saved', created_at: '2026-08-25T12:00:00Z', reopen_href: '/designer?pcr_experiment_id=pcr-1&pcr_revision_id=pcr-revision-1' },
        { id: 'alignment-draft', persistence: 'unsaved', kind: 'alignment', plasmid_sequence_id: 'sequence-pl1480', plasmid_sequence_ids: ['sequence-pl1480', 'sequence-pl2190'], plasmid_name: 'PL1480 / PL2190', title: 'Transient alignment', status: 'draft', created_at: '2026-08-25T12:00:00Z', reopen_href: null },
    ],
    results: [],
    activity: [
        { id: 'activity-1', summary: 'PL1480 added to the project', occurred_at: '2026-08-24T12:00:00Z', technical_event_type: 'molecular_member_attached', receipt_id: 'receipt-1', envelope_sha256: 'event-digest' },
    ],
};

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, '', '/designer?workspace_id=project-1&global_experiment_id=experiment-1&domain_experiment_id=domain-1&state_revision_id=state-current');
    apiMocks.fetchProjectHub.mockResolvedValue(readModel);
    apiMocks.updateProjectHubPlasmidInfo.mockResolvedValue(readModel);
    apiMocks.fetchMolBioNgsDomainState.mockResolvedValue({ current_state_revision_id: 'state-current', head_generation: 4 });
    apiMocks.fetchMolBioNgsStateRevisions.mockResolvedValue([]);
    apiMocks.fetchMolBioNgsStateRevision.mockResolvedValue({ id: 'state-current', members: [], payload: { acquisition_policy: {}, assessment_policy: {} } });
    apiMocks.fetchMolBioNgsSamples.mockResolvedValue([]);
    apiMocks.fetchMolBioNgsReferences.mockResolvedValue([]);
    apiMocks.fetchMolBioNgsEvidence.mockResolvedValue([]);
    managerMocks.getProject.mockResolvedValue({ id: 'project-1', name: 'Syenex New Plasmids', payload: { project_scope: 'ngs_molbio_local' } });
    managerMocks.listProjects.mockResolvedValue({ items: [], next_cursor: null });
    managerMocks.listDomainAdapters.mockResolvedValue({ schema: 'bms.global.adapter-registry.v1', adapters: [] });
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 60_000 }, mutations: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
});

async function renderWorkspace(search?: string) {
    if (search) window.history.replaceState({}, '', `/designer?${search}`);
    await act(async () => {
        root.render(
            <MemoryRouter>
                <QueryClientProvider client={queryClient}>
                    <DomainExperimentWorkspace />
                </QueryClientProvider>
            </MemoryRouter>,
        );
    });
    for (let attempt = 0; attempt < 20 && !container.textContent?.includes('Syenex New Plasmids'); attempt += 1) {
        await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    }
}

function buttonNamed(name: string) {
    return Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim() === name) ?? null;
}

describe('mounted MolBio project hub', () => {
    it('builds the default Construct Shelf only from exact Project membership', () => {
        const shelf = projectHubPlasmidsToConstructShelf(readModel);
        expect(shelf.map((item) => item.name)).toEqual(['PL1480', 'PL2190']);
        expect(shelf.map((item) => item.name)).not.toContain('pGM12_pEb-HS2-fluc');
        expect(shelf[0]?.revision_id).toBe('revision-pl1480');
        expect(shelf[0]?.reopen_href).toContain('molbio_revision_id=revision-pl1480');
    });
    it('leads with the approved project header, tab order, extended plasmid cards, and collapsed technical details', async () => {
        await renderWorkspace();

        expect(container.querySelector('h1')?.textContent).toBe('Syenex New Plasmids');
        expect(Array.from(container.querySelectorAll('[role="tab"]')).map((tab) => tab.textContent?.trim())).toEqual([
            'Overview', 'Plasmids', 'Sequence Data', 'Experiments', 'Results', 'Activity',
        ]);
        expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('Overview');
        expect(container.textContent).toContain('Routine new plasmid onboarding.');
        expect(container.querySelector('header')?.textContent).toContain('Local NGS / Mol Bio project');
        expect(container.querySelector('header')?.textContent).not.toContain('state-current');
        expect(container.textContent).toContain('PL1480');
        expect(container.textContent).toContain('5,512 bp');
        expect(container.textContent).toContain('53.52%');
        expect(container.textContent).toContain('10 features');
        expect(container.textContent).toContain('Saved Mol Bio experiments');
        expect(container.textContent).toContain('No sequencing data attached');
        const technicalDetails = container.querySelector<HTMLDetailsElement>('details[data-testid="project-technical-details"]');
        expect(technicalDetails?.open).toBe(false);
        technicalDetails?.setAttribute('open', '');
        expect(technicalDetails?.querySelector('button[aria-label="Copy Project / workspace ID"]')).not.toBeNull();
        expect(technicalDetails?.querySelector('button[aria-label="Copy Selected state revision"]')).not.toBeNull();
        expect(technicalDetails?.querySelector('button[aria-label="Copy PL1480 receipt ID"]')).not.toBeNull();
        const plasmidCards = Array.from(container.querySelectorAll('article')).filter((article) =>
            ['PL1480', 'PL2190'].includes(article.querySelector('h3')?.textContent ?? ''),
        );
        expect(plasmidCards[0]?.parentElement?.className.split(/\s+/)).toContain('lg:grid-cols-2');
        expect(plasmidCards[0]?.parentElement?.className.split(/\s+/)).not.toContain('md:grid-cols-2');
        expect(plasmidCards[0]?.parentElement?.className.split(/\s+/)).toContain('xl:grid-cols-4');
        await act(async () => buttonNamed('Compare all 2')?.click());
        expect(contextMocks.updateQueryParams).toHaveBeenCalledWith({ section: 'plasmids', plasmid: null });
        await act(async () => buttonNamed('Compare')?.click());
        expect(contextMocks.updateQueryParams).toHaveBeenCalledWith({ section: 'plasmids', plasmid: 'sequence-pl1480' });
        expect(Array.from(container.querySelectorAll('a[href*="molbio_sequence_id=sequence-pl1480"][href*="molbio_revision_id=revision-pl1480"]')).some((link) => link.textContent?.includes('Open plasmid'))).toBe(true);
        const miniMap = container.querySelector<HTMLAnchorElement>('a[data-testid="plasmid-mini-map"][href*="molbio_sequence_id=sequence-pl1480"]');
        expect(miniMap?.getAttribute('aria-label')).toBe('Open full plasmid map for PL1480, 5,512 bp');
        expect(miniMap?.querySelector('title')?.textContent).toContain('NeoR/KanR');
        expect(miniMap?.querySelector('[data-feature-direction="reverse"]')).not.toBeNull();
        expect(miniMap?.querySelector('[data-feature-label="NeoR/KanR"][tabindex="0"]')).not.toBeNull();
        expect(miniMap?.querySelector('circle[stroke-dasharray^="0 "]')).toBeNull();
        const attachButton = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim() === 'Add current work to Project');
        expect(attachButton).toBeDefined();
        await act(async () => attachButton?.click());
        await act(async () => { await Promise.resolve(); });
        expect(container.textContent).toContain('PL1480 saved revision 1');
        expect(container.textContent).not.toContain('Canonical source adapter');
    });

    it('renders an explicit responsive comparison surface and stacked plasmid records', async () => {
        await renderWorkspace('workspace_id=project-1&global_experiment_id=experiment-1&domain_experiment_id=domain-1&state_revision_id=state-current&section=plasmids&plasmid=sequence-pl1480');
        const comparison = container.querySelector('[data-testid="project-plasmid-comparison"]');
        expect(comparison?.textContent).toContain('Compare PL1480 with project plasmids');
        expect(comparison?.textContent).toContain('PL2190');
        expect(container.querySelector('[data-testid="project-plasmid-stacked-records"]')?.className.split(/\s+/)).toContain('lg:hidden');
        expect(container.querySelector('[data-testid="project-plasmid-desktop-table"]')?.className.split(/\s+/)).toContain('hidden');
    });

    it('keeps an unavailable molecular member visible with a per-card failure state', async () => {
        apiMocks.fetchProjectHub.mockResolvedValueOnce({
            ...readModel,
            plasmids: [...readModel.plasmids, {
                ...readModel.plasmids[0], sequence_id: 'missing-sequence', revision_id: 'missing-revision',
                receipt_id: 'missing-receipt', name: 'missing-sequence', availability: 'unavailable',
                unavailable_reason: 'Molecular member unavailable', length_bp: 0, feature_count: 0,
                feature_labels: [], map_segments: [],
            }],
        });
        await renderWorkspace();
        const card = Array.from(container.querySelectorAll('article')).find((item) => item.textContent?.includes('missing-sequence'));
        expect(card?.querySelector('[role="alert"]')?.textContent).toContain('Molecular member unavailable');
        expect(card?.querySelector<HTMLButtonElement>('button:last-child')?.disabled).toBe(true);
    });

    it('renders Sequence Data exclusively as the honest ONT empty state with exact supported routes', async () => {
        await renderWorkspace('workspace_id=project-1&global_experiment_id=experiment-1&domain_experiment_id=domain-1&state_revision_id=state-current&section=sequence-data');

        expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('Sequence Data');
        expect(container.textContent).toContain('No ONT sequencing data attached');
        expect(container.textContent).toContain('Runs and read sets');
        expect(container.textContent).toContain('Alignment and coverage');
        expect(container.textContent).toContain('Clone assessment');
        expect(container.textContent).toContain('Viewer evidence');
        expect(container.querySelector('a[href*="action=import-ont"]')?.textContent).toContain('Import ONT data');
        expect(container.querySelector('a[href^="/ngs?"][href*="state_revision_id=state-current"]:not([href*="action=import-ont"])')?.textContent).toContain('Open NGS launcher');
        expect(container.textContent).not.toContain('canonical plasmid sequence data');
    });

    it('shows only persisted saved work and filters it by plasmid through readable query state', async () => {
        await renderWorkspace('workspace_id=project-1&global_experiment_id=experiment-1&domain_experiment_id=domain-1&state_revision_id=state-current&section=experiments&plasmid=sequence-pl2190');

        expect(container.textContent).toContain('Validation PCR');
        expect(container.textContent).not.toContain('Transient alignment');
        expect(container.querySelector('[aria-pressed="true"]')?.textContent).toBe('PL2190');
        expect(container.textContent).toContain('1 saved');
        expect(container.textContent).toContain('0 saved');

        await act(async () => buttonNamed('PL1480')?.click());
        expect(contextMocks.updateQueryParams).toHaveBeenCalledWith({ plasmid: 'sequence-pl1480' });
    });

    it('opens the readable edit dialog, traps focus, closes on Escape, and restores the invoking control', async () => {
        await renderWorkspace();
        const edit = buttonNamed('Edit info');
        expect(edit).not.toBeNull();

        await act(async () => { edit?.click(); await Promise.resolve(); });
        const dialog = container.querySelector<HTMLElement>('[role="dialog"]');
        expect(dialog?.getAttribute('aria-labelledby')).toBe('project-plasmid-edit-title');
        expect(dialog?.getAttribute('aria-describedby')).toBe('project-plasmid-edit-description');
        expect(dialog?.querySelector('#project-plasmid-edit-description')?.textContent).toContain('Project metadata');
        expect(dialog?.textContent).toContain('Edit plasmid information');
        expect(dialog?.querySelector<HTMLInputElement>('input[name="name"]')?.value).toBe('PL1480');
        expect(dialog?.textContent).toContain('Molecule type');
        expect(dialog?.textContent).toContain('Organism / host context');
        expect(dialog?.textContent).toContain('Project tags');
        expect(dialog?.textContent).toContain('Project notes');
        expect(document.activeElement).toBe(dialog?.querySelector('input[name="name"]'));

        await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
        expect(container.querySelector('[role="dialog"]')).toBeNull();
        expect(document.activeElement).toBe(edit);
    });

    it('binds keyboard tabs to their tabpanel and preserves exact project context', async () => {
        await renderWorkspace();
        const overviewTab = Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]')).find((button) => button.textContent === 'Overview');
        expect(overviewTab?.id).toBe('project-tab-overview');
        expect(overviewTab?.getAttribute('aria-controls')).toBe('project-panel-overview');
        expect(container.querySelector('#project-panel-overview')?.getAttribute('role')).toBe('tabpanel');

        await act(async () => overviewTab?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })));
        expect(contextMocks.updateQueryParams).toHaveBeenCalledWith({ section: 'plasmids', plasmid: null });
    });

    it('keeps historical project states visibly read-only while preserving exact reopen links', async () => {
        apiMocks.fetchProjectHub.mockResolvedValue({
            ...readModel,
            identity: { ...readModel.identity, selected_state_revision_id: 'state-historical' },
        });
        await renderWorkspace('workspace_id=project-1&global_experiment_id=experiment-1&domain_experiment_id=domain-1&state_revision_id=state-historical');

        expect(container.textContent).toContain('Historical project state — read-only');
        expect(buttonNamed('Edit info')?.disabled).toBe(true);
        expect(container.querySelector<HTMLAnchorElement>('a[href*="molbio_revision_id=revision-pl1480"]')).not.toBeNull();
        expect(container.querySelector('a[href*="action=add-plasmid"]')).toBeNull();
    });

    it('submits one governed edit-info command with exact revision and state expectations', async () => {
        await renderWorkspace();
        await act(async () => buttonNamed('Edit info')?.click());
        const dialog = container.querySelector<HTMLElement>('[role="dialog"]');
        const name = dialog?.querySelector<HTMLInputElement>('input[name="name"]');
        expect(name).not.toBeNull();
        await act(async () => {
            if (name) {
                Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(name, 'PL1480 renamed');
                name.dispatchEvent(new Event('input', { bubbles: true }));
            }
            dialog?.querySelector<HTMLButtonElement>('button[type="submit"]')?.click();
            await Promise.resolve();
        });
        expect(apiMocks.updateProjectHubPlasmidInfo).toHaveBeenCalledTimes(1);
        const [projectId, experimentId, domainId, sequenceId, request] = apiMocks.updateProjectHubPlasmidInfo.mock.calls[0];
        expect([projectId, experimentId, domainId, sequenceId]).toEqual(['project-1', 'experiment-1', 'domain-1', 'sequence-pl1480']);
        expect(request).toMatchObject({
            expected_molecular_revision_id: 'revision-pl1480',
            expected_state_revision_id: 'state-current',
            expected_state_head_generation: 4,
            molecular_fields: { name: 'PL1480 renamed' },
            project_metadata: { project_tags: ['new plasmid'], project_notes: '' },
        });
        expect(request.idempotency_key).toEqual(expect.any(String));
    });

    it('refreshes stale authority and keeps edit input open for operator review', async () => {
        const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
        apiMocks.fetchMolBioNgsDomainState
            .mockResolvedValueOnce({ current_state_revision_id: 'state-current', head_generation: 4 })
            .mockResolvedValueOnce({ current_state_revision_id: 'state-refreshed', head_generation: 5 });
        apiMocks.updateProjectHubPlasmidInfo.mockRejectedValueOnce({
            response: { status: 409, data: { detail: { code: 'stale_generation', message: 'Domain state head changed' } } },
        });
        await renderWorkspace();
        await act(async () => buttonNamed('Edit info')?.click());
        await act(async () => {
            container.querySelector<HTMLElement>('[role="dialog"]')?.querySelector<HTMLButtonElement>('button[type="submit"]')?.click();
            await Promise.resolve();
        });
        for (let attempt = 0; attempt < 20 && !container.querySelector('[role="alert"]'); attempt += 1) {
            await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
        }
        expect(container.querySelector('[role="dialog"]')).not.toBeNull();
        expect(container.querySelector('[role="alert"]')?.textContent).toContain('Project state advanced');
        expect(container.querySelector('[role="alert"]')?.textContent).toContain('Review the refreshed state before retrying');
        expect(invalidate).toHaveBeenCalledWith({ queryKey: ['molbio-project-hub', 'project-1', 'experiment-1', 'domain-1'] });
        expect(contextMocks.setStateRevisionId).toHaveBeenCalledWith('state-refreshed');
    });

    it('renders populated Sequence Data from persisted typed summaries without bulk read payloads', async () => {
        apiMocks.fetchProjectHub.mockResolvedValue({
            ...readModel,
            sequence_data: {
                ...readModel.sequence_data,
                items: [{
                    id: 'run-42', plasmid_sequence_id: 'sequence-pl1480', plasmid_name: 'PL1480', kind: 'run',
                    title: 'ONT run 42', summary: 'Basecalled read set available.', status: 'completed',
                    created_at: '2026-08-26T12:00:00Z', reopen_href: '/ngs?run_id=run-42&observed_generation=3',
                }],
            },
        });
        await renderWorkspace('workspace_id=project-1&global_experiment_id=experiment-1&domain_experiment_id=domain-1&state_revision_id=state-current&section=sequence-data');
        expect(container.textContent).toContain('ONT run 42');
        expect(container.textContent).toContain('Basecalled read set available.');
        expect(container.querySelector('a[href*="run_id=run-42"][href*="observed_generation=3"]')).not.toBeNull();
        expect(container.textContent).not.toContain('No ONT sequencing data attached');
        expect(JSON.stringify(apiMocks.fetchProjectHub.mock.results)).not.toContain('fastq');
    });

    it('renders persisted Results and readable Activity without exposing technical envelopes by default', async () => {
        apiMocks.fetchProjectHub.mockResolvedValue({
            ...readModel,
            results: [{ id: 'result-1', plasmid_name: 'PL2190', type: 'Clone assessment', status: 'ready', owner: 'ONT run 42', created_at: '2026-08-26T12:00:00Z', summary: 'Clone matches the expected plasmid.', reopen_href: '/ngs?job_id=job-42' }],
        });
        await renderWorkspace('workspace_id=project-1&global_experiment_id=experiment-1&domain_experiment_id=domain-1&state_revision_id=state-current&section=results');
        expect(container.textContent).toContain('Clone assessment');
        expect(container.textContent).toContain('Clone matches the expected plasmid.');
        expect(container.querySelector('a[href="/ngs?job_id=job-42"]')).not.toBeNull();

        window.history.replaceState({}, '', '/designer?workspace_id=project-1&global_experiment_id=experiment-1&domain_experiment_id=domain-1&state_revision_id=state-current&section=activity');
        await act(async () => root.render(
            <MemoryRouter>
                <QueryClientProvider client={queryClient}><DomainExperimentWorkspace /></QueryClientProvider>
            </MemoryRouter>,
        ));
        expect(container.textContent).toContain('PL1480 added to the project');
        const activityTechnical = container.querySelector<HTMLDetailsElement>('details[data-testid="activity-technical-activity-1"]');
        expect(activityTechnical?.open).toBe(false);
        expect(activityTechnical?.textContent).toContain('molecular_member_attached');
        expect(activityTechnical?.textContent).toContain('event-digest');
    });
});
