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

test('legacy Shape geometry without a surface digest defaults to hash-bound points', async () => {
    memory.clear();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(['shape-geometries'], [{
        geometry_id: 'geom_legacy',
        source_id: 'cad_legacy',
        geometry_sha256: '1'.repeat(64),
        source_sha256: '2'.repeat(64),
        preview_obj_sha256: null,
        point_pool_sha256: '3'.repeat(64),
        sdf_sha256: '4'.repeat(64),
        sdf_sign: 'positive_inside',
        sdf_grid_shape: [48, 48, 48],
        vertex_count: 8,
        face_count: 12,
        point_count: 4096,
        bounds_angstrom: [-1, -1, -1, 1, 1, 1],
        dimensions_angstrom: [2, 2, 2],
        source_format: 'obj',
        source_parser: 'obj_strict_v1',
        source_unit: 'angstrom',
        angstrom_per_unit: 1,
    }]);
    let renderer: ReactTestRenderer;

    await act(async () => {
        renderer = create(React.createElement(
            QueryClientProvider,
            { client },
            React.createElement(MemoryRouter, null, React.createElement(ShapeBlueprintTemplate)),
        ));
    });

    const root = renderer!.root;
    const surfaceButton = root.findAllByType('button').find((node) => node.children.join('') === 'Surface');
    assert.ok(surfaceButton);
    assert.equal(surfaceButton.props.disabled, true);
    const renderedText = root.findAll(() => true)
        .flatMap((node) => node.children)
        .filter((value): value is string => typeof value === 'string')
        .join(' ');
    assert.match(renderedText, /legacy surface.*not hash-bound/i);

    await act(async () => { renderer!.unmount(); });
    client.clear();
});

test('Shape Blueprint admits OBJ and STL while making STL units explicit', async () => {
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
    const fileInput = root.findAllByType('input').find((node) => node.props.type === 'file');
    assert.ok(fileInput);
    assert.equal(fileInput.props.accept, '.obj,.stl');

    await act(async () => fileInput.props.onChange({ target: { files: [{ name: 'printed-part.stl' }] } }));
    const unitSelect = root.findAllByType('select').find((node) =>
        node.findAllByType('option').some((option) => option.props.value === 'millimeter'));
    assert.ok(unitSelect);
    assert.equal(unitSelect.props.value, 'angstrom');
    const unitValues = unitSelect.findAllByType('option').map((option) => option.props.value);
    assert.deepEqual(unitValues, [
        'angstrom', 'nanometer', 'micrometer', 'millimeter', 'centimeter', 'meter', 'inch', 'foot',
    ]);

    const renderedText = root.findAll(() => true)
        .flatMap((node) => node.children)
        .filter((value): value is string => typeof value === 'string')
        .join(' ');
    assert.match(renderedText, /STL files do not encode units/i);
    assert.match(renderedText, /Admit mesh/i);

    await act(async () => { renderer!.unmount(); });
    client.clear();
});
