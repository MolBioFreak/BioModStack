import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {expect, it, vi} from 'vitest';
import type {RestrictionAnalysisBatch, RestrictionOccurrence} from '../../src/lib/restrictionAnalysis';
import './setup';
vi.mock('seqviz', async original => {
    const actual = await original<typeof import('seqviz')>();
    return {...actual, SeqViz: (props: React.ComponentProps<typeof actual.SeqViz>) =>
        <actual.SeqViz {...props} disableExternalFonts testSize={{width: 1000, height: 700}} />};
});
import {SequenceViewer} from '../../src/components/MolBioToolkit/SequenceViewer';

const occurrences = ['Exact', 'Broad'].map((name, i) => ({
    occurrence_id: `occ:${name}`, occurrence_ordinal: 0, enzyme_id: name, canonical_name: name,
    recognition_pattern: i ? 'RCGTAY' : 'ACGTAC', orientation: 'forward', certainty: 'definite',
    site_start: 20, site_end_unwrapped: 26, site_segments: [[20, 26]], wraps_origin: false,
    double_strand_events: [{status: 'complete', top_boundary: 22, bottom_boundary: 26,
        top_boundary_unwrapped: 22, bottom_boundary_unwrapped: 26, overhang_kind: 'five_prime', overhang_length_nt: 4}],
    nicks: [], limitations: [],
})) as unknown as RestrictionOccurrence[];
const batch = {analysis: {occurrences}} as RestrictionAnalysisBatch;

it.each(['linear', 'circular', 'both'] as const)('opens grouped enzyme details from a native %s annotation click', async viewMode => {
    const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
    const onSelection = vi.fn();
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    const render = (analysis = batch, grouped = true) => root.render(<SequenceViewer
        sequenceData={{name: 'Neutral UI fixture', sequence: 'ACGT'.repeat(100), circular: true, sequenceType: 'dna', features: []}}
        visibility={{features: false, primers: false, cutsites: true, translations: false, reverseComplement: true}}
        restrictionAnalysis={analysis} selectedEnzymes={['Exact', 'Broad']} viewMode={viewMode}
        groupSameSiteLabels={grouped} onSelection={onSelection} />);
    const clickLabel = async () => {
        const annotation = host.querySelector(viewMode === 'linear' ? '.la-vz-annotation-label' : '.la-vz-circular-label')!;
        expect(annotation).not.toBeNull();
        await act(() => { for (const type of ['mousedown', 'mouseup', 'click']) annotation.dispatchEvent(new MouseEvent(type, {bubbles: true, button: 0})); });
        // A narrow circular pane may first disclose its packed annotation labels.
        const names = host.querySelector('[role="dialog"][aria-label="Annotation names"]');
        const named = [...(names?.querySelectorAll<HTMLButtonElement>('button') ?? [])].find(button => button.textContent === 'Exact +1');
        if (named && !host.querySelector('[data-restriction-label-details]')) await act(() => named.click());
        await act(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
    };
    try {
        await act(() => render());
        await clickLabel();
        const dialog = host.querySelector('[role="dialog"][data-restriction-label-details]')!;
        expect(dialog).not.toBeNull();
        expect([...dialog.querySelectorAll('dt')].map(node => node.textContent)).toEqual(['Exact', 'Broad']);
        expect([...dialog.querySelectorAll('dd')].map(node => node.textContent)).toEqual(['ACGTAC', 'RCGTAY']);
        expect(onSelection).toHaveBeenCalled();
        await act(() => dialog.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})));
        expect(host.querySelector('[data-restriction-label-details]')).toBeNull();
        await clickLabel();
        await act(() => host.querySelector<HTMLButtonElement>('[aria-label="Close enzyme details"]')!.click());
        expect(host.querySelector('[data-restriction-label-details]')).toBeNull();
        await clickLabel();
        await act(() => render({...batch}));
        expect(host.querySelector('[data-restriction-label-details]')).toBeNull();
        await act(() => render(batch, false));
        await clickLabel();
        expect(host.querySelector('[data-restriction-label-details]')).toBeNull();
    } finally { await act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); }
});
