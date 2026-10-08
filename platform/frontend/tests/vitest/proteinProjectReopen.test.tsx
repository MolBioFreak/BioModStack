import React from 'react';
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, test, vi } from 'vitest';
import { ProteinProjectWorkspace } from '../../src/components/project-manager/protein/ProteinProjectWorkspace';
import { ProjectReturnBanner } from '../../src/components/project-manager/ProjectReturnBanner';
import { api } from '../../src/lib/api';
import { parseFrustraMpnnExperimentContext } from '../../src/components/frustrampnn/workflowResultViewState';

const fixture = vi.hoisted(() => ({ route: { template_id: 'bms.route.verified-external-entity.v1', path: '/designs/job', query: { design_id: 'second' } } }));
// Generated TEST hierarchy/read-model projections. Reopen context issuance, route
// assembly, hierarchy validation, strict Frustra parser and banner stay real.
vi.mock('../../src/lib/projectManager', async original => ({
    ...await original<typeof import('../../src/lib/projectManager')>(),
    getProject: async () => ({ id: 'p', name: 'TEST Protein project', current_revision_id: 'pr' }),
    getGlobalExperiment: async () => ({ id: 'g', parent_id: 'p', name: 'TEST Global', current_revision_id: 'gr' }),
    getDomainExperiment: async () => ({ id: 'd', parent_id: 'g', current_revision_id: 'dr', payload: { domain_kind: 'protein_in_silico', domain_payload: { schema: 'bms.protein-in-silico-experiment.v3', experiment_mode: 'design', targets: [] } } }),
    getProjectSummary: async () => ({ source_receipt_ids: [], tasks: [], result_previews: [{ receipt_id: 'receipt', route: fixture.route, readiness: 'ready', surface_kind: 'protein_design', contract_id: 'test', comparison: { state: 'not_applicable' }, scientific_acceptance: { state: 'review' } }] }),
    reopenDomainResult: async () => ({ route: fixture.route }),
}));
const text = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
const Location = () => <span data-location={useLocation().pathname + useLocation().search} />;
const flush = async () => { for (let i = 0; i < 10; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
let renderer: ReactTestRenderer | undefined;
let client: QueryClient;
const originalAdapter = api.defaults.adapter;
afterEach(async () => { await act(async () => renderer?.unmount()); client?.clear(); api.defaults.adapter = originalAdapter; });

for (const path of ['/designs/job', '/designs/cm-job', '/designs/md-job', '/designs/frustra-job']) {
    test(`Protein Results reopens ${path} with exact server context and returns to Results after reload`, async () => {
        fixture.route.path = path;
        const returnUri = '/projects/p/experiments/g/domains/d?workspace=protein&section=results';
        let hierarchyReads = 0;
        api.defaults.adapter = async config => {
            expect(config.method).toBe('get');
            hierarchyReads++;
            const records: Record<string, unknown> = {
                '/api/projects/p': { id: 'p' },
                '/api/projects/p/experiments/g': { id: 'g', parent_id: 'p', current_revision_id: 'gr' },
                '/api/projects/p/experiments/g/domains/d': { id: 'd', parent_id: 'g', current_revision_id: 'dr' },
            };
            expect(records[config.url!]).toBeDefined();
            return { data: records[config.url!], status: 200, statusText: 'OK', headers: {}, config };
        };
        client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
        const mount = async (entry: string) => {
            await act(async () => { renderer = create(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><Location /><ProjectReturnBanner /><Routes><Route path="/projects/:p/experiments/:g/domains/:d" element={<ProteinProjectWorkspace projectId="p" globalExperimentId="g" domainExperimentId="d" />} /></Routes></MemoryRouter></QueryClientProvider>); });
            await flush();
        };
        await mount(returnUri);
        const open = renderer!.root.findAllByType('button').find(button => text(button) === 'Open canonical result')!;
        await act(async () => open.props.onClick()); await flush();
        expect(hierarchyReads).toBeGreaterThanOrEqual(3);
        const destination = renderer!.root.findAllByType('span').find(item => item.props['data-location'])!.props['data-location'];
        expect(destination).toContain(`${path}?design_id=second`);
        expect(destination).not.toContain('launch_context_id');
        expect(destination).toContain('workspace_id=p');
        expect(parseFrustraMpnnExperimentContext(new URL(destination, 'https://test').search)).toEqual({ projectId: 'p', globalExperimentId: 'g', domainExperimentId: 'd', globalExperimentRevisionId: 'gr', domainRevisionId: 'dr' });
        await act(async () => renderer!.unmount()); client.clear();
        await mount(destination);
        expect(renderer!.root.findAllByType('a').find(item => item.props['aria-label'] === 'Return to Project context')?.props.href).toBe(returnUri);
    });
}
