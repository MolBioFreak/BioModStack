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

        expect(cards).toHaveLength(6);
        expect(byKey('duplex')).toBeUndefined();
        expect(byKey('barcode')).toBeUndefined();
        expect(byKey('bamQc')).toBeUndefined();
        expect(byKey('modified')?.textContent).toContain('Methylation analysis');
        expect(byKey('dna')?.textContent).toContain('optional paired strands, barcodes and modification calls');
        expect(byKey('dna')?.getAttribute('aria-pressed')).toBe('true');
        expect(cards.filter((card) => card.getAttribute('aria-pressed') === 'true')).toHaveLength(1);
        expect(byKey('clone')?.getAttribute('aria-pressed')).toBe('false');
        expect(byKey('pooledAssignment')?.getAttribute('aria-pressed')).toBe('false');
        expect(byKey('clone')?.className).toContain('bg-[var(--bg-primary)]');
        expect(byKey('pooledAssignment')?.className).toContain('bg-[var(--bg-primary)]');
        expect(byKey('clone')?.className).not.toContain('ring-1');
        expect(byKey('pooledAssignment')?.className).not.toContain('ring-1');
        expect(byKey('clone')?.textContent).toContain('Plasmid analysis');
        expect(byKey('plasmidQc')).toBeUndefined();
        expect(byKey('constructScreening')).toBeUndefined();
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

    it.each([['duplex', 'dna'], ['barcode', 'dna'], ['bamQc', 'plasmidQc']])('reopens legacy %s under its existing workflow', async (saved, current) => {
        await renderChooser(saved as WorkflowKey);
        const selected = container.querySelectorAll('[aria-pressed="true"]');
        expect(selected).toHaveLength(1);
        expect((selected[0] as HTMLElement).dataset.ngsWorkflowKey).toBe(current === 'plasmidQc' ? 'clone' : current);
        if (current === 'plasmidQc') expect(container.querySelector<HTMLSelectElement>('select')?.value).toBe('plasmidQc');
    });

    it.each(['clone', 'plasmidQc', 'constructScreening'] as const)('reopens and edits the exact %s method within one plasmid task', async (workflow) => {
        const onSelect = await renderChooser(workflow);
        const select = container.querySelector<HTMLSelectElement>('select[aria-label="Plasmid analysis method"]')!;
        expect(select.value).toBe(workflow);
        expect(Array.from(select.options).map(option => option.value)).toEqual(['clone', 'plasmidQc', 'constructScreening']);
        const next = workflow === 'clone' ? 'plasmidQc' : 'clone';
        await act(async () => { select.value = next; select.dispatchEvent(new Event('change', { bubbles: true })); });
        expect(onSelect).toHaveBeenCalledWith(next);
    });

    it('does not reset an existing method when its selected task is clicked again', async () => {
        const onSelect = await renderChooser('constructScreening');
        await act(async () => container.querySelector<HTMLButtonElement>('[data-ngs-workflow-key="clone"]')!.click());
        expect(onSelect).not.toHaveBeenCalled();
    });

});
