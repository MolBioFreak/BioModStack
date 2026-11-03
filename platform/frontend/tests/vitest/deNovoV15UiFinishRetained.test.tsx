import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { ProjectManager } from '../../src/pages/ProjectManager';
import { ProteinProjectWorkspace } from '../../src/components/project-manager/protein/ProteinProjectWorkspace';
import { projectResultHref } from '../../src/lib/projectManager';

const fixture = vi.hoisted(() => ({ surface: {
    receipt_id: 'receipt', entity_kind: 'native_binder_job_result', entity_id: 'deleted-job',
    route: { template_id: 'bms.route.verified-external-entity.v1', path: '/designs/deleted-job', query: {} },
    readiness: 'failed', surface_kind: 'protein_design', contract_id: 'native', comparison: { state: 'not_applicable' },
    scientific_acceptance: { state: 'review' }, native_summary: { payload: { retained_artifacts: [{ resource_id: 'structure' }, { resource_id: 'document' }] } },
} }));
vi.mock('../../src/lib/projectManager', async original => ({
    ...await original<typeof import('../../src/lib/projectManager')>(),
    getProject: async () => ({ id: 'p', name: 'Project', current_revision_id: 'pr' }),
    getGlobalExperiment: async () => ({ id: 'g', parent_id: 'p', current_revision_id: 'gr' }),
    getDomainExperiment: async () => ({ id: 'd', parent_id: 'g', current_revision_id: 'dr', payload: { domain_kind: 'protein_in_silico', domain_payload: { schema: 'bms.protein-in-silico-experiment.v3', experiment_mode: 'design', targets: [] } } }),
    getProjectSummary: async () => ({ source_receipt_ids: [], tasks: [], result_previews: [fixture.surface] }),
    reopenDomainResult: async () => fixture.surface,
}));
// Only the renderer leaf is inert; native custody URL selection and all HTTP clients stay real.
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: ({ structureUrl, format }: any) => <div data-structure-url={structureUrl} data-format={format} /> }));
let root: Root | undefined, query: QueryClient;
const previous = api.defaults.adapter;
const calls: string[] = [], unexpected: string[] = [];
const returnUri = '/projects/p/experiments/g/domains/d?workspace=protein&section=results';
const Location = () => <span data-location={useLocation().pathname + useLocation().search} />;
const settle = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); }); };
async function close() { if (root) await act(async () => root!.unmount()); root = undefined; query?.clear(); document.body.replaceChildren(); }
afterEach(async () => { await close(); api.defaults.adapter = previous; calls.length = 0; expect(unexpected.splice(0)).toEqual([]); });
async function mount(entry: string) {
    api.defaults.adapter = async config => {
        const url = String(config.url); calls.push(url);
        const data: Record<string, any> = {
            '/api/projects/p': { id: 'p' }, '/api/projects/p/experiments/g': { id: 'g', parent_id: 'p', current_revision_id: 'gr' },
            '/api/projects/p/experiments/g/domains/d': { id: 'd', parent_id: 'g', current_revision_id: 'dr' },
            '/api/projects/p/native-results/receipt': { receipt_id: 'receipt', producer: { entity_id: 'deleted-job' }, artifacts: [
                { resource_id: 'structure', source_artifact_id: 'old-structure', logical_path: 'native/model.cif', sha256: 'a'.repeat(64), media_type: 'chemical/x-mmcif', bytes: 40, content_url: '/api/projects/p/native-results/receipt/artifacts/structure' },
                { resource_id: 'document', source_artifact_id: 'old-document', logical_path: 'native/manifest.json', sha256: 'b'.repeat(64), media_type: 'application/json', bytes: 18, content_url: '/api/projects/p/native-results/receipt/artifacts/document' },
            ] },
            '/api/projects/p/native-results/receipt/artifacts/document': '{"native":0,"partial":true}',
        };
        if (!(url in data)) { unexpected.push(url); throw Error(`No producer exists: ${url}`); }
        return { data: data[url], config, status: 200, statusText: 'OK', headers: {} };
    };
    query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<QueryClientProvider client={query}><MemoryRouter initialEntries={[entry]}><Location /><Routes>
        <Route path="/projects/:projectId" element={<ProjectManager />} />
        <Route path="/projects/p/experiments/g/domains/d" element={<ProteinProjectWorkspace projectId="p" globalExperimentId="g" domainExperimentId="d" />} />
    </Routes></MemoryRouter></QueryClientProvider>)); await settle();
}
async function click(text: string) { const node = [...document.querySelectorAll('button')].find(node => node.textContent === text); expect(node, text).toBeTruthy(); await act(async () => node!.click()); await settle(); }
it('opens failed/partial retained Project inventory and structure/document through real existing Project routing, then cold-reopens without producer requests', async () => {
    await mount(returnUri); await click('Open canonical result');
    const destination = document.querySelector('[data-location]')!.getAttribute('data-location')!;
    expect(destination).toContain('/projects/p?native_receipt=receipt'); expect(destination).not.toContain('/designs/deleted-job');
    expect(document.body.textContent).toContain('Original producer: deleted-job');
    await click('Inspect native/model.cif');
    expect(document.querySelector('[data-structure-url]')?.getAttribute('data-structure-url')).toContain('/api/projects/p/native-results/receipt/artifacts/structure');
    await click('Inspect native/manifest.json');
    expect(document.querySelector('pre')?.textContent).toBe('{"native":0,"partial":true}');
    expect([...document.querySelectorAll('a')].find(a => a.textContent === 'Return to Project results')?.getAttribute('href')).toBe(returnUri);
    await close(); await mount(destination);
    expect(document.body.textContent).toContain('Inspect native/model.cif');
    expect(calls.filter(url => url.startsWith('/api/jobs') || url.startsWith('/api/designs'))).toEqual([]);
});
it('does not advertise historical link-only receipts as retained backups', () => {
    const context = { project_id: 'p', global_experiment_id: 'g', domain_experiment_id: 'd', global_experiment_revision_id: 'gr', domain_revision_id: 'dr', return_uri: returnUri };
    const surface: any = { ...fixture.surface, native_summary: { payload: {} } };
    expect(projectResultHref(surface.route, context, surface)).toContain('/designs/deleted-job?');
});
