import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => {
    const mutation = () => ({
        data: undefined as { detail: string; remote_acknowledged: boolean; status?: string } | undefined,
        error: null as unknown | null,
        isPending: false,
        submittedAt: 0,
        mutate: vi.fn(),
    });
    return {
        status: {
            data: {
                connection: {
                    active: true,
                    configured: true,
                    reachable: true,
                    generation: 7,
                    last_error: null,
                    observed_at: '2026-07-30T12:00:00Z',
                    ownership: { transport: 'owned', usb: 'service', router: 'running' },
                    maintenance_state: {
                        motion_blocked: false,
                        recovery_required: false,
                        block_reason: null as string | null,
                    },
                },
                available_commands: [
                    'recover_motion_non_homing',
                    'run_axis_diagnostic',
                    'stop_axis_diagnostic',
                ],
                mutation_access: { enabled: true },
                emergency_stop: { delivery_available: true },
                unavailable_commands: {} as Record<string, string>,
            },
            isError: false,
            error: null as unknown | null,
        },
        execute: mutation(),
        stop: mutation(),
        emergency: mutation(),
        connect: mutation(),
        disconnect: mutation(),
        history: {
            data: { commands: [] as Array<Record<string, unknown>> },
            isError: false,
            error: null as unknown | null,
        },
        commandHookCalls: 0,
    };
});

vi.mock('../../src/lib/bioxpClient', () => ({
    useBioXpStatus: () => state.status,
    useBioXpCommand: () => state.commandHookCalls++ % 2 === 0 ? state.execute : state.stop,
    useBioXpEmergencyStop: () => state.emergency,
    useConnectBioXp: () => state.connect,
    useDisconnectBioXp: () => state.disconnect,
    useBioXpCommandHistory: () => state.history,
    bioXpErrorText: (error: unknown) => error instanceof Error ? error.message : String(error),
    bioXpCommandRecordText: (record: { detail: string; handler_response?: Record<string, unknown> }) => {
        const nested = record.handler_response?.detail;
        const text = typeof nested === 'object' && nested && 'detail' in nested
            ? String((nested as { detail: unknown }).detail)
            : typeof nested === 'string' ? nested : '';
        return text ? `${record.detail} — ${text}` : record.detail;
    },
}));

vi.mock('../../src/components/BioXpCameraPanel', () => ({
    BioXpCameraPanel: () => <div data-testid="camera-panel">Camera</div>,
}));

vi.mock('../../src/components/BioXpQuickDashboard', () => ({
    BioXpQuickDashboard: () => <div data-testid="quick-dashboard">Quick dashboard</div>,
}));

vi.mock('../../src/components/BioXpOperatorControlTabs', () => ({
    BioXpOperatorControlTabs: () => <div data-testid="operator-control-tabs">Operator controls</div>,
}));

import { BioXpCockpit } from '../../src/components/BioXpCockpit';

let container: HTMLDivElement;
let root: Root;

const button = (label: string) => Array.from(container.querySelectorAll('button'))
    .find((element) => element.textContent === label) as HTMLButtonElement | undefined;
const buttons = (label: string) => Array.from(container.querySelectorAll('button'))
    .filter((element) => element.textContent === label) as HTMLButtonElement[];

async function renderCockpit() {
    await act(async () => {
        root.render(<BioXpCockpit />);
        await Promise.resolve();
    });
}

beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    document.body.replaceChildren();
    state.status.isError = false;
    state.status.error = null;
    state.status.data.connection.reachable = true;
    state.status.data.connection.ownership = { transport: 'owned', usb: 'service', router: 'running' };
    state.status.data.connection.maintenance_state = {
        motion_blocked: false,
        recovery_required: false,
        block_reason: null,
    };
    state.status.data.available_commands = [
        'recover_motion_non_homing',
        'run_axis_diagnostic',
        'stop_axis_diagnostic',
    ];
    state.status.data.unavailable_commands = {};
    state.commandHookCalls = 0;
    state.history.data.commands = [];
    state.history.isError = false;
    state.history.error = null;
    for (const mutation of [state.execute, state.stop, state.emergency, state.connect, state.disconnect]) {
        mutation.data = undefined;
        mutation.error = null;
        mutation.isPending = false;
        mutation.submittedAt = 0;
        mutation.mutate.mockClear();
    }
});

