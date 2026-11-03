import { expect, it } from 'vitest';
import type { MethodCompile, MethodValue } from '../../src/lib/bioxpMethods';
import { methodPages, pageNativeActions, pageProvenance, pageState } from '../../src/lib/bioxpMethodPages';

function freeze<T>(value: T): T {
    if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); }
    return value;
}
const compile = (values: Partial<MethodCompile> = {}): MethodCompile => ({ document: null, digest: null, issues: [], ...values });
const node = (step_id: string): MethodValue => ({ step_id, type: 'action', action: 'transfer' });

it('presents all 24 barcode additions as one logical operation without eliding constituent roots', () => {
    const additions = Array.from({ length: 24 }, (_, index) => ['pickup', 'transfer', 'mix', 'eject'].map(action => ({ step_id: `${action}-${index}`, type: 'action', action }))).flat();
    const steps = [node('prepare'), ...additions, { step_id: 'hold', type: 'group', label: 'Hold', steps: [node('heat'), node('cool')] }];
    const method = freeze({ steps, editor_state: { workflow_pages: [{ id: 'barcodes', label: 'Add barcodes', step_ids: additions.map(row => row.step_id) }] } });
    const before = JSON.stringify(method);
    const pages = methodPages(method);
    expect(pages).toHaveLength(4);
    expect(pages[0]).toEqual({ id: 'starting-deck', label: 'Starting deck', paths: [], nodes: [] });
    expect(pages[2]).toMatchObject({ id: 'barcodes', label: 'Add barcodes', nodes: additions });
    expect(pages[3].nodes).toEqual([steps.at(-1)]);
    expect(pages.flatMap(page => page.nodes)).toEqual(steps);
    expect(pages.flatMap(page => page.paths)).toEqual(steps.map((_, index) => `/steps/${index}`));
    expect(pages[2].nodes[0]).toBe(additions[0]);
    expect(JSON.stringify(method)).toBe(before);
});

it.each([
    [{ id: 'stale', label: 'Stale', step_ids: ['a', 'missing'] }],
    [{ id: 'reversed', label: 'Reversed', step_ids: ['b', 'a'] }],
    [{ id: 'gap', label: 'Gap', step_ids: ['a', 'c'] }],
    [{ id: 'twice', label: 'Twice', step_ids: ['a', 'a'] }],
    [{ id: 'same', label: 'First', step_ids: ['a', 'b'] }, { id: 'same', label: 'Second', step_ids: ['c'] }],
    [{ id: 'one', label: 'First', step_ids: ['a', 'b'] }, { id: 'two', label: 'Second', step_ids: ['b', 'c'] }],
    [{ id: 'starting-deck', label: 'Reserved', step_ids: ['a', 'b'] }],
    [{ id: 'blank', label: '', step_ids: ['a', 'b'] }],
    [{ id: 'empty', label: 'Empty', step_ids: [] }],
])('falls back without losing/reordering roots for invalid metadata %#', (...workflow_pages) => {
    const steps = [node('a'), node('b'), node('c')];
    const pages = methodPages(freeze({ steps, editor_state: { workflow_pages } }));
    expect(pages.slice(1).map(page => page.nodes)).toEqual(steps.map(step => [step]));
});

it('keeps independent valid metadata despite stale rows and avoids fallback ID collisions', () => {
    const steps = [node('a'), node('b'), node('c'), node('d')];
    const pages = methodPages({ steps, editor_state: { workflow_pages: [
        { id: 'stale', label: 'Stale', step_ids: ['missing'] },
        { id: 'step:/steps/0', label: 'Joined', step_ids: ['b', 'c'] },
    ] } });
    expect(pages.map(page => page.nodes)).toEqual([[], [steps[0]], [steps[1], steps[2]], [steps[3]]]);
    expect(pages[2].id).toBe('step:/steps/0');
    expect(new Set(pages.map(page => page.id)).size).toBe(pages.length);
});

it('retains malformed roots, duplicate step IDs, empty groups and repeat-zero pages', () => {
    const steps = [node('a'), node('a'), null, { step_id: 'empty', type: 'group', steps: [] }, { step_id: 'zero', type: 'repeat', count: 0, steps: [node('child')] }];
    const pages = methodPages(freeze({ steps, editor_state: { workflow_pages: [{ id: 'duplicate', label: 'Duplicate', step_ids: ['a'] }] } }));
    expect(pages.slice(1).map(page => page.nodes)).toEqual(steps.map(step => [step]));
    expect(new Set(pages.map(page => page.id)).size).toBe(pages.length);
    for (const page of pages.slice(1)) expect(pageState(page, pages, compile({ simulation: { state: { final: true } } }), {})).toBeNull();
    expect(methodPages({})).toHaveLength(1);
});

