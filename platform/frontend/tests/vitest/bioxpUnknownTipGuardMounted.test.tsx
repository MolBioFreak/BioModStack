import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ runs: 0, compiled: [] as unknown[] }));
vi.mock('../../src/lib/bioxpImmediateRun', () => ({
    compileImmediate: async (name: string, steps: unknown[]) => { state.compiled.push({ name, steps }); return {}; },
    useBioXpImmediateRun: () => ({ enabled: true, pending: false, error: null, job: null,
        run: async (build: () => Promise<unknown>) => { state.runs += 1; await build(); } }),
}));
import { BioXpUnknownTipGuard, unknownTipsLoaded } from '../../src/components/BioXpUnknownTipGuard';

const pipettes = (tip_type: number, loaded: boolean) => ({ tip_type, channels: [0, 1, 2, 3].map(channel => ({ channel, software_tip_loaded: loaded })) }) as any;
let root: Root; let container: HTMLDivElement;
beforeEach(() => { state.runs = 0; state.compiled = []; localStorage.clear(); container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container); });
afterEach(() => { act(() => root.unmount()); container.remove(); });

describe('unknown tip guard', () => {
    it('detects only loaded tips with an unknown type', () => {
        expect(unknownTipsLoaded(pipettes(201, true))).toBe(true);
        expect(unknownTipsLoaded(pipettes(201, false))).toBe(false);
        expect(unknownTipsLoaded(pipettes(10, true))).toBe(false);
    });
    it('offers Eject to waste, which moves to the bin then ejects all channels', async () => {
        await act(async () => root.render(<BioXpUnknownTipGuard generation={3} connected pipettes={pipettes(201, true)} />));
        expect(container.textContent).toContain('type is unknown');
        const button = [...container.querySelectorAll('button')].find(b => b.textContent === 'Eject to waste')!;
        await act(async () => button.click());
        expect(state.runs).toBe(1);
        expect(JSON.stringify(state.compiled[0])).toContain('"action":"tip_eject"');
    });
    it('auto mode ejects once only when the option is on', async () => {
        await act(async () => root.render(<BioXpUnknownTipGuard mode="auto" generation={3} connected pipettes={pipettes(201, true)} />));
        expect(state.runs).toBe(0);
        localStorage.setItem('bioxp.unknownTips.autoEject', 'on');
        await act(async () => { window.dispatchEvent(new Event('bioxp-auto-eject')); });
        await act(async () => root.render(<BioXpUnknownTipGuard mode="auto" generation={3} connected pipettes={pipettes(201, true)} />));
        expect(state.runs).toBe(1);
    });
});
