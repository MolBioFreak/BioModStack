import React, { act, type ComponentProps } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() }));
vi.mock('../../src/lib/api', () => ({
    fetchUserTemplates: api.list, createUserTemplate: api.create,
    updateUserTemplate: api.update, deleteUserTemplate: api.remove,
}));
import { TemplateManagerModal } from '../../src/components/TemplateManagerModal';
import type { UserTemplate } from '../../src/lib/api';

type Props = ComponentProps<typeof TemplateManagerModal>;
const nativeParams = { target_path: 'inputs/unchanged.cif', chain: 'a', residues: ['a42A'], zero: 0, off: false, empty: '', list: [], nullable: null, nested: { custom: ['unchanged'] } };
const saved = { id: 'saved-native', name: 'Saved native configuration', description: 'Original description', icon: 'star', color: '#10B981', model_id: 'ppiflow', mode: 'antibody_binder', base_template_id: 'native-base', params: nativeParams } as UserTemplate;
let root: Root;
let client: QueryClient;
let props: Props;
let rows: UserTemplate[];

beforeEach(() => {
    rows = [saved];
    api.list.mockImplementation(async (search?: string) => ({ data: rows.filter(row => !search || row.name.includes(search)) }));
    api.create.mockImplementation(async (data) => { const row = { ...data, id: 'created-native' }; rows = [...rows, row]; return { data: row }; });
    api.update.mockImplementation(async (id, data) => { rows = rows.map(row => row.id === id ? { ...row, ...data } : row); return { data: rows.find(row => row.id === id) }; });
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    props = { isOpen: true, onClose: vi.fn(), onSelect: vi.fn(), currentParams: nativeParams, currentModelId: 'ppiflow', currentMode: 'antibody_binder', baseTemplateId: 'native-base' };
});
afterEach(async () => {
    await act(async () => root.unmount()); client.clear(); document.body.replaceChildren(); vi.resetAllMocks();
});
async function render(next: Partial<Props> = {}) {
    props = { ...props, ...next };
    await act(async () => root.render(<React.StrictMode><QueryClientProvider client={client}><TemplateManagerModal {...props} /></QueryClientProvider></React.StrictMode>));
    await settle();
}
async function settle() {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
}
function heading() { return document.querySelector('h3')?.textContent; }
function input(placeholder: string) { return document.querySelector<HTMLInputElement>(`input[placeholder="${placeholder}"]`)!; }
async function click(label: string) {
    const button = [...document.querySelectorAll('button')].find(node => node.textContent?.trim() === label);
    expect(button, label).toBeDefined();
    await act(async () => button!.click()); await settle();
}
async function type(placeholder: string, value: string) {
    const node = input(placeholder); expect(node).not.toBeNull();
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, value);
        node.dispatchEvent(new Event('input', { bubbles: true }));
    }); await settle();
}
async function listReady(name = saved.name) {
    await vi.waitFor(async () => {
        await settle();
        expect(client.isFetching()).toBe(0);
        expect(heading()).toBe('My Templates');
        expect(input('Search templates...')).not.toBeNull();
        expect(document.body.textContent).toContain(name);
    });
}

it('keeps standalone BioXP drafts out of the model library and its launch callback', async () => {
    const robotDraft = { ...saved, id: 'bioxp-draft', name: 'Robot-only workflow', model_id: null,
        base_template_id: null, mode: 'bioxp_workflow',
        params: { schema: 'bms.bioxp-workflow-draft.v1', steps: [], editor_state: {} } };
    // Deliberately return a mixed list to qualify the receiving boundary too.
    rows = [robotDraft, saved];
    await render({ initialIntent: 'browse' }); await listReady();
    expect(api.list).toHaveBeenCalledWith(undefined, undefined, undefined, 'bioxp_workflow');
    expect(document.body.textContent).not.toContain(robotDraft.name);
    expect([...document.querySelectorAll('button')].filter(node => node.textContent?.trim() === 'Load')).toHaveLength(1);
    await click('Load');
    expect(props.onSelect).toHaveBeenCalledExactlyOnceWith(saved);
    expect(api.create).not.toHaveBeenCalled(); expect(api.update).not.toHaveBeenCalled();
});
