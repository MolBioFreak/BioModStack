import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import BinderSelectedControls from '../../src/components/BinderSelectedControls';
import { readBinderSelection, writeBinderSelection } from '../../src/lib/binderContinuation';

let renderer: ReactTestRenderer | undefined;
const original = api.defaults.adapter;
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const posted: Array<Record<string, any>> = [];
const model = { params: [
    { name: 'maturation_repack_enabled', label: 'Repack', type: 'boolean', default: false },
    { name: 'maturation_anchors_enabled', label: 'Anchors', type: 'boolean', default: false },
    { name: 'maturation_flow_enabled', label: 'Flow', type: 'boolean', default: false },
    { name: 'maturation_redesign_enabled', label: 'Redesign', type: 'boolean', default: false },
    { name: 'samples', label: 'Native samples', type: 'integer', default: 1, minimum: 1, maximum: 8 },
    { name: 'pdb_paths', label: 'Owned inputs', type: 'string', hidden: true },
] };
const open = vi.fn(), md = vi.fn();
async function mount(ids = ['page-1-state-a', 'page-2-state-b']) {
    posted.length = 0;
    api.defaults.adapter = async config => {
        let data: unknown = [];
        if (config.url?.startsWith('/api/models/')) data = model;
        if (config.method === 'post') {
            posted.push(JSON.parse(config.data));
            data = { source_job_id: 'round2', root_job_id: 'root', selected_design_count: ids.length,
                operation: posted.at(-1)!.operation, launched_jobs: [{ id: 'queued-child', name: 'fixture queue receipt' }] };
        }
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => { renderer = create(<QueryClientProvider client={client}>
        <BinderSelectedControls sourceJobId="round2" selectedDesignIds={ids} onOpenJob={open} onStartMD={md} />
    </QueryClientProvider>); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
}
const button = (text: string) => renderer!.root.findAllByType('button').find(node => node.children.join('') === text)!;
afterEach(async () => { if (renderer) await act(async () => renderer!.unmount()); renderer = undefined; api.defaults.adapter = original; client.clear(); vi.clearAllMocks(); });

it('mounts model-owned typed controls and submits ALL selected page/state identities with independent false values', async () => {
    await mount();
    const checks = renderer!.root.findAllByType('input').filter(node => node.props.type === 'checkbox');
    expect(checks).toHaveLength(4);
    await act(async () => checks[0].props.onChange({ target: { checked: true } }));
    await act(async () => button('Run selected operation').props.onClick());
    expect(posted).toHaveLength(1);
    expect(posted[0]).toMatchObject({ source_job_id: 'round2', design_ids: ['page-1-state-a', 'page-2-state-b'], operation: 'refine',
        params: { maturation_repack_enabled: true, maturation_anchors_enabled: false, maturation_flow_enabled: false, maturation_redesign_enabled: false, samples: 1 } });
    expect(JSON.stringify(renderer!.toJSON())).toContain('queued-child');
    await act(async () => button('Open child Job').props.onClick());
    expect(open).toHaveBeenCalledWith('queued-child');
});

it('keeps modified model settings across operation switches and hands MD the exact selected state ID', async () => {
    await mount();
    const field = renderer!.root.findAllByType('input').find(node => node.props.type === 'number')!;
    await act(async () => field.props.onChange({ target: { value: '5' } }));
    const select = renderer!.root.findByProps({ 'aria-label': 'Binder continuation operation' });
    await act(async () => select.props.onChange({ target: { value: 'caliby' } }));
    await act(async () => select.props.onChange({ target: { value: 'refine' } }));
    expect(renderer!.root.findAllByType('input').find(node => node.props.type === 'number')!.props.value).toBe(5);
    const buttons = renderer!.root.findAllByType('button').filter(node => node.children.join('') === 'Use as GROMACS MD starting structure');
    await act(async () => buttons[1].props.onClick());
    expect(md).toHaveBeenCalledWith('page-2-state-b');
    expect(posted).toHaveLength(0);
});

it('submits the complete global FrustraMPNN settings rather than a simplified score action', async () => {
    await mount();
    await act(async () => renderer!.root.findByProps({ 'aria-label': 'Binder continuation operation' }).props.onChange({ target: { value: 'frustrampnn' } }));
    await act(async () => button('Run selected operation').props.onClick());
    expect(posted[0].operation).toBe('frustrampnn');
    expect(posted[0].frustrampnn_settings).toHaveProperty('protein_selection');
    expect(posted[0].frustrampnn_settings).toHaveProperty('source_structure');
    expect(posted[0].design_ids).toEqual(['page-1-state-a', 'page-2-state-b']);
});

it('shows queue errors without a fabricated child success', async () => {
    await mount();
    api.defaults.adapter = async () => { throw new Error('model-owned request failed'); };
    await act(async () => button('Run selected operation').props.onClick());
    expect(JSON.stringify(renderer!.toJSON())).toContain('model-owned request failed');
    expect(renderer!.root.findAllByProps({ role: 'status' })).toHaveLength(0);
});

it('persists all selected IDs per source job without mixing another round', () => {
    writeBinderSelection('job1', ['p1', 'p2-state1', 'p2-state2']);
    writeBinderSelection('job2', ['other']);
    expect(readBinderSelection('job1')).toEqual(['p1', 'p2-state1', 'p2-state2']);
    expect(readBinderSelection('job2')).toEqual(['other']);
});
