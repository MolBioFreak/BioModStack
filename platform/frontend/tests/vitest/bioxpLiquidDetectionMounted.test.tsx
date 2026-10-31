import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { BioXpLiquidDetection } from '../../src/components/BioXpLiquidDetection';

let host: HTMLDivElement, root: Root, last: unknown;
async function render(value: unknown) {
    await act(async () => root.render(<BioXpLiquidDetection value={value} onChange={v => { last = v; }} />));
}
const select = (label: string) => host.querySelector(`select[aria-label="${label}"]`) as HTMLSelectElement;
function change(el: HTMLInputElement | HTMLSelectElement, value: string) {
    const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
}
beforeEach(() => { host = document.createElement('div'); document.body.append(host); root = createRoot(host); last = 'unset'; });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

it('defaults new transfers to first-draw, by-level detection and edits options', async () => {
    await render({});
    expect(select('Detect liquid').value).toBe('first_draw');
    expect(select('Descent mode').value).toBe('by_level');
    await act(async () => change(select('Descent mode'), 'one_at_a_time'));
    expect(last).toEqual({ mode: 'one_at_a_time' });
    await act(async () => change(host.querySelector('input[aria-label="Depth below surface (mm)"]') as HTMLInputElement, '2'));
    expect(last).toEqual({ submerge_mm: 2 });
});

it('shows saved transfers without the setting as off', async () => {
    await render(undefined);
    expect(select('Detect liquid').value).toBe('off');
    expect(select('Descent mode').disabled).toBe(true);
});
