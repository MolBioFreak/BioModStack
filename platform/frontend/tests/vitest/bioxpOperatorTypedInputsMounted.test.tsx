import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BioXpOperatorActionSpec } from '../../src/lib/bioxpClient';

const state = vi.hoisted(() => ({ actions: [] as BioXpOperatorActionSpec[] }));
vi.mock('../../src/lib/bioxpClient', async importOriginal => ({
    ...await importOriginal<typeof import('../../src/lib/bioxpClient')>(),
    useBioXpOperatorControlCatalog: () => ({ data: { actions: state.actions, ownership_generation: 2, source_authority_verified: true }, error: null }),
    useBioXpOperatorDashboard: () => ({ data: {}, error: null }),
    useBioXpOperatorActionHistory: () => ({ data: { items: [], limit: 100, next_cursor: null }, error: null }),
    useBioXpOperatorActionAdmission: () => ({ data: { enabled: true, dependencies: [] }, error: null }),
}));
import { api } from '../../src/lib/api';
import { BioXpOperatorControlTabs } from '../../src/components/BioXpOperatorControlTabs';
import { liquidInputKind } from '../../src/components/BioXpOperatorInput';

const catalog = JSON.parse(readFileSync('../api/tests/fixtures/bioxp_retained_catalog_v1.json', 'utf8'));
const liquidActions: BioXpOperatorActionSpec[] = catalog.actions.filter((action: BioXpOperatorActionSpec) => action.informational_path?.startsWith('/liquid/'));
let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let requests: Array<{ url: string; body: { inputs: Record<string, unknown>; expected_connection_generation: number; expected_ownership_generation: number } }>;
const originalAdapter = api.defaults.adapter;

beforeEach(() => {
    requests = [];
    api.defaults.adapter = async config => {
        requests.push({ url: config.url!, body: JSON.parse(config.data) });
        return { data: undefined, status: 200, statusText: 'OK', headers: {}, config };
    };
    container = document.createElement('div'); document.body.append(container); root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); container.remove(); api.defaults.adapter = originalAdapter; });

async function mount(path: string, transform?: (action: BioXpOperatorActionSpec) => BioXpOperatorActionSpec) {
    const action = structuredClone(liquidActions.find(item => item.informational_path === path)!);
    // Only read-side admission is mocked. Real mounted fields, mutation hook,
    // generation helper, Axios serialization and interceptors are exercised.
    action.enabled = true;
    state.actions = [transform ? transform(action) : action];
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpOperatorControlTabs generation={7} connected /></QueryClientProvider>));
}
async function change(label: string, value: string) {
    const element = container.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLSelectElement;
    expect(element, label).not.toBeNull();
    await act(async () => {
        const prototype = element.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, value);
        element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
    });
}
async function click(text: string) {
    const button = [...container.querySelectorAll('button')].find(item => item.textContent === text)!;
    expect(button, text).toBeDefined(); await act(async () => button.click());
}
async function run() {
    const confirmation = [...container.querySelectorAll('label')].find(label => label.textContent?.includes('I confirm this exact governed action'))?.querySelector('input');
    if (confirmation && !confirmation.checked) await act(async () => confirmation.click());
    await click('Run exactly this action');
    await vi.waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0].url).toBe(`/api/bioxp/operator-controls/actions/${encodeURIComponent(state.actions[0].action_id)}`);
    expect(requests[0].body).toMatchObject({ expected_connection_generation: 7, expected_ownership_generation: 2 });
    return requests[0].body.inputs;
}

