import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
    admissionArgs: [] as unknown[],
    admissionData: null as null | Record<string, unknown>,
    invokeCalls: [] as Array<Record<string, unknown>>,
    invokeMock: null as null | Record<string, unknown>,
    catalog: {
        data: {
            machine_serial: '206',
            ownership_generation: 2,
            source_authority_verified: true,
            actions: [] as Array<Record<string, unknown>>,
        },
        error: null,
    },
    dashboard: {
        data: {
            x_axis: {
                provider: {
                    state: 'referenced_ready',
                    lifecycle: {
                        state: 'referenced_ready',
                        awaiting_observation_receipt_id: null,
                    },
                },
            },
        },
        error: null,
    },
}));

const action = (
    actionId: string,
    label: string,
    subsystem: string,
    path: string,
    kind: 'primitive' | 'meta' = 'primitive',
) => ({
    action_id: actionId,
    label,
    subsystem,
    category: kind === 'meta' ? 'meta' : 'route',
    kind,
    safety_class: 'read_only',
    description: label,
    source_anchor: 'OEM source',
    informational_method: 'GET',
    informational_path: path,
    provider_available: true,
    provider_unavailable_reason: null,
    available: true,
    unavailable_reason: null,
    enabled: true,
    disabled_reason: null,
    dependencies: [],
    requires_confirmation: false,
    timeout_seconds: 5,
    inputs: [],
    stages: [],
});

vi.mock('../../src/lib/bioxpClient', () => ({
    useBioXpOperatorControlCatalog: () => state.catalog,
    useBioXpOperatorDashboard: () => state.dashboard,
    useBioXpOperatorActionHistory: () => ({ data: { receipts: [] }, error: null }),
    useBioXpOperatorActionAdmission: (...args: unknown[]) => {
        state.admissionArgs = args;
        state.admissionData ??= { data: { enabled: true, disabled_reason: null, dependencies: [] }, error: null };
        return state.admissionData;
    },
    useInvokeBioXpOperatorAction: () => {
        state.invokeMock ??= {
            data: undefined,
            error: null,
            isPending: false,
            mutate: (payload: Record<string, unknown>) => state.invokeCalls.push(payload),
            reset: vi.fn(),
        };
        return state.invokeMock;
    },
    useAssessBioXpOperatorAction: () => ({ data: undefined, error: null, isPending: false, mutate: vi.fn() }),
    bioXpErrorText: (error: unknown) => String(error),
}));

import { BioXpOperatorControlTabs } from '../../src/components/BioXpOperatorControlTabs';

let container: HTMLDivElement;
let root: Root;

const setSelect = async (value: string) => {
    const selector = container.querySelector('select') as HTMLSelectElement;
    await act(async () => {
        selector.value = value;
        selector.dispatchEvent(new Event('change', { bubbles: true }));
        await Promise.resolve();
    });
};

beforeEach(() => {
    const critical = [
        action('route.motion_power_status', 'Motion Power Status', 'motion.power', '/motion/power/status'),
        action('route.hardware_snapshot', 'Hardware Snapshot Collect', 'hardware', '/hardware/snapshot/collect'),
        action('route.emergency_stop', 'Emergency Stop', 'oem.runtime', '/oem/runtime/emergency_stop'),
        action('route.initialize_system', 'Initialize System', 'oem.startup', '/oem/startup/initialize_system'),
        action('route.axis_status', 'Axis Status', 'motion.axis', '/motion/axis/{axis}/status'),
    ];
    const fillers = Array.from({ length: 135 }, (_, index) => action(
        `route.filler_${index}`,
        `Filler Action ${index}`,
        `subsystem.${index % 20}`,
        `/fixture/${index}`,
    ));
    const meta = [
        action('meta.activate_motion', 'Activate Motion', 'meta', '/operator/actions/meta.activate_motion', 'meta'),
        action('meta.home_xy', 'Home XY', 'meta', '/operator/actions/meta.home_xy', 'meta'),
        action('meta.full_initialization', 'Full Initialization', 'meta', '/operator/actions/meta.full_initialization', 'meta'),
    ];
    const exactXz = [
        action('oem.x.move_steps', 'X Relative Move', 'motion.x', '/operator/actions/oem.x.move_steps'),
        action('oem.z.move_steps', 'Z Relative Move', 'motion.z', '/operator/actions/oem.z.move_steps'),
    ];
    state.catalog.data.source_authority_verified = true;
    state.catalog.data.actions = [...critical, ...fillers, ...exactXz, ...meta];
    state.admissionArgs = [];
    state.admissionData = null;
    state.invokeCalls = [];
    state.invokeMock = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    document.body.replaceChildren();
});

