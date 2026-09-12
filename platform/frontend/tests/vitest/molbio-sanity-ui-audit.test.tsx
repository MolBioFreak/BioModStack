import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ExportDropdown } from '../../src/components/MolBioToolkit/ExportDropdown';
import { SequenceHeader } from '../../src/components/MolBioToolkit/SequenceHeader';
import { FeaturePanel } from '../../src/components/MolBioToolkit/panels/FeaturePanel';

const sequence = {
    name: 'PL2190', sequence: 'ACGT'.repeat(25), sequenceType: 'dna' as const, circular: true,
    features: Array.from({ length: 7 }, (_, i) => ({
        id: `f${i}`, name: `Existing feature ${i}`, type: 'CDS', start: i * 10, end: i * 10 + 8,
        strand: 1 as const, color: '#22c55e', qualifiers: { gene: `gene_${i}` },
    })),
};
const noop = () => {};
let host: HTMLDivElement;
let root: Root;
beforeEach(() => { host = document.createElement('div'); document.body.append(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); });
async function render(node: React.ReactNode) { await act(async () => root.render(node)); }
async function click(button: HTMLElement) { await act(async () => button.click()); }

it('exports all six formats through a viewport-bounded portal, with keyboard dismissal and focus return', async () => {
    host.style.overflow = 'hidden';
    await render(<ExportDropdown sequenceData={sequence} />);
    const trigger = host.querySelector('button')!;
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({ right: 2000, bottom: 2000 } as DOMRect);
    await click(trigger);
    const popup = document.querySelector('[aria-label="Export formats"]')!;
    expect(host.contains(popup)).toBe(false);
    expect(popup.parentElement).toBe(document.body);
    expect(popup.querySelectorAll('button')).toHaveLength(6);
    expect(popup.classList.contains('fixed')).toBe(true);
    expect(Number.parseFloat((popup as HTMLElement).style.left)).toBeLessThan(window.innerWidth);
    expect(Number.parseFloat((popup as HTMLElement).style.top)).toBeLessThan(window.innerHeight);
    expect(document.activeElement).toBe(popup.querySelector('button'));
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(document.querySelector('[aria-label="Export formats"]')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    await click(trigger);
    await act(async () => document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })));
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
});

it('portal option clicks download the existing FASTA format and close without outside-click interception', async () => {
    const createUrl = vi.fn(() => 'blob:export');
    const revokeUrl = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createUrl });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeUrl });
    const download = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    await render(<ExportDropdown sequenceData={sequence} />);
    const trigger = host.querySelector('button')!;
    await click(trigger);
    const fasta = [...document.querySelectorAll('[aria-label="Export formats"] button')].find(b => b.textContent?.includes('FASTA')) as HTMLButtonElement;
    await act(async () => { fasta.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); fasta.click(); });
    expect(createUrl).toHaveBeenCalledOnce();
    expect(download).toHaveBeenCalledOnce();
    expect(revokeUrl).toHaveBeenCalledWith('blob:export');
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(trigger);
});

it('common toolbar controls remain outside the collapsed secondary options and retain callbacks', async () => {
    const save = vi.fn(); const projection = vi.fn(); const shelf = vi.fn();
    await render(<SequenceHeader sequenceData={sequence} onSave={save} isDirty onViewModeChange={projection}
        onToggleLibraryPanel={shelf} onToggleToolPanel={noop} onAutoAnnotate={noop}
        onDisplayStrandChange={noop} onGCTrackToggle={noop} onOpenLibrary={noop} />);
    const common = host.querySelector('[data-sequence-primary-actions]')!;
    for (const label of ['Save', 'Export', 'Acquire', 'Linear', 'Both', 'Circular', 'Hide Shelf', 'Hide Tools']) {
        expect(common.textContent).toContain(label);
    }
    expect(common.classList.contains('flex-wrap')).toBe(true);
    const options = host.querySelector('details')!;
    expect(options.open).toBe(false);
    expect(options.textContent).toContain('Auto-Annotate');
    const button = (label: string) => [...common.querySelectorAll('button')].find(b => b.textContent?.trim() === label)!;
    await click(button('Save')); await click(button('Linear')); await click(button('Hide Shelf'));
    expect(save).toHaveBeenCalledOnce(); expect(projection).toHaveBeenCalledWith('linear'); expect(shelf).toHaveBeenCalledOnce();
});

it('features open for inspection, use one scroll owner, and provide named selectable color targets in add and edit', async () => {
    const jump = vi.fn();
    await render(<FeaturePanel sequenceData={sequence} selection={{ start: 10, end: 20 }}
        onHighlight={noop} onAddFeature={noop} onRemoveFeature={noop} onUpdateFeature={noop} onJumpToPosition={jump} />);
    const add = host.querySelector('details')!;
    expect(add.open).toBe(false);
    for (const feature of sequence.features) expect(host.textContent).toContain(feature.name);
    expect(host.querySelector('[class*="overflow-y-auto"]')).toBeNull();
    const existing = [...host.querySelectorAll('button')].find(b => b.textContent?.includes('Existing feature 0'))!;
    await click(existing); expect(jump).toHaveBeenCalledWith(0);
    await click([...host.querySelectorAll('button')].find(b => b.textContent === 'Use Range')!);
    expect(add.open).toBe(true);
    const colors = add.querySelectorAll<HTMLButtonElement>('button[aria-label^="Feature color:"]');
    expect(colors).toHaveLength(8);
    expect(new Set([...colors].map(b => b.getAttribute('aria-label'))).size).toBe(8);
    expect([...colors].every(b => b.classList.contains('h-7') && b.classList.contains('w-7'))).toBe(true);
    await click(colors[0]); expect(colors[0].getAttribute('aria-pressed')).toBe('true');
    await click(host.querySelector<HTMLButtonElement>('[title="Edit feature"]')!);
    const editColors = [...host.querySelectorAll<HTMLButtonElement>('button[aria-label^="Feature color:"]')].filter(b => !add.contains(b));
    expect(editColors).toHaveLength(8);
    await click(editColors[1]); expect(editColors[1].getAttribute('aria-pressed')).toBe('true');
});