describe('all retained published liquid action input types', () => {
    it.each(liquidActions.map(action => [action.informational_path, action] as const))('%s renders every published input and submits through the shared helper', async (path, action) => {
        await mount(path);
        const expected: Record<string, unknown> = {};
        for (const input of action.inputs) {
            const label = liquidInputKind(action, input) === 'location' ? input.name : input.label;
            expect(container.querySelector(`[aria-label="${label} presence"]`), input.name).not.toBeNull();
            if (input.default != null) expected[input.name] = input.default;
            if (input.required && input.default == null) {
                await change(`${label} presence`, 'value');
                if (input.value_type === 'enum') { await change(label, input.enum_values[0]); expected[input.name] = input.enum_values[0]; }
                else if (input.value_type === 'boolean') expected[input.name] = false;
                else if (input.value_type === 'integer' || input.value_type === 'number') { await change(label, '1'); expected[input.name] = 1; }
                else { await change(label, 'context'); expected[input.name] = 'context'; }
            }
        }
        expect(container.querySelector('[role="tabpanel"] textarea')).toBeNull();
        expect(await run()).toEqual(expected);
    });

    it('edits channels, source/well context and nested metadata without turning context into positioning', async () => {
        await mount('/liquid/aspirate');
        await change('Volume Ul presence', 'value'); await change('Volume Ul', '12.5');
        await change('Channels presence', 'value');
        for (const channel of [0, 3]) await act(async () => (container.querySelector(`[aria-label="Channels channel ${channel}"]`) as HTMLInputElement).click());
        await change('source presence', 'value');
        for (const [name, value] of [['location_id', 'LOC_RC'], ['well_id', 'A1'], ['plate_name', ''], ['z_offset_steps', '0']]) {
            await change(`source.${name} presence`, 'value'); await change(`source.${name}`, value);
        }
        expect(container.textContent).toContain('These fields do not position the robot or move to a well');
        await change('Metadata presence', 'value');
        await change('Metadata property name', 'rows'); await click('Add Metadata property');
        await change('Metadata.rows value type', 'list'); await click('Add Metadata.rows item');
        await change('Metadata.rows[0] value type', 'object');
        for (const [name, type] of [['zero', 'number'], ['off', 'boolean'], ['nothing', 'null'], ['empty', 'string'], ['list', 'list'], ['object', 'object']]) {
            await change('Metadata.rows[0] property name', name); await click('Add Metadata.rows[0] property');
            await change(`Metadata.rows[0].${name} value type`, type);
        }
        await change('Air Gap Ul presence', 'value');
        await change('Operator presence', 'null');
        const inputs = await run();
        expect(inputs).toEqual({ volume_ul: 12.5, pressure_profile: '1R', channels: [0, 3], source: { location_id: 'LOC_RC', well_id: 'A1', plate_name: '', z_offset_steps: 0 }, air_gap_ul: 0, operator: null,
            metadata: { rows: [{ zero: 0, off: false, nothing: null, empty: '', list: [], object: {} }] } });
        expect(requests).toHaveLength(1); // no positioning side request
    });

    it.each(['destination', 'dest'])('preserves the published %s alias independently, false, empty channels and explicit null', async name => {
        await mount('/liquid/dispense');
        await change('Volume Ul presence', 'value'); await change('Volume Ul', '5');
        await change(`${name} presence`, 'value'); await change(`${name}.location_id presence`, 'value'); await change(`${name}.location_id`, 'plate');
        await change(`${name}.well_id presence`, 'null');
        await change('Channels presence', 'value');
        const inputs = await run();
        expect(inputs).toEqual({ volume_ul: 5, pressure_profile: '1R', blow_out: false, dispense_type: 0, [name]: { location_id: 'plate', well_id: null }, channels: [] });
    });

    it('edits and removes recursive list/object entries while preserving all other hydrated values', async () => {
        await mount('/liquid/terminate', action => ({ ...action, inputs: action.inputs.map(input => input.name === 'metadata' ? { ...input, default: { rows: [false, 0, null, '', [], {}], keep: { empty: '', off: false } } } : input) }));
        await click('Remove Metadata.rows[1]');
        await click('Remove Metadata.keep.empty');
        await change('Metadata.rows[0] value type', 'string'); await change('Metadata.rows[0] value', 'edited');
        await change('Reason presence', 'value'); await change('Reason', '');
        expect(await run()).toEqual({ reason: '', metadata: { rows: ['edited', null, '', [], {}], keep: { off: false } } });
    });

    it('uses resolved liquid context schemas and keeps the compact channel selector', async () => {
        await mount('/liquid/aspirate', action => ({ ...action, inputs: action.inputs.map(input => input.name === 'source'
            ? { ...input, json_schema: { anyOf: [{ type: 'object', properties: {
                location_id: { type: 'string', minLength: 1, maxLength: 120 },
                well_id: { anyOf: [{ type: 'string' }, { type: 'null' }], default: null },
                plate_name: { anyOf: [{ type: 'string' }, { type: 'null' }], default: null },
                z_offset_steps: { anyOf: [{ type: 'integer' }, { type: 'null' }], default: null },
            }, required: ['location_id'] }, { type: 'null' }], default: null } }
            : input.name === 'channels' ? { ...input, json_schema: { anyOf: [{ type: 'array', items: { type: 'integer' } }, { type: 'null' }], default: null } } : input) }));
        await change('Volume Ul presence', 'value'); await change('Volume Ul', '2.25');
        await change('Channels presence', 'value');
        await act(async () => (container.querySelector('[aria-label="Channels channel 2"]') as HTMLInputElement).click());
        await change('source presence', 'value'); await change('source.location_id presence', 'value'); await change('source.location_id', 'LOC_RC');
        await change('source.well_id presence', 'value');
        await change('source.z_offset_steps presence', 'value'); await change('source.z_offset_steps variant', '0'); await change('source.z_offset_steps', '-12');
        expect(await run()).toEqual({ volume_ul: 2.25, pressure_profile: '1R', source: { location_id: 'LOC_RC', well_id: null, z_offset_steps: -12 }, channels: [2] });
    });

    it('uses published object/list schemas, typed enum values, nullable defaults and nested bounds', async () => {
        await mount('/liquid/terminate', action => ({ ...action, inputs: action.inputs.map(input => input.name === 'metadata' ? { ...input, json_schema: {
            anyOf: [{ type: 'object', additionalProperties: false, required: ['rows'], properties: {
                rows: { type: 'array', minItems: 1, maxItems: 3, items: { type: 'object', additionalProperties: false, properties: {
                    channel: { type: 'integer', enum: [0, 1, 2, 3], default: 0 },
                    amount: { type: 'number', exclusiveMinimum: 0, maximum: 100, default: 0 },
                    off: { type: 'boolean', default: false },
                    note: { anyOf: [{ type: 'string' }, { type: 'null' }], default: null },
                    empty: { type: 'string', default: '' },
                } } },
            } }, { type: 'null' }], default: null,
        } } : input) }));
        expect((container.querySelector('[aria-label="Metadata presence"]') as HTMLSelectElement).value).toBe('null');
        await change('Metadata presence', 'value');
        await change('Metadata.rows presence', 'value'); await click('Add Metadata.rows item');
        for (const name of ['channel', 'amount', 'off', 'note', 'empty']) await change(`Metadata.rows[0].${name} presence`, 'value');
        await change('Metadata.rows[0].channel', '3');
        const number = container.querySelector('[aria-label="Metadata.rows[0].amount"]') as HTMLInputElement;
        expect(number.max).toBe('100'); expect(container.textContent).toContain('> 0');
        await change('Metadata.rows[0].amount', '4.5');
        await change('Metadata.rows[0].note variant', '0'); await change('Metadata.rows[0].note', '');
        await click('Add Metadata.rows item'); await click('Remove Metadata.rows[1]');
        expect(await run()).toEqual({ metadata: { rows: [{ channel: 3, amount: 4.5, off: false, note: '', empty: '' }] } });
    });

    it('renders self-contained $defs and discriminated manual steps without sending schema metadata', async () => {
        await mount('/liquid/terminate', action => ({ ...action, inputs: action.inputs.map(input => input.name === 'metadata' ? { ...input,
            json_schema: { type: 'array', items: { oneOf: [{ $ref: '#/$defs/Move' }, { $ref: '#/$defs/Lift' }, { $ref: '#/$defs/Liquid' }],
                discriminator: { propertyName: 'operation', mapping: { move: '#/$defs/Move', lift: '#/$defs/Lift', aspirate: '#/$defs/Liquid', dispense: '#/$defs/Liquid' } } },
                $defs: {
                    Move: { title: 'Move', type: 'object', additionalProperties: false, properties: { operation: { const: 'move' }, location_id: { type: 'integer' }, well: { type: 'string' } } },
                    Lift: { title: 'Lift', type: 'object', additionalProperties: false, properties: { operation: { const: 'lift' }, location_id: { type: 'integer' }, height_steps: { anyOf: [{ type: 'integer' }, { type: 'null' }] } } },
                    Liquid: { title: 'Liquid', type: 'object', required: ['operation'], additionalProperties: false, properties: { operation: { type: 'string', enum: ['aspirate', 'dispense'] }, speed: { type: 'number' } } },
                } },
        } : input) }));
        await change('Metadata presence', 'value'); await click('Add Metadata item');
        expect((container.querySelector('[aria-label="Metadata[0].operation"]') as HTMLSelectElement).selectedOptions[0].textContent).toBe('move');
        await change('Metadata[0].location_id presence', 'value'); await change('Metadata[0].location_id', '4');
        await change('Metadata[0].well presence', 'value'); await change('Metadata[0].well', 'C3');
        await click('Add Metadata item'); await change('Metadata[1] variant', '1');
        expect(container.querySelector('[aria-label="Metadata[1].well presence"]')).toBeNull();
        await change('Metadata[1].height_steps presence', 'value'); await change('Metadata[1].height_steps variant', '1');
        await click('Add Metadata item'); await change('Metadata[2] variant', '2');
        await change('Metadata[2].operation', '1'); await change('Metadata[2].speed presence', 'value'); await change('Metadata[2].speed', '62.5');
        expect(await run()).toEqual({ metadata: [{ operation: 'move', location_id: 4, well: 'C3' }, { operation: 'lift', height_steps: null }, { operation: 'dispense', speed: 62.5 }] });
    });

    it('keeps recursive refs lazy and literal $ref values unchanged', async () => {
        await mount('/liquid/terminate', action => ({ ...action, inputs: action.inputs.map(input => input.name === 'metadata' ? { ...input,
            json_schema: { $ref: '#/$defs/Node', $defs: { Node: { type: 'object', additionalProperties: false, properties: {
                literal: { type: 'object', default: { $ref: '#/not-a-schema-ref' } },
                children: { type: 'array', items: { $ref: '#/$defs/Node' } },
            } } } },
        } : input) }));
        await change('Metadata presence', 'value'); await change('Metadata.literal presence', 'value');
        await change('Metadata.children presence', 'value'); await click('Add Metadata.children item');
        await change('Metadata.children[0].children presence', 'value');
        expect(await run()).toEqual({ metadata: { literal: { $ref: '#/not-a-schema-ref' }, children: [{ children: [] }] } });
    });

    it('preserves top-level schema null/false/zero/empty defaults through the shared invoke transport', async () => {
        await mount('/liquid/terminate', action => ({ ...action, inputs: action.inputs.map(input => ({ ...input, json_schema: input.name === 'metadata'
            ? { type: 'object', default: { off: false, zero: 0, empty: '', nothing: null, rows: [] } }
            : { anyOf: [{ type: 'string' }, { type: 'null' }], default: null } })) }));
        expect(await run()).toEqual({ operator: null, reason: null, metadata: { off: false, zero: 0, empty: '', nothing: null, rows: [] } });
    });

    it('can return an edited value to omission without emitting undefined/null/default replacements', async () => {
        await mount('/liquid/eject-all');
        await change('Channels presence', 'value'); await change('Channels presence', 'omitted');
        await change('Wait presence', 'omitted');
        expect(await run()).toEqual({ check_missing_tip: true });
    });
});
