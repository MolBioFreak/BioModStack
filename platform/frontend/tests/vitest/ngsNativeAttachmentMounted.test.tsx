import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const apiMocks = vi.hoisted(() => ({
    fetchMolBioNgsJobEvidenceIdentities: vi.fn(), fetchMolBioNgsAttachmentDelivery: vi.fn(),
    attachMolBioNgsJobEvidence: vi.fn(), assessMolBioNgsEvidence: vi.fn(),
}));
vi.mock('../../src/lib/api', async (original) => ({ ...(await original<typeof import('../../src/lib/api')>()), ...apiMocks }));
import { DomainEvidenceMutationPanel } from '../../src/components/molbio-ngs/DomainScientificMutationPanels';
let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
async function settle() {
    for (let i = 0; i < 10; i++) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}
function button(text: string) {
    const result = [...container.querySelectorAll('button')].find((node) => node.textContent === text);
    if (!result) throw new Error(`Missing ${text}`);
    return result;
}
function input(label: string, value: string) {
    const node = [...container.querySelectorAll('label')].find((node) => node.textContent?.includes(label))?.querySelector('input');
    if (!node) throw new Error(`Missing ${label}`);
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, value);
    node.dispatchEvent(new Event('input', { bubbles: true }));
}
beforeEach(() => {
    vi.resetAllMocks();
    apiMocks.fetchMolBioNgsJobEvidenceIdentities.mockResolvedValue({ job_id: 'J', identities: ['native-scientific-result'], launch_state_revision_id: 'A' });
    apiMocks.fetchMolBioNgsAttachmentDelivery.mockResolvedValue({ state_revision_id: 'B', receipt_id: 'M', project_delivery: 'pending' });
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); });
async function mount() {
    await act(async () => root.render(<QueryClientProvider client={client}><DomainEvidenceMutationPanel
        domainExperimentId="D" canMutate mutationBlocker={null} stateRevisionId="A"
        stateRevisions={[{ id: 'A', revision_number: 1 }] as never} members={[]} sampleRows={[]}
    /></QueryClientProvider>));
    await act(async () => input('NGS job ID', 'J'));
    await settle();
}
it('reuses the operation key after a lost response and distinguishes membership, delivery, and QC', async () => {
    apiMocks.attachMolBioNgsJobEvidence.mockRejectedValueOnce(new Error('Response lost')).mockResolvedValue({
        ngs_job: { receipt_id: 'JR' }, ngs_result_manifest: { receipt_id: 'M', source_schema: 'bms.ngs.native-scientific-result.v1' },
        state_revision_id: 'B', project_delivery: 'pending',
    });
    await mount();
    await act(async () => button('Attach job receipts').click()); await settle();
    await act(async () => button('Attach job receipts').click()); await settle();
    const calls = apiMocks.attachMolBioNgsJobEvidence.mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[0][1]).toEqual(calls[1][1]);
    expect(calls[1][1]).toMatchObject({ job_id: 'J', manifest_identity: 'native-scientific-result', state_revision_id: 'A' });
    expect(container.textContent).toContain('Domain membership saved at B');
    expect(container.textContent).toContain('Project delivery: pending');
    expect(button('Create immutable evidence assessment').disabled).toBe(true);
    expect(apiMocks.assessMolBioNgsEvidence).not.toHaveBeenCalled();
    apiMocks.fetchMolBioNgsAttachmentDelivery.mockResolvedValue({ state_revision_id: 'B', receipt_id: 'M', project_delivery: 'delivered' });
    await act(async () => button('Refresh delivery').click()); await settle();
    expect(container.textContent).toContain('Project delivery: delivered');
});
it('requires an explicit choice when the native producer also emitted QC', async () => {
    apiMocks.fetchMolBioNgsJobEvidenceIdentities.mockResolvedValue({ job_id: 'J', identities: ['sequence-qc-manifest', 'native-scientific-result'], launch_state_revision_id: 'A' });
    await mount();
    expect(button('Attach job receipts').disabled).toBe(true);
    const select = [...container.querySelectorAll('select')].find((node) => [...node.options].some((option) => option.value === 'native-scientific-result'))!;
    await act(async () => { select.value = 'native-scientific-result'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(button('Attach job receipts').disabled).toBe(false);
    expect(button('Create immutable evidence assessment').disabled).toBe(true);
});
