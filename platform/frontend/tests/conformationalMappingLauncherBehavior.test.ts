import assert from 'node:assert/strict';
import test from 'node:test';
import * as React from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { ConformationalMappingLauncher } from '../src/components/conformationalMapping/ConformationalMappingLauncher.js';
import { compileCmRuntimePolicy } from '../src/components/conformationalMapping/conformationalMappingApi.js';
import type {
    CmSource,
    CmSubmitReceipt,
    CmSubmitRequest,
} from '../src/components/conformationalMapping/conformationalMappingApi.js';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean; React: typeof React }).IS_REACT_ACT_ENVIRONMENT = true;
(globalThis as typeof globalThis & { React: typeof React }).React = React;

const memory = new Map<string, string>();
Object.defineProperty(globalThis, 'sessionStorage', {
    configurable: true,
    value: {
        getItem: (key: string) => memory.get(key) ?? null,
        setItem: (key: string, value: string) => { memory.set(key, value); },
        removeItem: (key: string) => { memory.delete(key); },
        clear: () => { memory.clear(); },
        key: (index: number) => [...memory.keys()][index] ?? null,
        get length() { return memory.size; },
    },
});

const source = (id: string, format: string): CmSource => ({
    source_id: id,
    source_kind: 'structure_upload',
    format,
    sha256: id.padEnd(64, 'a').slice(0, 64),
    bytes: 1024,
    metadata: { target_id: id },
});

const containsOption = (node: ReactTestInstance, text: string): boolean =>
    node.findAllByType('option').some((option) => option.props.children === text);

const hasText = (root: ReactTestInstance, text: string): boolean =>
    root.findAll((node) => node.props.children === text).length > 0;

test('runtime policy overrides are executable only for Protenix', () => {
    assert.deepEqual(compileCmRuntimePolicy('protenix_v2_ensemble', false, 12, 240), {
        use_default_params: false,
        n_cycle: 12,
        n_step: 240,
    });
    assert.deepEqual(compileCmRuntimePolicy('protenix_v2_ensemble', true, 12, 240), { use_default_params: true });
    assert.deepEqual(compileCmRuntimePolicy('confornets', false, 12, 240), { use_default_params: true });
    assert.deepEqual(compileCmRuntimePolicy('external_import', false, 12, 240), { use_default_params: true });
});

