// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ProteinDesignRun } from '../../src/components/ProteinDesignWorkflow';

let root: Root; let container: HTMLDivElement;
beforeEach(() => { container = document.createElement('div'); document.body.append(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

const mount = (props: Partial<React.ComponentProps<typeof ProteinDesignRun>> = {}) => act(async () => root.render(
    <ProteinDesignRun jobName="My design" onJobNameChange={() => {}} pending={false} onSubmit={() => {}} submitLabel="Generate candidates" {...props}><p>scope</p></ProteinDesignRun>));
const buttons = () => [...container.querySelectorAll('button')].filter(b => b.textContent === 'Generate candidates');

it('keeps the single Run action inside the Run section by default', async () => {
    const onSubmit = vi.fn();
    await mount({ onSubmit });
    expect(buttons()).toHaveLength(1);
    expect(container.querySelector('[data-bms-run-action]')).toBeNull();
    expect(container.querySelector('section[aria-label="Run"]')!.contains(buttons()[0])).toBe(true);
    await act(async () => buttons()[0].click());
    expect(onSubmit).toHaveBeenCalledTimes(1);
});

it('offers one sticky Run action beside the form, with the job name, and honors pending and disabled', async () => {
    const onSubmit = vi.fn(); const onCancel = vi.fn();
    await mount({ stickyAction: true, onSubmit, onCancel });
    expect(buttons()).toHaveLength(1);                       // never a second Run button
    const bar = container.querySelector<HTMLElement>('[data-bms-run-action="sticky"]')!;
    expect(bar.contains(buttons()[0])).toBe(true);
    expect(bar.className).toContain('sticky');
    expect(bar.className).toContain('bottom-0');
    expect(container.querySelector('section[aria-label="Run"]')!.contains(bar)).toBe(false);   // sticky needs the form as its parent
    expect(bar.textContent).toContain('My design');
    expect([...bar.querySelectorAll('button')].map(b => b.textContent)).toEqual(['Cancel', 'Generate candidates']);
    await act(async () => buttons()[0].click());
    expect(onSubmit).toHaveBeenCalledTimes(1);
    await mount({ stickyAction: true, pending: true });
    expect(bar.isConnected ? container.querySelector('[data-bms-run-action] button:last-child')!.textContent : '').toBe('Submitting…');
    await mount({ stickyAction: true, disabled: true });
    expect(container.querySelector<HTMLButtonElement>('[data-bms-run-action] button')!.disabled).toBe(true);
});

it('shows a placeholder name rather than an empty bar label', async () => {
    await mount({ stickyAction: true, jobName: '' });
    expect(container.querySelector('[data-bms-run-action]')!.textContent).toContain('Unnamed job');
});