describe('mounted BioXP cockpit wiring', () => {
    it('renders the newest ordinary or emergency outcome instead of retained Stop data', async () => {
        state.stop.data = { detail: 'old stop accepted', remote_acknowledged: true };
        state.stop.submittedAt = 10;
        state.execute.data = { detail: 'new move accepted', remote_acknowledged: true };
        state.execute.submittedAt = 20;
        await renderCockpit();
        expect(container.textContent).toContain('new move accepted');
        expect(container.textContent).not.toContain('old stop accepted');

        state.emergency.error = new Error('emergency delivery failed');
        state.emergency.submittedAt = 30;
        await renderCockpit();
        expect(container.textContent).toContain('emergency delivery failed');
        expect(container.textContent).not.toContain('new move accepted');

        state.emergency.error = null;
        state.emergency.data = { detail: 'emergency acknowledged', remote_acknowledged: true };
        state.emergency.submittedAt = 40;
        await renderCockpit();
        expect(container.textContent).toContain('emergency acknowledged');
    });

    it('renders a terminally failed acknowledged receipt as failure, never green success', async () => {
        state.execute.data = {
            detail: 'Robot acknowledged an error payload',
            remote_acknowledged: true,
            status: 'delivery_failed',
        };
        state.execute.submittedAt = 20;
        await renderCockpit();

        const receipt = Array.from(container.querySelectorAll('p'))
            .find((entry) => entry.textContent?.includes('Robot acknowledged an error payload'));
        expect(receipt?.className).toContain('border-red-800');
        expect(receipt?.className).not.toContain('border-emerald-700');
    });

    it('shows a disabled connected state and only offers reconnect after a link error', async () => {
        await renderCockpit();

        expect(button('BMS Link Connected')?.disabled).toBe(true);
        expect(button('Reconnect BMS Link')).toBeUndefined();

        state.status.data.connection.reachable = false;
        await renderCockpit();
        expect(button('Reconnect BMS Link')?.disabled).toBe(false);
    });

    it('keeps Stop available during normal execution and blocks new normal work while Stop is pending', async () => {
        state.execute.isPending = true;
        state.execute.submittedAt = 10;
        await renderCockpit();
        expect(button('Move +')?.disabled).toBe(true);
        expect(buttons('Stop').every((entry) => !entry.disabled)).toBe(true);

        state.execute.isPending = false;
        state.stop.isPending = true;
        state.stop.submittedAt = 20;
        await renderCockpit();
        expect(button('Move +')?.disabled).toBe(true);
        expect(buttons('Stop').every((entry) => entry.disabled)).toBe(true);
    });

    it('fails every hardware control closed when a refetch error retains prior active status data', async () => {
        state.status.isError = true;
        state.status.error = new Error('status failed');
        await renderCockpit();

        expect(button('Claim USB Transport')?.disabled).toBe(true);
        expect(button('Non-homing Recovery')?.disabled).toBe(true);
        expect(button('Move +')?.disabled).toBe(true);
        expect(buttons('Stop').every((entry) => entry.disabled)).toBe(true);
        expect(button('Emergency Abort Unavailable')?.disabled).toBe(true);
        expect(container.textContent).toContain('BioXP status unavailable.');
    });

    it('shows ownership, motion-block reason, and distinct claim/recovery admission', async () => {
        state.status.data.connection.ownership = { transport: 'unbound', usb: 'unbound', router: 'unbound' };
        state.status.data.connection.maintenance_state = {
            motion_blocked: true,
            recovery_required: true,
            block_reason: 'Non-homing recovery required after ownership claim',
        };
        state.status.data.available_commands = ['activate_usb_for_service', 'stop_axis_diagnostic'];
        state.status.data.unavailable_commands = {
            recover_motion_non_homing: 'Robot transport is not service-owned and running',
            run_axis_diagnostic: 'Non-homing recovery required after ownership claim',
        };
        await renderCockpit();

        expect(container.textContent).toContain('unbound / unbound / unbound');
        expect(container.textContent).toContain('Non-homing recovery required after ownership claim');
        expect(button('Claim USB Transport')?.disabled).toBe(false);
        expect(button('Non-homing Recovery')?.disabled).toBe(true);
        expect(button('Move +')?.disabled).toBe(true);
    });

    it('renders USB claim admission and request failures inside transport recovery', async () => {
        state.status.data.available_commands = ['stop_axis_diagnostic'];
        state.status.data.unavailable_commands = {
            activate_usb_for_service: 'USB claim provider is unavailable',
        };
        await renderCockpit();

        const controllerSection = Array.from(container.querySelectorAll('section'))
            .find((section) => section.textContent?.includes('Controller Transport & Recovery'));
        expect(controllerSection?.textContent).toContain('USB claim unavailable: USB claim provider is unavailable');

        state.status.data.available_commands = ['activate_usb_for_service', 'stop_axis_diagnostic'];
        state.status.data.unavailable_commands = {};
        await renderCockpit();
        await act(async () => button('Claim USB Transport')?.click());
        expect(state.execute.mutate).toHaveBeenCalledWith(expect.objectContaining({ command: 'activate_usb_for_service' }));

        state.execute.error = new Error('USB device is already owned by another process');
        state.execute.submittedAt = 30;
        await renderCockpit();
        const updatedControllerSection = Array.from(container.querySelectorAll('section'))
            .find((section) => section.textContent?.includes('Controller Transport & Recovery'));
        expect(updatedControllerSection?.textContent).toContain('USB claim failed: USB device is already owned by another process');

        state.execute.error = null;
        state.execute.data = {
            command_id: 'claim-ok',
            command: 'activate_usb_for_service',
            idempotency_key: 'claim-ok-key',
            generation: 7,
            status: 'acknowledged',
            started_at: '2026-07-30T12:00:00Z',
            finished_at: '2026-07-30T12:00:01Z',
            remote_acknowledged: true,
            physical_effect_verified: false,
            detail: 'USB transport claimed by service',
            handler_response: null,
        };
        state.execute.submittedAt = 40;
        await renderCockpit();
        const successfulControllerSection = Array.from(container.querySelectorAll('section'))
            .find((section) => section.textContent?.includes('Controller Transport & Recovery'));
        expect(successfulControllerSection?.textContent).toContain('USB claim result: USB transport claimed by service');
    });

    it('renders bounded recent command receipts with nested robot detail and effect truth', async () => {
        state.history.data.commands = [{
            command_id: 'cmd-503',
            command: 'recover_motion_non_homing',
            generation: 7,
            status: 'delivery_failed',
            finished_at: '2026-07-30T12:01:00Z',
            remote_acknowledged: false,
            physical_effect_verified: false,
            detail: 'Robot rejected command with HTTP 503',
            handler_response: {
                detail: { detail: 'USB transport is intentionally unbound' },
                debug_blob: 'UNBOUNDED_DEBUG_PAYLOAD_SHOULD_NOT_RENDER'.repeat(300),
            },
        }];
        await renderCockpit();

        expect(container.textContent).toContain('Recent Commands');
        expect(container.textContent).toContain('Robot rejected command with HTTP 503 — USB transport is intentionally unbound');
        expect(container.textContent).toContain('Effect not verified');
        expect(container.textContent).not.toContain('Raw robot receipt');
        expect(container.textContent).not.toContain('UNBOUNDED_DEBUG_PAYLOAD_SHOULD_NOT_RENDER');
    });
});