describe('mounted BioXP operator critical and exhaustive controls', () => {
    it('keeps critical controls pinned while every primitive remains grouped and submits only the action contract', async () => {
        await act(async () => {
            root.render(<BioXpOperatorControlTabs generation={2637337272774657} connected />);
            await Promise.resolve();
        });

        const critical = container.querySelector('[data-critical-controls]') as HTMLElement;
        const grouped = container.querySelector('[data-individual-control-groups]') as HTMLElement;
        expect(critical.textContent).toContain('Motion Power Status');
        const exhaustiveIds = [...grouped.querySelectorAll('[data-action-id]')].map((node) => node.getAttribute('data-action-id'));
        expect(exhaustiveIds).toHaveLength(142);
        expect(new Set(exhaustiveIds).size).toBe(142);
        const subsystemDropdowns = [...grouped.querySelectorAll<HTMLDetailsElement>('details[data-subsystem]')];
        expect(subsystemDropdowns.length).toBeGreaterThan(1);
        expect(grouped.querySelectorAll('summary')).toHaveLength(subsystemDropdowns.length);
        expect(subsystemDropdowns.filter((dropdown) => dropdown.open)).toHaveLength(1);
        expect(state.admissionArgs[1]).toBe(2637337272774657);
        expect(state.admissionArgs[2]).toBe(2);

        await setSelect('motion.axis');
        expect(critical.textContent).toContain('Motion Power Status');
        expect(container.querySelectorAll('[data-individual-control-groups] [data-action-id]')).toHaveLength(1);
        expect(grouped.textContent).toContain('Axis Status');

        await setSelect('all');
        const search = container.querySelector('input[type="search"]') as HTMLInputElement;
        await act(async () => {
            const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
            valueSetter?.call(search, 'Filler Action 42');
            search.dispatchEvent(new Event('input', { bubbles: true }));
            await Promise.resolve();
        });
        expect(critical.textContent).toContain('Motion Power Status');
        expect(container.querySelectorAll('[data-individual-control-groups] [data-action-id]')).toHaveLength(1);

        const filler = container.querySelector('[data-individual-control-groups] [data-action-id="route.filler_42"]') as HTMLButtonElement;
        await act(async () => filler.click());
        const run = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Run exactly this action') as HTMLButtonElement;
        await act(async () => run.click());
        expect(state.invokeCalls).toEqual([{
            actionId: 'route.filler_42',
            connectionGeneration: 2637337272774657,
            ownershipGeneration: 2,
            inputs: {},
        }]);
        expect(JSON.stringify(state.invokeCalls)).not.toContain('/fixture/42');

        const metaButton = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Meta Actions') as HTMLButtonElement;
        await act(async () => metaButton.click());
        expect(container.querySelector('[data-critical-controls]')).toBeNull();
        expect(container.querySelectorAll('[role="tab"]')).toHaveLength(3);
    });

    it('lets exact X/Z actions use robot admission when catalog-wide source authority is unavailable', async () => {
        state.catalog.data.source_authority_verified = false;
        await act(async () => {
            root.render(<BioXpOperatorControlTabs generation={2637337272774657} connected />);
            await Promise.resolve();
        });

        const zAction = container.querySelector('[data-action-id="oem.z.move_steps"]') as HTMLButtonElement;
        await act(async () => zAction.click());
        let run = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Run exactly this action') as HTMLButtonElement;
        expect(run.disabled).toBe(false);

        await act(async () => run.click());
        expect(state.invokeCalls[0]).toMatchObject({ actionId: 'oem.z.move_steps' });

        const unrelatedAction = container.querySelector('[data-action-id="route.filler_0"]') as HTMLButtonElement;
        await act(async () => unrelatedAction.click());
        run = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Run exactly this action') as HTMLButtonElement;
        expect(run.disabled).toBe(true);
    });
});
