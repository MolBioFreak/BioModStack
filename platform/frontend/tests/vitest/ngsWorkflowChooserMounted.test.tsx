import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NanoporeWorkflowChooser, type WorkflowKey } from '../../src/components/ngs/NanoporeWorkflowChooser';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => root.unmount());
    container.remove();
});

async function renderChooser(selectedWorkflow: WorkflowKey = 'dna', onSelect = vi.fn()) {
    await act(async () => {
        root.render(
            <NanoporeWorkflowChooser selectedWorkflow={selectedWorkflow} onSelect={onSelect} />,
        );
    });
    return onSelect;
}

describe('mounted NGS workflow chooser', () => {
    it('renders one selected card with neutral inactive special cards and semantic badges', async () => {
        await renderChooser();
        const cards = Array.from(container.querySelectorAll<HTMLButtonElement>('button[data-ngs-workflow-key]'));
        const byKey = (key: string) => cards.find((card) => card.dataset.ngsWorkflowKey === key);

        expect(cards).toHaveLength(11);
        expect(byKey('dna')?.getAttribute('aria-pressed')).toBe('true');
        expect(cards.filter((card) => card.getAttribute('aria-pressed') === 'true')).toHaveLength(1);
        expect(byKey('clone')?.getAttribute('aria-pressed')).toBe('false');
        expect(byKey('pooledAssignment')?.getAttribute('aria-pressed')).toBe('false');
        expect(byKey('clone')?.className).toContain('bg-[color-mix(in_srgb,var(--bg-secondary)_75%,#000)]');
        expect(byKey('pooledAssignment')?.className).toContain('bg-[color-mix(in_srgb,var(--bg-secondary)_75%,#000)]');
        expect(byKey('clone')?.className).not.toContain('ring-1');
        expect(byKey('pooledAssignment')?.className).not.toContain('ring-1');
        expect(container.textContent).toContain('VENDOR REPORT');
        expect(container.textContent).toContain('REVIEW ONLY');
    });

    it('passes the selected workflow through the mounted button interaction', async () => {
        const onSelect = await renderChooser('dna');
        const fastqQc = container.querySelector<HTMLButtonElement>('[data-ngs-workflow-key="fastqQc"]');
        expect(fastqQc).not.toBeNull();

        await act(async () => {
            fastqQc?.click();
        });

        expect(onSelect).toHaveBeenCalledTimes(1);
        expect(onSelect).toHaveBeenCalledWith('fastqQc');
    });

    it('disables nonessential card motion when reduced motion is requested', async () => {
        await renderChooser();
        const cards = Array.from(container.querySelectorAll<HTMLButtonElement>('button[data-ngs-workflow-key]'));

        expect(cards).not.toHaveLength(0);
        for (const card of cards) {
            expect(card.className).toContain('motion-reduce:transform-none');
            expect(card.className).toContain('motion-reduce:transition-none');
        }
    });
});
