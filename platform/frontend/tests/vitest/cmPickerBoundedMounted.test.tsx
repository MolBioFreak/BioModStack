import assert from 'node:assert/strict';
import React from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, test, vi } from 'vitest';
import { ConformationalMappingLauncher } from '../../src/components/conformationalMapping/ConformationalMappingLauncher';
import { listCmSourcePage, getCmSource, type CmSource, type CmSourcePageQuery } from '../../src/components/conformationalMapping/conformationalMappingApi';
import { api } from '../../src/lib/api';

vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/HostedMsaControls', () => ({ HostedMsaControls: () => null }));
const text = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
const flush = async () => { for (let i = 0; i < 8; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const source = (id: string, kind: CmSource['source_kind'] = 'complex_snapshot'): CmSource => ({
    source_id: id, source_kind: kind, format: kind.startsWith('structure') ? 'mmcif' : 'json',
    sha256: 'a'.repeat(64), bytes: 2048, metadata: { target_ids: ['target-a'], scientific: 'immutable' },
    managed_checkpoint: kind === 'confornets_checkpoint',
    authority_receipt: { schema_name: 'cm_source_authority_receipt', schema_version: 1,
        source_id: id, source_kind: kind, content_sha256: 'a'.repeat(64), receipt_sha256: 'b'.repeat(64),
        authority_kind: 'complex_snapshot_normalization', payload: { target_ids: ['target-a'], chain_ids: ['A'], entity_ids: ['entity-a'] } },
});
const summary = (item: CmSource): CmSource => ({ ...item, summary: true, metadata: {}, authority_receipt: undefined });
const saved = () => JSON.parse(sessionStorage.getItem('bms.conformational-mapping.launcher.v1') || '{}');
const mount = async (services: Record<string, unknown>, initialValues: Record<string, unknown> = {}) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    const drafts: Record<string, unknown>[] = [];
    let renderer: ReactTestRenderer;
    await act(async () => { renderer = create(<MemoryRouter><QueryClientProvider client={client}>
        <ConformationalMappingLauncher initialValues={initialValues} onDraftChange={(draft) => drafts.push(draft)} services={{
            loadFrustrampnnIntegration: async () => ({ workflows: { conformational_mapping: { default_enabled: true, enabled_summary: 'Required' } } }),
            inspectFrustrampnnSource: async () => { throw new Error('Fixture inspection intentionally unavailable'); },
            ...services,
        } as never} />
    </QueryClientProvider></MemoryRouter>); });
    await flush();
    return { renderer: renderer!, drafts, close: async () => { await act(async () => renderer.unmount()); client.clear(); } };
};
const click = async (renderer: ReactTestRenderer, label: string) => {
    const button = renderer.root.findAllByType('button').find(node => text(node) === label);
    assert.ok(button, label); assert.ok(!button.props.disabled, label);
    await act(async () => button.props.onClick()); await flush();
};
afterEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); });

test('actual mounted picker pages and searches without losing off-page selected handles or native draft', async () => {
    const selected = [source('saved-snapshot'), source('saved-sequence', 'protein_sequence'),
        source('saved-checkpoint', 'confornets_checkpoint'), source('saved-config', 'confornets_config'),
        source('saved-state', 'confornets_state'), source('saved-import', 'structure_upload'), source('saved-reference', 'structure_artifact')];
    const details: string[] = [];
    const pages: CmSourcePageQuery[] = [];
    const services = {
        listSourcePage: async (query: CmSourcePageQuery) => {
            pages.push(query);
            return { sources: query.search ? [] : [summary(source(query.after ? 'page-two' : 'page-one'))],
                next_cursor: query.after || query.search ? null : 'page-one', managed_source: summary(selected[2]) };
        },
        getSource: async (id: string) => { details.push(id); return selected.find(item => item.source_id === id) || source(id); },
    };
    const mounted = await mount(services, { name: 'Retained draft', notes: 'Exact notes', snapshotId: selected[0].source_id,
        sequenceId: selected[1].source_id, checkpointId: selected[2].source_id, configId: selected[3].source_id,
        transferId: selected[4].source_id, importIds: [selected[5].source_id], referenceIds: [selected[6].source_id],
        ordered_seeds: [0, 17], samples_per_seed: 3, feature_policy: { protein_msa_enabled: false, templates_enabled: false } });
    assert.deepEqual(new Set(details), new Set(selected.map(item => item.source_id)));
    const before = saved(); assert.equal(before.snapshotId, 'saved-snapshot');
    const requestBefore = mounted.drafts.at(-1);
    assert.equal((requestBefore?.request as Record<string, unknown>).registered_snapshot_id, 'saved-snapshot');
    assert.deepEqual((requestBefore?.request as Record<string, unknown>).ordered_seeds, [0, 17]);
    await click(mounted.renderer, 'Next sources');
    assert.equal(pages.at(-1)?.after, 'page-one');
    await act(async () => mounted.renderer.root.findByProps({ 'aria-label': 'Search registered source IDs' }).props.onChange({ target: { value: 'not-present' } }));
    await flush();
    assert.equal(pages.at(-1)?.search, 'not-present'); assert.equal(pages.at(-1)?.after, undefined);
    assert.deepEqual(saved(), before);
    assert.deepEqual(mounted.drafts.at(-1), requestBefore);
    const filter = mounted.renderer.root.findByProps({ 'aria-label': 'Inventory source kind' });
    assert.equal(filter.findAllByType('option').length, 8); // all seven kinds plus All
    await act(async () => filter.props.onChange({ target: { value: 'confornets_config' } })); await flush();
    assert.equal(pages.at(-1)?.source_kind, 'confornets_config'); assert.deepEqual(saved(), before);
    assert.deepEqual(mounted.drafts.at(-1), requestBefore);
    await mounted.close();
    const reopened = await mount(services); assert.deepEqual(saved(), before); await reopened.close();
    console.log('CM_PICKER_MOUNTED_METRIC', JSON.stringify({ page_requests: pages.length, selected_detail_ids: new Set(details).size, unselected_detail_reads: details.filter(id => id.startsWith('page-')).length }));
});

