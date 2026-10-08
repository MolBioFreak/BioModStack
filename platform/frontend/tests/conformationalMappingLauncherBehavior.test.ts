import assert from 'node:assert/strict';
import test from 'node:test';
import * as React from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { ConformationalMappingLauncher } from '../src/components/conformationalMapping/ConformationalMappingLauncher.js';
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
                        backend: 'external_import',
                        registered_artifact_ids: ['stale-source'],
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
    const importSelect = root.findAllByType('select').find((node) =>
        containsOption(node, 'Select one registered mmCIF…'));
    assert.ok(importSelect, 'external-import selector was not rendered');
    assert.equal(importSelect.props.value, '', 'stale persisted source ID was not cleared after refresh');
    assert.equal(
        importSelect.findAllByType('option').some((option) => option.props.value === 'valid-mmcif'),
        true,
    );
    assert.equal(
        importSelect.findAllByType('option').some((option) => String(option.props.children).includes('invalid-pdb')),
        false,
    );
    assert.equal(hasText(root, 'Ready for typed admission'), false);

    await act(async () => {
        importSelect.props.onChange({ target: { value: 'valid-mmcif' } });
    });
    assert.equal(hasText(root, 'Ready for typed admission'), true);

    const submit = root.findAllByType('button').find((node) =>
        node.props.children === 'Submit canonical request');
    assert.ok(submit, 'submit control was not rendered');
    assert.equal(submit.props.disabled, false);
    await act(async () => {
        submit.props.onClick();
        await new Promise<void>((resolve) => setImmediate(resolve));
    });

    assert.equal(submissions.length, 1);
    assert.deepEqual(submissions[0].registered_artifact_ids, ['valid-mmcif']);
    assert.deepEqual(submissions[0].ordered_seeds, [0]);
    assert.equal(submissions[0].samples_per_seed, 1);
    assert.equal('registered_snapshot_id' in submissions[0], false);

    await act(async () => { renderer!.unmount(); });
    client.clear();
});