test('rendered external import refresh clears stale IDs, filters formats, becomes ready, and submits exact payload', async () => {
    memory.clear();
    const submissions: CmSubmitRequest[] = [];
    const sources = [source('valid-mmcif', 'mmcif'), source('invalid-pdb', 'pdb')];
    const receipt: CmSubmitReceipt = {
        request_id: '11111111-1111-4111-8111-111111111111',
        job_id: '11111111-1111-4111-8111-111111111111',
        status: 'queued',
        backend: 'external_import',
        request_sha256: 'a'.repeat(64),
        coordinate_plan_sha256: 'b'.repeat(64),
        expected_cardinality: 1,
    };
    const services = {
        listSources: async () => sources,
        submitRequest: async (payload: CmSubmitRequest) => {
            submissions.push(payload);
            return receipt;
        },
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    let renderer: ReactTestRenderer;

    await act(async () => {
        renderer = create(React.createElement(
            QueryClientProvider,
            { client },
            React.createElement(
                MemoryRouter,
                null,
                React.createElement(ConformationalMappingLauncher, {
                    initialValues: {
                        name: 'Imported structure',
                        notes: 'Preserve the imported state.',
                        backend: 'external_import',
                        registered_artifact_id: 'stale-source',
                        analysis_policy: {
                            sign_zero_epsilon: 0.002,
                            clash_detector_id: 'bms_clash',
                            clash_detector_version: '1',
                            outer_support_minimum: 0.8,
                            inner_support_minimum: 0.7,
                            sign_consistency_minimum: 0.6,
                            clash_free_minimum: 0.5,
                            rank_stability_minimum: 0.4,
                            minimum_common_ranked_universe_size: 11,
                        },
                    },
                    services,
                }),
            ),
        ));
        await new Promise<void>((resolve) => setTimeout(resolve, 25));
    });
    await act(async () => {
        await new Promise<void>((resolve) => setTimeout(resolve, 0));
    });

    const root = renderer!.root;
    const cachedTab = root.findAllByType('button').find((node) => node.props.children === 'Cached');
    assert.ok(cachedTab, 'cached source tab was not rendered');
    await act(async () => { cachedTab.props.onClick(); });
    assert.equal(hasText(root, 'stale-source'), false, 'stale persisted source ID was not cleared after refresh');
    assert.equal(hasText(root, 'valid-mmcif'), true);
    assert.equal(hasText(root, 'invalid-pdb'), false);
    assert.equal(hasText(root, 'Ready for typed admission'), false);

    const sourceButton = root.findAllByType('button').find((node) => hasText(node, 'valid-mmcif'));
    assert.ok(sourceButton, 'compatible source control was not rendered');
    await act(async () => {
        sourceButton.props.onClick();
    });
    assert.equal(
        hasText(root, 'Ready for typed admission'),
        true,
        root.findAll(() => true).flatMap((node) => node.children.filter((child): child is string => typeof child === 'string')).join(' | '),
    );
    const renderedText = root.findAll(() => true)
        .flatMap((node) => node.children.filter((child): child is string => typeof child === 'string'))
        .join(' | ');
    assert.match(renderedText, /analysis bms_clash\/1/);
    assert.match(renderedText, /support 0\.8\/0\.6/);

    const submit = root.findAllByType('button').find((node) =>
        node.props.children === 'Launch conformational mapping');
    assert.ok(submit, 'submit control was not rendered');
    assert.equal(submit.props.disabled, false);
    await act(async () => {
        submit.props.onClick();
        await new Promise<void>((resolve) => setImmediate(resolve));
    });

    assert.equal(submissions.length, 1);
    assert.equal(submissions[0].registered_artifact_id, 'valid-mmcif');
    assert.equal(submissions[0].notes, 'Preserve the imported state.');
    assert.deepEqual(submissions[0].ordered_seeds, [0]);
    assert.equal(submissions[0].samples_per_seed, 1);
    assert.deepEqual(submissions[0].runtime_policy, { use_default_params: true });
    assert.deepEqual(submissions[0].analysis_policy, {
        sign_zero_epsilon: 0.000001,
        clash_detector_id: 'bms_clash',
        clash_detector_version: '1',
        outer_support_minimum: 0.8,
        inner_support_minimum: 0.6,
        sign_consistency_minimum: 0.8,
        clash_free_minimum: 0.9,
        rank_stability_minimum: 0.6,
        minimum_common_ranked_universe_size: 3,
    });
    assert.equal('registered_snapshot_id' in submissions[0], false);

    await act(async () => { renderer!.unmount(); });
    client.clear();
});


test('rendered pasted sequence canonicalizes bytes, rejects invalid residues, and selects the returned handle', async () => {
    memory.clear();
    const registered: Array<{ kind: string; bytes: string; name: string; metadata: Record<string, unknown> }> = [];
    const available: CmSource[] = [];
    const returned: CmSource = {
        source_id: 'cm_src_sequence_returned',
        source_kind: 'protein_sequence',
        format: 'fasta',
        sha256: 'c'.repeat(64),
        bytes: 4,
        metadata: { sequence: 'ACDE', target_id: 'cm_src_sequence_returned' },
    };
    const services = {
        listSources: async () => [...available],
        registerSource: async (kind: string, file: File, metadata: Record<string, unknown>) => {
            registered.push({ kind, bytes: await file.text(), name: file.name, metadata });
            available.splice(0, available.length, returned);
            return returned;
        },
        submitRequest: async () => { throw new Error('submission is not part of this test'); },
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    let renderer: ReactTestRenderer;
    await act(async () => {
        renderer = create(React.createElement(
            QueryClientProvider,
            { client },
            React.createElement(
                MemoryRouter,
                null,
                React.createElement(ConformationalMappingLauncher, {
                    initialValues: { backend: 'confornets', ordered_seeds: [101, 202, 303] },
                    services,
                }),
            ),
        ));
        await new Promise<void>((resolve) => setTimeout(resolve, 0));
    });

    const root = renderer!.root;
    assert.ok(
        root.findAllByType('input').some((node) => node.props.type === 'number' && node.props.value === '101'),
        'ConforNets did not normalize cloned seed state to one explicit seed',
    );
    const sourceType = root.findAllByType('select').find((node) =>
        containsOption(node, 'Protein sequence'));
    assert.ok(sourceType, 'source-type selector was not rendered');
    await act(async () => { sourceType.props.onChange({ target: { value: 'protein_sequence' } }); });

    const sequenceBox = () => root.findAllByType('textarea').find((node) =>
        String(node.props.placeholder || '').startsWith('MQIFVKTL'))!;
    const registerButton = () => root.findAllByType('button').find((node) =>
        node.props.children === 'Register and select sequence')!;

    await act(async () => { sequenceBox().props.onChange({ target: { value: 'ACD*' } }); });
    await act(async () => { registerButton().props.onClick(); });
    assert.equal(registered.length, 0);
    assert.equal(
        root.findAllByProps({ role: 'alert' })[0].props.children,
        'Paste one-letter protein residues only (ACDEFGHIKLMNPQRSTVWY); FASTA headers are not part of the registered sequence bytes.',
    );

    await act(async () => { sequenceBox().props.onChange({ target: { value: ' ac d\nE ' } }); });
    await act(async () => {
        registerButton().props.onClick();
        await new Promise<void>((resolve) => setImmediate(resolve));
    });
    assert.deepEqual(registered, [{
        kind: 'protein_sequence',
        bytes: 'ACDE',
        name: 'protein-sequence.fasta',
        metadata: { name: 'protein-sequence.fasta' },
    }]);
    assert.equal(hasText(root, returned.source_id), true, 'returned sequence was not selected in the run record');
    await act(async () => { renderer!.unmount(); });
    client.clear();
});


test('rendered stale Protenix source handle remains blocked after registry refresh', async () => {
    memory.clear();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    let renderer: ReactTestRenderer;
    await act(async () => {
        renderer = create(React.createElement(
            QueryClientProvider,
            { client },
            React.createElement(
                MemoryRouter,
                null,
                React.createElement(ConformationalMappingLauncher, {
                    initialValues: { backend: 'protenix_v2_ensemble', registered_snapshot_id: 'stale-snapshot' },
                    services: {
                        listSources: async () => [],
                        submitRequest: async () => { throw new Error('blocked request must not submit'); },
                    },
                }),
            ),
        ));
        await new Promise<void>((resolve) => setTimeout(resolve, 25));
    });
    const root = renderer!.root;
    const submit = root.findAllByType('button').find((node) =>
        node.props.children === 'Launch conformational mapping');
    assert.ok(submit);
    assert.equal(submit.props.disabled, true);
    assert.equal(hasText(root, 'Ready for typed admission'), false);
    await act(async () => { renderer!.unmount(); });
    client.clear();
});