it('keeps called procedure actions on the calling page without confusing adjacent call paths', () => {
    const pages = methodPages({ steps: Array.from({ length: 11 }, (_, i) => ({ step_id: `call-${i}`, type: 'call', procedure_id: 'p' })) });
    const result = compile({ provenance: [
        { path: '/method/procedures/0/steps/0', call_path: [{ path: '/method/steps/1' }], occurrence_id: 'one', native_action_ids: ['a'] },
        { path: '/dependencies/procedures/p/steps/0', call_path: [{ path: '/method/steps/10' }], occurrence_id: 'ten', native_action_ids: ['b'] },
    ], document: { stages: [{ actions: [{ action_id: 'a' }, { action_id: 'b' }] }] } });
    expect(pageProvenance(pages[2], result).map(p => p.occurrence_id)).toEqual(['one']);
    expect(pageNativeActions(pages[2], result)).toEqual([{ action_id: 'a' }]);
    expect(pageNativeActions(pages[11], result)).toEqual([{ action_id: 'b' }]);
});

it('uses full slash-boundary provenance and preserves all native document actions in order', () => {
    const pages = methodPages({ steps: Array.from({ length: 11 }, (_, index) => node(String(index))) });
    const page = pages[2];
    const provenance = [
        { path: '/method/steps/10', native_action_ids: ['other'] },
        { path: '/method/steps/1/steps/0', occurrence_id: 'first', native_action_ids: ['b', 'a'] },
        { path: '/steps/1', native_action_ids: ['short'] },
        { path: '/method/steps/1', occurrence_id: 'second', native_action_ids: ['c', 'd'] },
        { path: '/method/steps/1suffix', native_action_ids: ['suffix'] },
    ];
    const actions = ['a', 'b', 'other', 'c', 'd', 'short', 'suffix'].map(action_id => ({ action_id, kind: 'note' }));
    const result = freeze(compile({ provenance, document: { stages: [{ actions: actions.slice(0, 3) }, { actions: actions.slice(3) }] } }));
    expect(pageProvenance(page, result)).toEqual([provenance[1], provenance[3]]);
    expect(pageNativeActions(page, result)).toEqual([actions[0], actions[1], actions[3], actions[4]]);
    expect(pageProvenance(pages[0], result)).toEqual([]);
    expect(pageNativeActions(page, undefined)).toEqual([]);
});

it('replays through the last repeated occurrence from producer initial state without mutation or final fallback', () => {
    const pages = methodPages({ steps: [{ type: 'repeat', count: 2, steps: [node('transfer')] }, node('later')] });
    const initial = freeze({ vessels: { source: { volume_ul: 20 }, target: { volume_ul: 0 } }, channels: { '0': { contacts: ['initial'] } } });
    const result = freeze(compile({
        provenance: [
            { path: '/method/steps/0/steps/0', occurrence_id: 'first' },
            { path: '/method/steps/0/steps/0', occurrence_id: 'second' },
            { path: '/method/steps/1', occurrence_id: 'last' },
        ],
        simulation: { initial_state: initial, state: { final: true }, after_occurrences: [
            { occurrence_id: 'first', state_delta: { vessels: { target: { volume_ul: 2 } }, channels: { '0': { contacts_append: ['first'] } } } },
            { occurrence_id: 'second', state_delta: { vessels: { target: { volume_ul: 4 } }, channels: { '0': { contacts_append: ['second'] } } } },
            { occurrence_id: 'last', state_delta: { vessels: { target: { volume_ul: 999 } } } },
        ] },
    }));
    const before = JSON.stringify(result);
    expect(pageState(pages[0], pages, result, { wrong: true })).toEqual(initial);
    expect(pageState(pages[1], pages, result, {})).toEqual({ vessels: { source: { volume_ul: 20 }, target: { volume_ul: 4 } }, channels: { '0': { contacts: ['initial', 'first', 'second'] } } });
    const incomplete = compile({ ...result, provenance: [...result.provenance!, { path: '/method/steps/0/steps/0', occurrence_id: 'missing' }] });
    expect(pageState(pages[1], pages, incomplete, initial)).toBeNull();
    expect(pageState(pages[0], pages, undefined, initial)).toEqual(initial);
    expect(JSON.stringify(result)).toBe(before);
});
