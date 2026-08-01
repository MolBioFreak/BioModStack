import assert from 'node:assert/strict';
import test from 'node:test';
import * as React from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import ShapeBlueprintTemplate from '../src/components/ShapeBlueprintTemplate.js';
import CanonicalMeshPreview from '../src/components/CanonicalMeshPreview.js';
import { buildShapeLaunchRequest } from '../src/lib/shapeBlueprintLaunch.js';

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

test('Shape launch binds the exact reviewed geometry manifest digest', () => {
    const geometry = {
        geometry_id: 'geom_reviewed', source_id: 'cad_reviewed',
        geometry_sha256: '1'.repeat(64), manifest_sha256: '2'.repeat(64),
        source_sha256: '3'.repeat(64), preview_obj_sha256: '4'.repeat(64),
        point_pool_sha256: '5'.repeat(64), sdf_sha256: '6'.repeat(64),
        sdf_sign: 'positive_inside' as const, sdf_grid_shape: [48, 48, 48] as [number, number, number],
        vertex_count: 4, face_count: 4, point_count: 4096,
        bounds_angstrom: [0, 0, 0, 1, 1, 1] as [number, number, number, number, number, number],
        dimensions_angstrom: [1, 1, 1] as [number, number, number],
        source_format: 'stl' as const, source_parser: 'stl_ascii_v1' as const,
        source_unit: 'angstrom', angstrom_per_unit: 1,
    };
    const request = buildShapeLaunchRequest(geometry, {
        client_request_id: 'review-1', name: 'reviewed', target_length: 100,
        num_backbones: 1, sequences_per_backbone: 1, seed: 0,
    });
    assert.equal(request.expected_geometry_manifest_sha256, geometry.manifest_sha256);
    assert.equal(request.geometry_id, geometry.geometry_id);
});

test('canonical surface canvas clears immediately when its URL changes', async () => {
    const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
    Object.defineProperty(globalThis, 'window', { configurable: true, value: { devicePixelRatio: 1 } });
    const originalFetch = globalThis.fetch;
    const pending = new Map<string, (response: Response) => void>();
    globalThis.fetch = ((input: RequestInfo | URL) => new Promise<Response>((resolve) => {
        pending.set(String(input), resolve);
    })) as typeof fetch;
    let clearCount = 0;
    const context = {
        setTransform() {}, clearRect() { clearCount += 1; }, fillRect() {},
        beginPath() {}, moveTo() {}, lineTo() {}, closePath() {}, fill() {}, stroke() {},
        fillStyle: '', strokeStyle: '', lineWidth: 0,
    } as unknown as CanvasRenderingContext2D;
    const canvas = {
        width: 0,
        height: 0,
        getBoundingClientRect: () => ({ width: 900 }),
        getContext: () => context,
    };
    const obj = '# bms_shape_canonical_obj_v1\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n';
    let renderer: ReactTestRenderer;

    try {
        await act(async () => {
            renderer = create(
                React.createElement(CanonicalMeshPreview, { url: '/geometry-a.obj', label: 'surface' }),
                { createNodeMock: (element) => element.type === 'canvas' ? canvas : null },
            );
        });
        await act(async () => {
            pending.get('/geometry-a.obj')!({ ok: true, status: 200, text: async () => obj } as Response);
            await Promise.resolve();
            await Promise.resolve();
        });
        const afterFirstDraw = clearCount;
        assert.ok(afterFirstDraw > 0);

        await act(async () => {
            renderer!.update(React.createElement(CanonicalMeshPreview, { url: '/geometry-b.obj', label: 'surface' }));
        });
        assert.ok(clearCount > afterFirstDraw, 'URL change must clear stale surface pixels before the next fetch resolves');
        await act(async () => { renderer!.unmount(); });
    } finally {
        globalThis.fetch = originalFetch;
        if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow);
        else Reflect.deleteProperty(globalThis, 'window');
    }
});

test('canonical surface ignores a stale fetch that completes after the selected URL', async () => {
    const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
    Object.defineProperty(globalThis, 'window', { configurable: true, value: { devicePixelRatio: 1 } });
    const originalFetch = globalThis.fetch;
    const pending = new Map<string, (response: Response) => void>();
    globalThis.fetch = ((input: RequestInfo | URL) => new Promise<Response>((resolve) => {
        pending.set(String(input), resolve);
    })) as typeof fetch;
    const context = {
        setTransform() {}, clearRect() {}, fillRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
        closePath() {}, fill() {}, stroke() {}, fillStyle: '', strokeStyle: '', lineWidth: 0,
    } as unknown as CanvasRenderingContext2D;
    const canvas = { width: 0, height: 0, getBoundingClientRect: () => ({ width: 900 }), getContext: () => context };
    const oldObj = '# bms_shape_canonical_obj_v1\nv 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\nf 1 2 3\nf 1 3 4\n';
    const newObj = '# bms_shape_canonical_obj_v1\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n';
    let renderer: ReactTestRenderer;
    try {
        await act(async () => {
            renderer = create(React.createElement(CanonicalMeshPreview, { url: '/old.obj', label: 'surface' }), {
                createNodeMock: (element) => element.type === 'canvas' ? canvas : null,
            });
        });
        await act(async () => {
            renderer!.update(React.createElement(CanonicalMeshPreview, { url: '/new.obj', label: 'surface' }));
        });
        assert.ok(pending.has('/new.obj'));
        await act(async () => {
            pending.get('/new.obj')!({ ok: true, status: 200, text: async () => newObj } as Response);
            await Promise.resolve(); await Promise.resolve();
        });
        await act(async () => {
            pending.get('/old.obj')!({ ok: true, status: 200, text: async () => oldObj } as Response);
            await Promise.resolve(); await Promise.resolve();
        });
        assert.ok(renderer!.root.findAll((node) => node.children.join('') === '3 vertices · 1 faces').length);
        assert.equal(renderer!.root.findByType('a').props.href, '/new.obj');
        await act(async () => { renderer!.unmount(); });
    } finally {
        globalThis.fetch = originalFetch;
        if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow);
        else Reflect.deleteProperty(globalThis, 'window');
    }
});

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
        source_parser: 'obj_triangle_v1',
        manifest_sha256: 'f'.repeat(64),
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
    assert.match(renderedText, /Manifest/i);
    assert.match(renderedText, /ffffffffffff/i);

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
