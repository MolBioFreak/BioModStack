import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => {
    const mutation = () => ({
        data: undefined as { detail: string; remote_acknowledged: boolean } | undefined,
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
                },
                available_commands: [
                    'recover_motion_non_homing',
                    'run_axis_diagnostic',
                    'stop_axis_diagnostic',
                ],
                mutation_access: { enabled: true },
                emergency_stop: { delivery_available: true },
            },
            isError: false,
            error: null as unknown | null,
        },
        execute: mutation(),
        stop: mutation(),
        emergency: mutation(),
        connect: mutation(),
        disconnect: mutation(),
        commandHookCalls: 0,
    };
});

vi.mock('../../src/lib/bioxpClient', () => ({
    useBioXpStatus: () => state.status,
    useBioXpCommand: () => state.commandHookCalls++ % 2 === 0 ? state.execute : state.stop,
    useBioXpEmergencyStop: () => state.emergency,
    useConnectBioXp: () => state.connect,
    useDisconnectBioXp: () => state.disconnect,
    bioXpErrorText: (error: unknown) => error instanceof Error ? error.message : String(error),
}));

vi.mock('../../src/components/BioXpCameraPanel', () => ({
    BioXpCameraPanel: () => <div data-testid="camera-panel">Camera</div>,
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
    state.commandHookCalls = 0;
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

        expect(button('Initialize Controllers')?.disabled).toBe(true);
        expect(button('Move +')?.disabled).toBe(true);
        expect(buttons('Stop').every((entry) => entry.disabled)).toBe(true);
        expect(button('Emergency Stop')?.disabled).toBe(true);
        expect(container.textContent).toContain('BioXP status unavailable.');
    });
});
