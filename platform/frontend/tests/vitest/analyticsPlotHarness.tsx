import React, { act } from 'react';
import { beforeEach, expect, vi } from 'vitest';

// Only graphics and browser layout are doubled; real chart owners build traces.
beforeEach(() => vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} }));
if (!window.matchMedia) Object.defineProperty(window, 'matchMedia', {
    configurable: true, value: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
});
export default function PlotSnapshot(props: any) {
    return <div data-plot={JSON.stringify({ data: props.data, layout: props.layout, config: props.config })}
        onClick={() => props.onClick?.({ points: [{ customdata: props.data[0]?.customdata?.[0] }] })} />;
}
export const plots = (host: Element) => [...host.querySelectorAll('[data-plot]')].map(node => JSON.parse(node.getAttribute('data-plot')!));
export const scatter = (host: Element) => plots(host).find(plot => plot.data[0]?.type === 'scatter' && plot.data[0]?.mode === 'markers');
export async function change(host: Element, label: string, value: string) {
    const select = host.querySelector(`select[aria-label="${label}"]`) as HTMLSelectElement;
    expect(select, label).not.toBeNull();
    await act(async () => { select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })); });
}
export async function settled(host: Element) {
    for (let i = 0; i < 50 && host.querySelector('[role="status"]')?.textContent?.startsWith('Loading'); i++) {
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
    }
    expect(host.textContent).not.toContain('Loading scientific metric authority');
    expect(host.querySelector('[role="alert"]')).toBeNull();
}
