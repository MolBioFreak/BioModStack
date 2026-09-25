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

it('opens in save mode, then back/search/load stays in the settled list and selects the exact native template', async () => {
    await render(); expect(heading()).toBe('Save as Template');
    await click('← Back to list'); await listReady();
    await render({ currentParams: { ...nativeParams } }); await listReady();
    await type('Search templates...', 'Saved native'); await listReady();
    expect(api.list).toHaveBeenLastCalledWith('Saved native');
    await click('Load');
    expect(props.onSelect).toHaveBeenCalledWith(saved);
    expect(props.onClose).toHaveBeenCalledOnce();
    expect(api.create).not.toHaveBeenCalled(); expect(api.update).not.toHaveBeenCalled();
});

it('saves exact native model/mode/params and stays in the refetched list until explicitly saving again', async () => {
    await render(); await type('e.g., My Boltz Config', 'Current native draft');
    await click('Save Template'); await listReady('Current native draft');
    expect(api.create.mock.calls[0][0]).toEqual({ name: 'Current native draft', description: undefined, icon: 'bookmark', color: '#6B7280', base_template_id: 'native-base', model_id: 'ppiflow', mode: 'antibody_binder', params: nativeParams });
    expect(api.list.mock.calls.length).toBeGreaterThan(1);
    await click('+ Save Current Config'); expect(heading()).toBe('Save as Template');
    expect(input('e.g., My Boltz Config').value).toBe('');
    await click('Cancel'); await listReady();
    await type('Search templates...', 'Current native draft'); await listReady('Current native draft');
    await click('Load');
    expect(props.onSelect).toHaveBeenCalledWith(expect.objectContaining({ name: 'Current native draft', model_id: 'ppiflow', mode: 'antibody_binder', params: nativeParams }));
});

it('cancels create and edit without writes, then updates saved metadata without substituting the current draft', async () => {
    await render({ currentModelId: 'different-model', currentMode: 'different-mode', currentParams: { other: true } });
    await type('e.g., My Boltz Config', 'Discard me'); await click('Cancel'); await listReady();
    await click('Edit'); expect(heading()).toBe('Edit Template');
    expect(input('e.g., My Boltz Config').value).toBe(saved.name);
    await type('e.g., My Boltz Config', 'Discard edit'); await click('Cancel'); await listReady();
    expect(api.create).not.toHaveBeenCalled(); expect(api.update).not.toHaveBeenCalled();
    await click('Edit'); await type('e.g., My Boltz Config', 'Renamed native');
    await click('Update Template'); await listReady('Renamed native');
    expect(api.update.mock.calls[0]).toEqual([saved.id, { name: 'Renamed native', description: saved.description, icon: saved.icon, color: saved.color, base_template_id: saved.base_template_id, model_id: saved.model_id, mode: saved.mode, params: saved.params }]);
});

it('does not hijack browsing when an asynchronous draft arrives; explicit save uses the latest draft', async () => {
    await render({ currentParams: undefined }); await listReady();
    await type('Search templates...', 'Saved');
    const late = { ...nativeParams, late: 'exact' };
    await render({ currentParams: late }); await listReady();
    expect(input('Search templates...').value).toBe('Saved');
    await click('+ Save Current Config'); await type('e.g., My Boltz Config', 'Saved late draft');
    await click('Save Template'); await listReady('Saved late draft');
    expect(api.create.mock.calls[0][0].params).toEqual(late);
});

it('keeps back navigation in the list when the pending template request finally resolves', async () => {
    let resolve!: (value: { data: UserTemplate[] }) => void;
    api.list.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    await render(); expect(heading()).toBe('Save as Template');
    await click('← Back to list'); expect(heading()).toBe('My Templates');
    expect(client.isFetching()).toBe(1);
    await act(async () => resolve({ data: [saved] }));
    await listReady(); await click('Load');
    expect(props.onSelect).toHaveBeenCalledWith(saved);
});

it('close/reopen resets discarded form state and chooses the fresh draft view, including externally controlled closes', async () => {
    await render(); await type('e.g., My Boltz Config', 'Discard on close');
    await click('✕'); expect(props.onClose).toHaveBeenCalledOnce();
    await render({ isOpen: false }); expect(document.querySelector('h3')).toBeNull();
    await render({ isOpen: true }); expect(heading()).toBe('Save as Template');
    expect(input('e.g., My Boltz Config').value).toBe('');
    await click('Cancel'); await listReady(); await click('Edit');
    await render({ isOpen: false });
    await render({ isOpen: true, currentParams: {} }); await listReady();
    await render({ isOpen: false, currentParams: nativeParams });
    await render({ isOpen: true }); expect(heading()).toBe('Save as Template');
    expect(input('e.g., My Boltz Config').value).toBe('');
});
