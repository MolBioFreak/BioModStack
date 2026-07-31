import assert from 'node:assert/strict';
import test from 'node:test';
import * as React from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import ShapeBlueprintTemplate from '../src/components/ShapeBlueprintTemplate.js';

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

const numberInput = (root: ReactTestInstance, min: number, max: number) => {
    const input = root.findAllByType('input').find((node) =>
        node.props.type === 'number' && node.props.min === min && node.props.max === max);
    assert.ok(input, `number input ${min}..${max} was not rendered`);
    return input;
};

test('Shape Blueprint numeric state stays within the API integer contract', async () => {
    memory.clear();
    const client = new QueryClient({ defaultOptions: { queries: { enabled: false, retry: false } } });
    let renderer: ReactTestRenderer;

    await act(async () => {
        renderer = create(React.createElement(
            QueryClientProvider,
            { client },
            React.createElement(MemoryRouter, null, React.createElement(ShapeBlueprintTemplate)),
        ));
    });

    const root = renderer!.root;
    const targetLength = numberInput(root, 40, 600);
    const backbones = numberInput(root, 1, 32);
    const sequences = numberInput(root, 1, 8);
    const seed = numberInput(root, 0, 2147483647);

    assert.equal(seed.props.value, 0);

    await act(async () => targetLength.props.onChange({ target: { value: '41.9' } }));
    assert.equal(numberInput(root, 40, 600).props.value, 41);
    await act(async () => targetLength.props.onChange({ target: { value: '999' } }));
    assert.equal(numberInput(root, 40, 600).props.value, 600);

    await act(async () => backbones.props.onChange({ target: { value: '0' } }));
    assert.equal(numberInput(root, 1, 32).props.value, 1);
    await act(async () => sequences.props.onChange({ target: { value: '9' } }));
    assert.equal(numberInput(root, 1, 8).props.value, 8);
    await act(async () => seed.props.onChange({ target: { value: '-1' } }));
    assert.equal(numberInput(root, 0, 2147483647).props.value, 0);

    await act(async () => { renderer!.unmount(); });
    client.clear();
});
