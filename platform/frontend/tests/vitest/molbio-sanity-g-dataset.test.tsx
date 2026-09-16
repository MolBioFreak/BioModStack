import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
    listDomainDatasetKinds: vi.fn(), listDomainDatasets: vi.fn(), getDomainDataset: vi.fn(),
    listDomainDatasetRevisions: vi.fn(), getDomainDatasetRevision: vi.fn(), listDomainDatasetRevisionMembers: vi.fn(),
}));
vi.mock('../../src/lib/projectManager', async (original) => ({ ...(await original<Record<string, unknown>>()), ...mocks }));
import DomainDatasetOperator from '../../src/components/molbio-ngs/DomainDatasetOperator';

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
const member = (ordinal: number) => ({ receipt_id: `receipt-${ordinal}`, role: 'construct', ordinal,
    media_type: null, metadata: { tags: [] }, canonical_member_sha256: `sha-${ordinal}`, size_bytes: 1 });
const page = (items: unknown[], next_cursor: string | null = null) => ({ schema: 'page', items, next_cursor, has_more: next_cursor !== null });
beforeEach(() => {
    vi.clearAllMocks();
    mocks.listDomainDatasetKinds.mockResolvedValue({ items: [] });
    mocks.listDomainDatasets.mockImplementation((_p, _e, _d, _signal, cursor) => Promise.resolve(page([{ dataset_id: cursor ? 'dataset-2' : 'dataset-1', name: 'Dataset', dataset_kind: 'kind', lifecycle_state: 'active' }], cursor ? null : 'datasets-next')));
    mocks.getDomainDataset.mockResolvedValue({ dataset_id: 'dataset-1', dataset_kind: 'kind', lifecycle_state: 'active', current_revision_id: 'revision-1', head_generation: 1 });
    mocks.listDomainDatasetRevisions.mockImplementation((_p, _e, _d, _id, _signal, cursor) => Promise.resolve(page([{ revision_id: cursor ? 'revision-2' : 'revision-1', revision_number: cursor ? 2 : 1 }], cursor ? null : 'revisions-next')));
    mocks.getDomainDatasetRevision.mockResolvedValue({ revision_id: 'revision-1', member_count: 101, members_uri: '/members', revision_number: 1 });
    mocks.listDomainDatasetRevisionMembers.mockImplementation((_p, _e, _d, _id, _rev, _signal, cursor) => Promise.resolve(cursor ? page([member(100)]) : page(Array.from({ length: 100 }, (_, i) => member(i)), 'members-next')));
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div'); document.body.append(container); root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); client.clear(); container.remove(); });
async function flush() { for (let i = 0; i < 8; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); }
function button(text: string) { return Array.from(container.querySelectorAll('button')).find(item => item.textContent === text)!; }
async function mount() {
    await act(async () => root.render(<QueryClientProvider client={client}><DomainDatasetOperator projectId="p" globalExperimentId="e" domainExperimentId="d" canMutate mutationBlocker={null} currentStateRevisionId={null} selectedRevisionIds={[]} onSelectedRevisionIdsChange={vi.fn()} /></QueryClientProvider>));
    await flush();
}
it('copies only all 101 exact members after explicit continuation, preserving order', async () => {
    await mount();
    expect(button('Copy members into new draft').disabled).toBe(true);
    expect(mocks.listDomainDatasetRevisionMembers).toHaveBeenCalledTimes(1);
    await act(async () => button('Load more members').click()); await flush();
    expect(mocks.listDomainDatasetRevisionMembers.mock.calls[1][6]).toBe('members-next');
    expect(button('Copy members into new draft').disabled).toBe(false);
    await act(async () => button('Copy members into new draft').click());
    const receipts = Array.from(container.querySelectorAll<HTMLInputElement>('input[placeholder="Verified receipt ID"]')).map(input => input.value);
    expect(receipts).toEqual(Array.from({ length: 101 }, (_, i) => `receipt-${i}`));
});
it('continues Dataset and revision selectors without discarding earlier rows', async () => {
    await mount();
    await act(async () => button('Load more Datasets').click()); await flush();
    await act(async () => button('Load more revisions').click()); await flush();
    expect(container.querySelector('option[value="dataset-1"]')).not.toBeNull();
    expect(container.querySelector('option[value="dataset-2"]')).not.toBeNull();
    expect(container.querySelector('option[value="revision-1"]')).not.toBeNull();
    expect(container.querySelector('option[value="revision-2"]')).not.toBeNull();
    expect(mocks.listDomainDatasets.mock.calls[1][4]).toBe('datasets-next');
    expect(mocks.listDomainDatasetRevisions.mock.calls[1][5]).toBe('revisions-next');
});
