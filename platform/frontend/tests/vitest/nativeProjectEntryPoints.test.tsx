import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { NativeProjectTools } from '../../src/components/project-manager/NativeProjectTools';
import { ProjectAttachmentDialog } from '../../src/components/project-manager/ProjectAttachmentDialog';
import { ensureNativeProjectWorkspace } from '../../src/lib/nativeProjectWorkspace';
import { MOLECULAR_MEMBER_ADAPTER } from '../../src/lib/molecularProjectMembership';

let root: Root | undefined;
let client: QueryClient | undefined;
afterEach(async () => { await act(() => root?.unmount()); client?.clear(); vi.restoreAllMocks(); document.body.replaceChildren(); });
function Location() { const location = useLocation(); return <output aria-label="Current location">{location.pathname}{location.search}</output>; }
async function mount(node: React.ReactNode) {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    await act(() => root!.render(<MemoryRouter><QueryClientProvider client={client!}>{node}<Location /></QueryClientProvider></MemoryRouter>));
    return host;
}
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); }); }
function button(host: HTMLElement, text: string) { return [...host.querySelectorAll('button')].find((item) => item.textContent === text)!; }
function mockEmptyProject() {
    vi.spyOn(api, 'get').mockImplementation(async (url) => ({ data: String(url).endsWith('/ngs-data-records') ? { schema: 'bns.ngs-data-record-list.v1', items: [], next_cursor: null }
        : String(url).endsWith('/experiments') || String(url).endsWith('/domains') ? { items: [] }
        : String(url).endsWith('/state') ? { current_state_revision_id: null, head_generation: 0 }
        : String(url) === '/api/domain-adapters' ? { schema: 'bms.global.adapter-registry.v1', adapters: [
            { adapter_id: MOLECULAR_MEMBER_ADAPTER, adapter_version: 1, entity_kind: 'molecular_revision', domain_kind: 'ngs_molbio', display_name: 'Saved DNA sequence' },
            { adapter_id: 'protein', adapter_version: 1, entity_kind: 'protein', domain_kind: 'protein_in_silico', display_name: 'Protein folding' },
        ] } : { id: 'project', head_generation: 1 } } as never));
    return vi.spyOn(api, 'post').mockImplementation(async (url, data) => ({ data: String(url).endsWith('/domains') ? { id: 'domain', domain_kind: 'ngs_molbio' } : String(url).endsWith('/experiments') ? { id: 'experiment' } : { id: 'record', payload: data } } as never));
}
it('opens an empty Project directly in MolBio with add-sequence context and no research form', async () => {
    const post = mockEmptyProject();
    const host = await mount(<NativeProjectTools projectId="project" projectName="QA" context={null} />);
    await settle();
    for (const label of ['Open Molecular Tools', 'Open NGS', 'Add DNA sequence', 'Add NGS data record']) expect(button(host, label)).toBeTruthy();
    await act(() => button(host, 'Add DNA sequence').click()); await settle();
    expect(host.querySelector('output')?.textContent).toBe('/designer?workspace_id=project&global_experiment_id=experiment&domain_experiment_id=domain&action=add-plasmid');
    const domain = post.mock.calls.find(([url]) => String(url).endsWith('/domains'))?.[1] as { domain_payload: unknown };
    expect(domain.domain_payload).toMatchObject({ planned_capability_ids: [], acceptance_criteria: [], evidence_plan: [] });
    expect(host.textContent).not.toContain('Hypothesis');
});
it('creates a data record on an empty Project without creating any hierarchy', async () => {
    const post = mockEmptyProject();
    const host = await mount(<NativeProjectTools projectId="project" projectName="QA" context={null} />);
    await settle(); await act(() => button(host, 'Add NGS data record').click());
    const input = host.querySelector<HTMLInputElement>('[aria-label="NGS record name"]')!;
    await act(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'RNA reads'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(() => host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))); await settle();
    expect(post.mock.calls).toHaveLength(1);
    expect(post.mock.calls[0][0]).toBe('/api/projects/project/ngs-data-records');
    expect(post.mock.calls[0][1]).toMatchObject({ schema: 'bns.ngs-data-record.v1', title: 'RNA reads', files: [] });
});
it('does not expose Global Experiment selectors or protein adapters in an empty Project export', async () => {
    mockEmptyProject();
    const host = await mount(<ProjectAttachmentDialog open projectId="project" source={{ adapterId: MOLECULAR_MEMBER_ADAPTER, entityId: 'sequence_id=s&revision_id=r', label: 'Plasmid', availability: 'available', historyJournal: [] }} onClose={() => {}} />);
    await settle();
    expect(host.textContent).not.toContain('Global'); expect(host.textContent).not.toContain('New experiment');
    expect(button(host, 'Export to Project').disabled).toBe(false);
    await act(() => root!.render(<MemoryRouter><QueryClientProvider client={client!}><ProjectAttachmentDialog open projectId="project" onClose={() => {}} /></QueryClientProvider></MemoryRouter>));
    await settle();
    expect([...host.querySelectorAll('option')].some((option) => option.textContent === 'Protein folding')).toBe(false);
    expect(host.textContent).toContain('Saved DNA sequence');
});
it('coalesces simultaneous workspace setup and preserves an existing native destination', async () => {
    const post = mockEmptyProject();
    const [one, two] = await Promise.all([ensureNativeProjectWorkspace('p-coalesce'), ensureNativeProjectWorkspace('p-coalesce')]);
    expect(one).toEqual(two); expect(post).toHaveBeenCalledTimes(2);
    vi.mocked(api.get).mockImplementation(async (url) => ({ data: String(url).endsWith('/experiments') ? { items: [{ id: 'g' }] } : String(url).endsWith('/domains') ? { items: [{ id: 'd', domain_kind: 'ngs_molbio', name: 'Workspace' }] } : {} } as never));
    expect(await ensureNativeProjectWorkspace('p-existing')).toEqual({ globalExperimentId: 'g', domainExperimentId: 'd' });
    expect(post).toHaveBeenCalledTimes(2);
});
it('requires an explicit workspace choice instead of guessing among native contexts', async () => {
    const post = mockEmptyProject();
    vi.mocked(api.get).mockImplementation(async (url) => ({ data: String(url).endsWith('/experiments') ? { items: [{ id: 'g' }] } : { items: [{ id: 'a', domain_kind: 'ngs_molbio' }, { id: 'b', domain_kind: 'ngs_molbio' }] } } as never));
    await expect(ensureNativeProjectWorkspace('p-multiple')).rejects.toThrow('multiple NGS/MolBio workspaces');
    expect(post).not.toHaveBeenCalled();
});