test('mounted selection resolves exact detail only on demand and keeps it outside the next page', async () => {
    const details: string[] = [];
    const mounted = await mount({
        listSourcePage: async (query: CmSourcePageQuery) => ({ sources: [summary(source(query.after ? 'next' : 'chosen'))], next_cursor: query.after ? null : 'chosen', managed_source: null }),
        getSource: async (id: string) => { details.push(id); return source(id); },
    });
    assert.deepEqual(details, []);
    const tab = mounted.renderer.root.findByProps({ id: 'cm-source-tab-cached' });
    await act(async () => tab.props.onClick()); await flush();
    const card = mounted.renderer.root.findAllByType('button').find(node => text(node).startsWith('chosen'))!;
    await act(async () => card.props.onClick()); await flush();
    assert.deepEqual(details, ['chosen']); assert.equal(saved().snapshotId, 'chosen');
    assert.match(text(mounted.renderer.root), /Chain A/);
    await click(mounted.renderer, 'Next sources'); assert.equal(saved().snapshotId, 'chosen');
    assert.deepEqual(details, ['chosen']);
    await click(mounted.renderer, 'Inspect next');
    assert.deepEqual(details, ['chosen', 'next']); assert.equal(saved().snapshotId, 'chosen');
    assert.match(text(mounted.renderer.root), /Exact source detail: next/);
    assert.match(text(mounted.renderer.root), /immutable/);
    await mounted.close();
});

test.each([0, 1, 2])('mounted automatic managed selection preserves zero/one/multiple candidate behavior (%s)', async count => {
    const mounted = await mount({ listSources: async () => Array.from({ length: count }, (_, i) => source(`managed-${i}`, 'confornets_checkpoint')) });
    assert.equal(saved().checkpointId, count === 1 ? 'managed-0' : ''); await mounted.close();
});

test.each([404, 503])('exact detail status %s preserves existing removal versus diagnostic handling', async status => {
    const mounted = await mount({
        listSourcePage: async () => ({ sources: [], next_cursor: null, managed_source: null }),
        getSource: async () => { throw { response: { status } }; },
    }, { snapshotId: 'saved-off-page', notes: 'Retain this draft', ordered_seeds: [0] });
    assert.equal(saved().snapshotId, status === 404 ? '' : 'saved-off-page');
    assert.equal(saved().notes, 'Retain this draft'); assert.equal(saved().seeds, '0');
    await mounted.close();
});

test('client sends bounded summary filters and uses exact encoded detail path', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue({ data: { sources: [], next_cursor: null, managed_source: null } });
    await listCmSourcePage({ after: 'literal-cursor', search: 'literal_%', source_kind: 'confornets_config' });
    assert.deepEqual(get.mock.calls[0], ['/api/conformational-mapping/sources', { params: { after: 'literal-cursor', search: 'literal_%', source_kind: 'confornets_config', summary: true, limit: 50 } }]);
    await getCmSource('exact/id'); assert.equal(get.mock.calls[1][0], '/api/conformational-mapping/sources/exact%2Fid');
});
