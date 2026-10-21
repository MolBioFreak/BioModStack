import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, test, vi } from 'vitest';
import { ProjectTree } from '../../src/components/project-manager/ProjectTree';
import { RelationshipMap } from '../../src/components/project-manager/RelationshipMap';
import { projectResultHref, type ProjectManagerReadModel } from '../../src/lib/projectManager';

let root: Root | undefined;
afterEach(async () => { await act(async () => root?.unmount()); root = undefined; document.body.replaceChildren(); });
async function mount(component: React.ReactNode) { const host = document.createElement('div'); document.body.append(host); root = createRoot(host); await act(async () => root!.render(component)); return host; }
const reconciliation = { state: 'current' as const, last_verified_at: null, reason: null };
const plan = { node_key: 'workflow:plan', node_type: 'workflow' as const, label: 'DRT4 ESMFold2 validation Plan', normalized_state: 'draft', canonical_identity: { entity_id: 'plan' }, counts: {}, reconciliation, allowed_actions: [] };

test('Find a Project item includes a visible native Plan without duplicating hierarchy matches', async () => {
    const select = vi.fn();
    const host = await mount(<ProjectTree nodes={[]} mapNodes={[plan]} selectedNodeKey="" onSelect={select} />);
    const input = host.querySelector('input')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'ESMFold2'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    expect(host.textContent).toContain(plan.label);
    expect(host.textContent).toContain('Searching loaded tree and map');
    await act(async () => host.querySelector<HTMLButtonElement>('nav button')!.click());
    expect(select).toHaveBeenCalledWith('workflow:plan');
});

test('Show selected item reveals the native exact selection without fetching or changing the bounded page', async () => {
    HTMLElement.prototype.scrollIntoView = vi.fn();
    const summary = { project: { name: 'Project' }, tree: { nodes: [] }, map: { nodes: [plan], edges: [], truncated: true, next_cursor: 'map:next' }, selection: { node_key: 'domain_experiment:offpage', node_type: 'domain_experiment', title: 'Monomer e8db269a', summary: { lifecycle_state: 'review' }, canonical_identity: { entity_id: 'offpage' }, reconciliation } } as unknown as ProjectManagerReadModel;
    const load = vi.fn(); const select = vi.fn();
    const host = await mount(<RelationshipMap summary={summary} selectedNodeKey={summary.selection.node_key} onSelect={select} onLoadMore={load} />);
    const show = Array.from(host.querySelectorAll('button')).find(b => b.textContent === 'Show selected item')!;
    expect(show.disabled).toBe(false);
    await act(async () => show.click());
    expect(host.querySelector('[aria-label="Selected item outside map page"]')?.textContent).toContain('Monomer e8db269a');
    expect(summary.map.nodes).toEqual([plan]);
    expect(load).not.toHaveBeenCalled();
    await act(async () => root!.render(<RelationshipMap summary={summary} selectedNodeKey="virtual_folder:offpage:results" onSelect={select} />));
    expect(host.querySelector('[aria-label="Selected item outside map page"]')).toBeNull();
    expect(show.disabled).toBe(true);
});

test('receipt route excludes scheduler descendants and retains all PM return filters', () => {
    const return_uri = '/projects/p?focus=g&selected=external_entity_receipt%3Ar&view=records&candidate_source=s&candidate_search=e8db269a';
    const route = projectResultHref({ template_id: 'bms.route.verified-external-entity.v1', path: '/designs/monomer', query: { include_children: 'true' } }, { project_id: 'p', global_experiment_id: 'g', domain_experiment_id: 'd', global_experiment_revision_id: 'gr', domain_revision_id: 'dr', return_uri });
    const parsed = new URL(route, window.location.origin);
    expect(parsed.pathname).toBe('/designs/monomer');
    expect(parsed.searchParams.get('include_children')).toBe('false');
    expect(parsed.searchParams.get('return_uri')).toBe(return_uri);
});

// PM adapter edge identity is metadata, not a new scientific result contract.
// Exercise the actual parser on the same native shape as the live attached result.
test('native produced attachment result is accepted by the PM read-model consumer', async () => {
    const { parseResultSurface } = await import('../../src/lib/projectManager');
    const surface = { schema: 'bms.result-surface.v1', receipt_id: 'r', entity_kind: 'typed_core_job_result', entity_id: 'monomer', contract_id: 'typed_core_job_result_v1', content_digest: 'a'.repeat(64), surface_kind: 'protein_design', route: { template_id: 'bms.route.verified-external-entity.v1', path: '/designs/monomer', query: {} }, readiness: 'ready', native_summary: { schema_id: 'bms.result-surface.native-summary.v1', content_sha256: 'a'.repeat(64), canonical_size_bytes: 2, payload: {} }, provenance: { schema_id: 'bms.result-surface.provenance.v1', content_sha256: 'a'.repeat(64), canonical_size_bytes: 2, payload: {} }, scientific_acceptance: { state: 'review', reason: null }, comparison: { state: 'unavailable', reason: null, authority: null }, available_actions: ['open'], lineage_edge_key: 'attachment:bms.core-job.protenix.adapter.v1:090303b9-59dc-424d-bbd7-c74cc63ecf1d:produced' };
    expect(parseResultSurface(surface).lineage_edge_key).toBe(surface.lineage_edge_key);
    expect(() => parseResultSurface({ ...surface, lineage_edge_key: 'invalid' })).toThrow();
});
