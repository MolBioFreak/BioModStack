import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { ManagerDialog, type ManagerDialogMode } from '../../src/components/project-manager/ManagerDialog';
import type { ProjectManagerReadModel } from '../../src/lib/projectManager';

const transport = vi.hoisted(() => ({ read: vi.fn(), edit: vi.fn(), archive: vi.fn(), restore: vi.fn() }));
vi.mock('../../src/lib/projectManager', async importOriginal => ({
    ...await importOriginal<typeof import('../../src/lib/projectManager')>(),
    getProject: transport.read, getGlobalExperiment: transport.read, getDomainExperiment: transport.read,
    updateProject: transport.edit, updateGlobalExperiment: transport.edit, updateDomainExperiment: transport.edit,
    archiveProject: transport.archive, archiveGlobalExperiment: transport.archive, archiveDomainExperiment: transport.archive,
    restoreProject: transport.restore, restoreGlobalExperiment: transport.restore, restoreDomainExperiment: transport.restore,
}));
let root: Root;
let host: HTMLDivElement;
let client: QueryClient;
afterEach(async () => { if (root) await act(() => root.unmount()); client?.clear(); host?.remove(); vi.clearAllMocks(); });
const button = (text: string) => [...host.querySelectorAll('button')].find(b => b.textContent?.trim() === text)!;
const wait = async (check: () => void) => vi.waitFor(async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); check(); });

it.each(['project', 'global_experiment', 'domain_experiment'] as const)('refreshes %s editor values and revision across saves and archive/restore', async kind => {
    const entity = kind === 'project' ? 'project' : kind === 'global_experiment' ? 'group' : 'domain';
    const scope = `${kind}:${entity}`;
    const stored = { id: entity, name: 'Initial title', description: 'Description', head_generation: 3, lifecycle_state: 'active', payload: { schema: kind === 'project' ? 'bms.project.v2' : kind === 'global_experiment' ? 'bms.global-experiment.v2' : 'bms.domain-experiment.v4', objective: 'Tracking only', status: 'active', domain_kind: 'ngs_molbio' } };
    transport.read.mockImplementation(async () => structuredClone(stored));
    const generations: number[] = [];
    const receive = (expected: number) => { expect(expected).toBe(stored.head_generation); generations.push(expected); stored.head_generation++; };
    transport.edit.mockImplementation(async (...args: any[]) => {
        const body = args.at(-1); receive(body.expected_head_generation); stored.name = body.name;
        return structuredClone(stored);
    });
    transport.archive.mockImplementation(async (...args: any[]) => { receive(args.at(-1)); stored.lifecycle_state = 'archived'; return structuredClone(stored); });
    transport.restore.mockImplementation(async (...args: any[]) => { receive(args.at(-1)); stored.lifecycle_state = 'active'; return structuredClone(stored); });
    const summary = { project: { id: 'project', head_generation: 3 }, selection: { node_key: scope, node_type: kind, title: 'Tracked work', canonical_identity: { entity_id: entity } }, tree: { nodes: [
        { node_key: 'project:project', node_type: 'project', subject_id: 'project', parent_node_key: null },
        { node_key: 'global_experiment:group', node_type: 'global_experiment', subject_id: 'group', parent_node_key: 'project:project' },
        { node_key: 'domain_experiment:domain', node_type: 'domain_experiment', subject_id: 'domain', parent_node_key: 'global_experiment:group' },
    ] } } as unknown as ProjectManagerReadModel;
    function Harness() {
        const [mode, setMode] = useState<ManagerDialogMode | null>(null);
        return <><button onClick={() => setMode('edit')}>Edit record</button><button onClick={() => setMode('archive')}>Archive record</button><button onClick={() => setMode('restore')}>Restore record</button>
            <ManagerDialog mode={mode} projectId="project" summary={summary} onClose={() => setMode(null)} onComplete={() => {}} /></>;
    }
    // Match the application cache window: reopening a still-fresh query must not reuse a pre-mutation head.
    client = new QueryClient({ defaultOptions: { queries: { staleTime: 60_000, gcTime: Infinity, retry: false }, mutations: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(() => root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>));
    await act(() => button('Edit record').click());
    await wait(() => expect(host.querySelector<HTMLInputElement>('[aria-label="Name"]')?.value).toBe('Initial title'));
    await act(() => { const input = host.querySelector<HTMLInputElement>('[aria-label="Name"]')!; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'Saved title'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(() => button('Save immutable revision').click());
    await wait(() => expect(host.querySelector('[role="dialog"]')).toBeNull());
    await act(() => button('Edit record').click());
    await wait(() => expect(host.querySelector<HTMLInputElement>('[aria-label="Name"]')?.value).toBe('Saved title'));
    await act(() => button('Save immutable revision').click());
    await wait(() => expect(host.querySelector('[role="dialog"]')).toBeNull());
    for (const [open, confirm] of [['Archive record', kind === 'project' ? 'Delete Project (archive)' : 'Archive without cancelling runs'], ['Restore record', 'Restore']]) {
        await act(() => button(open).click());
        await wait(() => expect(button(confirm)).toBeTruthy());
        await act(() => button(confirm).click());
        await wait(() => expect(host.querySelector('[role="dialog"]')).toBeNull());
    }
    expect(generations).toEqual([3, 4, 5, 6]);
    expect(stored).toMatchObject({ name: 'Saved title', lifecycle_state: 'active', head_generation: 7 });
});
