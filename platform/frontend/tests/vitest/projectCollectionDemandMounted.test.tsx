import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, test, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { normalizeProjectManagerReadModel } from '../../src/lib/projectManager';
import { ProjectManager } from '../../src/pages/ProjectManager';
import { ProteinProjectWorkspace } from '../../src/components/project-manager/protein/ProteinProjectWorkspace';

// Scientific native panels have independent readers; only their internals are inert.
vi.mock('../../src/components/molbio-ngs/DomainDatasetOperator', () => ({ default: () => <div>Native dataset owner</div> }));
vi.mock('../../src/components/project-manager/protein/ProteinEvidenceOperator', () => ({ ProteinEvidenceOperator: () => <div>Native evidence owner</div> }));
vi.mock('../../src/components/project-manager/protein/ProteinPlanOperator', () => ({ ProteinPlanOperator: () => <div>Native plan owner</div> }));

const families = ['results', 'datasets', 'notes', 'decisions', 'activity'];

test('collection DTO distinguishes unrequested pages from empty full catalogs', () => {
    const projected = summary();
    expect(normalizeProjectManagerReadModel(projected).loaded_collection_families).toEqual([]);
    const { loaded_collection_families: _omitted, ...legacy } = projected;
    expect(normalizeProjectManagerReadModel(legacy).loaded_collection_families).toEqual(['results', 'lineage', 'datasets', 'notes', 'decisions', 'activity']);
});
const reconciliation = { state: 'current', last_verified_at: null, reason: null };
function summary(selected = 'domain_experiment:d', demand = '', cursor?: string) {
    const keys = ['project:p', 'global_experiment:g', 'domain_experiment:d', ...families.map(f => `virtual_folder:d:${f}`)];
    const tree = keys.map((key, index) => ({ node_key: key, node_type: key.split(':')[0], subject_id: index < 3 ? key.split(':')[1] : null, parent_node_key: index === 0 ? null : index === 1 ? keys[0] : index === 2 ? keys[1] : keys[2], label: index < 3 ? ['Project', 'Experiment', 'Domain'][index] : families[index - 3], counts: {}, has_children: index < 3, allowed_actions: [], lifecycle_state: 'active' }));
    const mapNodes = tree.slice(0, 3).map(n => ({ node_key: n.node_key, node_type: n.node_type, label: n.label, counts: n.counts, allowed_actions: n.allowed_actions, normalized_state: 'active', canonical_identity: { store_id: 'global', entity_id: n.subject_id }, reconciliation }));
    const pagination: Record<string, unknown> = { map_next_cursor: null, run_next_cursor: null, result_next_cursor: null, lineage_next_cursor: null, note_next_cursor: null, decision_next_cursor: null, dataset_next_cursor: null, activity_next_cursor: null };
    for (const f of ['map', 'runs', ...families, 'lineage']) pagination[f] = { items: [], next_cursor: null };
    pagination.map = { items: [], next_cursor: null, repeated_context_node_keys: [] };
    if (demand === 'notes') pagination.notes = { items: [{ id: cursor ? 'note-2' : 'note-1', record_kind: 'note', body: cursor ? 'Second note' : 'First note' }], next_cursor: cursor ? null : 'notes:next' };
    if (demand === 'activity') pagination.activity = { items: [{ id: 'event-1', resource_id: 'd', event_type: 'source_attached', generation: 1, payload: {}, created_at: '2026-08-09T00:00:00Z' }], next_cursor: null };
    const nodeType = selected.split(':')[0];
    return { loaded_collection_families: demand ? [demand] : [], schema: 'bms.project-manager.read-model.v1', subject_id: 'p', subject_generation: 1, assembled_at: '2026-08-09T00:00:00Z', source_receipt_ids: [], source_digest_set_sha256: 'a'.repeat(64), adapter_versions: [], reconciliation, counts: {}, status_summary: {}, recent_activity: [], result_previews: [], pagination,
        project: { id: 'p', project_scope: 'global', name: 'Project', objective: 'Objective', lifecycle_state: 'active', head_generation: 1, current_revision_id: 'pr', updated_at: '2026-08-09T00:00:00Z' }, tasks: [], tree: { nodes: tree }, map: { focus_node_key: 'global_experiment:g', nodes: mapNodes, edges: [], truncated: false, next_cursor: null },
        selection: { node_key: selected, node_type: nodeType, title: selected === 'research_record:note-2' ? 'Retained second note' : 'Domain', subtitle: null, canonical_identity: { store_id: 'global', entity_id: selected.split(':')[1] }, summary: { name: 'Domain' }, relationship: { parent_node_key: 'global_experiment:g' }, scientific_context: {}, reconciliation, available_actions: [], canonical_surface: null }, runs: { items: [], next_cursor: null }, warnings: [], allowed_actions: [] };
}
let root: Root; let client: QueryClient; let container: HTMLDivElement;
const original = api.defaults.adapter;
const reads: { selected: string; demand: string; cursor?: string }[] = [];
const settle = async () => { for (let i = 0; i < 12; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
async function mount(protein: boolean) {
    reads.length = 0;
    api.defaults.adapter = async config => {
        let data: unknown;
        if (config.url?.endsWith('/summary')) {
            const params = config.params;
            const demand = params.collection_families;
            expect(typeof demand).toBe('string'); // [] must reach HTTP as explicit empty, not omitted.
            const wireQuery = new URL(api.getUri(config), 'https://test.invalid').searchParams;
            expect(wireQuery.has('collection_families')).toBe(true);
            expect(wireQuery.get('collection_families')).toBe(demand);
            reads.push({ selected: params.selected_node_key, demand, cursor: params.note_cursor });
            data = summary(params.selected_node_key, demand, params.note_cursor);
        } else if (config.url === '/api/projects/p') data = { id: 'p', name: 'Project', current_revision_id: 'pr' };
        else if (config.url === '/api/projects/p/experiments/g') data = { id: 'g', parent_id: 'p', name: 'Experiment', current_revision_id: 'gr' };
        else if (config.url === '/api/projects/p/experiments/g/domains/d') data = { id: 'd', parent_id: 'g', current_revision_id: 'dr', payload: { domain_kind: 'protein_in_silico', domain_payload: { schema: 'bms.protein-in-silico-experiment.v3', experiment_mode: 'design', targets: [] } } };
        else data = { items: [], capabilities: [], adapters: [] };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    container = document.createElement('div'); document.body.append(container); root = createRoot(container);
    const entry = protein ? '/projects/p/experiments/g/domains/d?workspace=protein' : '/projects/p?focus=g&selected=domain_experiment%3Ad';
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><Routes><Route path="/projects/:projectId" element={<ProjectManager />} /><Route path="/projects/:projectId/experiments/:experimentId/domains/:domainId" element={<ProteinProjectWorkspace projectId="p" globalExperimentId="g" domainExperimentId="d" />} /></Routes></MemoryRouter></QueryClientProvider>));
    await settle();
}
async function click(label: string) {
    const button = Array.from(container.querySelectorAll('button')).find(b => b.textContent?.trim() === label || b.getAttribute('aria-label') === `Expand ${label}` || b.getAttribute('aria-label') === `Collapse ${label}`);
    expect(button, `${label}: ${container.textContent}`).toBeDefined();
    await act(async () => button!.click()); await settle();
}
afterEach(async () => { await act(async () => root?.unmount()); client?.clear(); api.defaults.adapter = original; document.body.replaceChildren(); });

test('mounted shared tree/map switches families, traverses notes, and retains off-page selection', async () => {
    await mount(false);
    expect(reads.at(-1)?.demand).toBe('');
    expect(container.querySelector('aside[aria-label="Project tree"]')).not.toBeNull();
    for (const family of families) {
        await click(family);
        expect(reads.at(-1)?.demand).toBe(family);
        expect(container.querySelector('aside[aria-label="Project tree"]')).not.toBeNull();
    }
    await click('notes');
    await click('Next notes page');
    expect(reads.at(-1)).toMatchObject({ demand: 'notes', cursor: 'notes:next' });
    expect(container.textContent).not.toContain('First note');
    expect(container.textContent).toContain('Second note');
    const record = Array.from(container.querySelectorAll('button')).find(b => b.textContent?.includes('Second note'))!;
    await act(async () => record.click()); await settle();
    expect(reads.at(-1)).toMatchObject({ demand: '', selected: 'research_record:note-2' });
    await click('Show inspector');
    expect(container.textContent).toContain('Retained second note');
    expect(container.querySelector('aside[aria-label="Project tree"]')).not.toBeNull();
    await click('notes');
    // Navigation restarts the bounded page; direct-ID inspection above retains
    // off-page selection without retaining every previously displayed row.
    expect(reads.at(-1)).toMatchObject({ demand: 'notes', cursor: undefined });
    expect(container.textContent).toContain('First note');
    expect(container.textContent).not.toContain('Second note');
});

test('mounted Protein sections discover comparison/evidence/history demand while overview keeps shared context', async () => {
    await mount(true);
    expect(reads.at(-1)?.demand).toBe('');
    for (const [section, demand] of [['Results', 'results'], ['Comparisons', 'results'], ['Evidence / ELN', 'lineage'], ['History', 'activity'], ['Targets', ''], ['Datasets', ''], ['Workflows', ''], ['Runs', ''], ['Technical details', ''], ['Overview', '']]) {
        await click(section);
        expect(reads.at(-1)?.demand).toBe(demand);
        expect(reads.at(-1)?.selected).toBe('domain_experiment:d');
    }
});
