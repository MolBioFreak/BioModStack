import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { BioXpMovePreparation, useBioXpMovePreparation, type PrepStep } from '../../src/components/BioXpMovePreparation';


type World = { enabled: boolean; zHomed: boolean; xyHomed: boolean };
const calls: string[] = [];
let setWorld: (w: World) => void = () => {};
let request: (() => void) | null = null;

function Harness({ initial }: { initial: World }) {
    const [world, set] = useState(initial);
    setWorld = set;
    const plan = (): PrepStep[] => [
        { id: 'enable', label: 'Enable controllers', done: () => world.enabled, submit: () => calls.push('enable'), blocked: () => null },
        { id: 'home_z', label: 'Home Z (raise the head)', done: () => world.zHomed, submit: () => calls.push('home_z'), blocked: () => (world.enabled ? null : 'motion off') },
        { id: 'home_xy', label: 'Home X and Y', done: () => world.xyHomed, submit: () => calls.push('home_xy'), blocked: () => null },
    ];
    const prep = useBioXpMovePreparation(plan, world);
    request = () => prep.request({ label: 'Reagent chiller', run: () => calls.push('move'), ready: () => null });
    return <BioXpMovePreparation state={prep} />;
}

describe('live deck move preparation', () => {
    let root: Root; let container: HTMLDivElement;
    beforeEach(() => { calls.length = 0; container = document.createElement('div'); document.body.append(container); root = createRoot(container); });
    afterEach(() => { act(() => root.unmount()); container.remove(); });
    const click = (text: string) => act(() => { [...container.querySelectorAll('button')].find(b => b.textContent === text)!.click(); });

    it('lists outstanding items and runs nothing before Proceed', () => {
        act(() => root.render(<Harness initial={{ enabled: false, zHomed: false, xyHomed: true }} />));
        act(() => request!());
        expect(container.textContent).toContain('Enable controllers');
        expect(container.textContent).toContain('Home Z');
        expect(container.textContent).not.toContain('Home X and Y');
        expect(calls).toEqual([]);
        click('Cancel');
        expect(calls).toEqual([]);
        expect(container.textContent).toBe('');
    });

    it('runs each step after the robot reports the previous done, then the move', () => {
        act(() => root.render(<Harness initial={{ enabled: false, zHomed: false, xyHomed: true }} />));
        act(() => request!());
        click('Proceed');
        expect(calls).toEqual(['enable']);
        act(() => setWorld({ enabled: true, zHomed: false, xyHomed: true }));
        expect(calls).toEqual(['enable', 'home_z']);
        act(() => setWorld({ enabled: true, zHomed: true, xyHomed: true }));
        expect(calls).toEqual(['enable', 'home_z', 'move']);
    });

    it('moves directly when nothing is outstanding', () => {
        act(() => root.render(<Harness initial={{ enabled: true, zHomed: true, xyHomed: true }} />));
        act(() => request!());
        expect(calls).toEqual(['move']);
        expect(container.textContent).toBe('');
    });
});
