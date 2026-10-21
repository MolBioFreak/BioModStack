import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { DigestPanel } from '../../src/components/MolBioToolkit/panels/DigestPanel';
import { buildRestrictionMapAnnotations } from '../../src/components/MolBioToolkit/SequenceViewer';
import type { SequenceData } from '../../src/components/MolBioToolkit/types';
import type { RestrictionAnalysisBatch, RestrictionCatalogReceipt, RestrictionCatalogSummary, RestrictionOccurrence } from '../../src/lib/restrictionAnalysis';

// Canonical IDs, deliberately synthetic counts/motifs: these exercise UI policy,
// not scientific recognition or enzyme capabilities.
const special = ['AbaSI', 'MspJI'];
const ordinary = ['EcoRI', 'BamHI', 'HindIII', 'Nt.BbvCI', 'BsaI'];
const sequence: SequenceData = { name: 'special-case fixture', sequence: 'ACGTACGTACGT', sequenceType: 'dna', circular: true, features: [] };
const records = [...ordinary, ...special].map(enzyme_id => ({
    enzyme_id, canonical_name: enzyme_id, aliases: [], recognition_site: 'ACGT',
    recognition_sites: ['ACGT'], recognition: { site_iupac: 'ACGT' },
    analysis_capability: enzyme_id === 'Nt.BbvCI' ? 'nicking_analysis' : 'digest_simulation',
    // Special records intentionally look compatible, to catch category-only exclusion.
    golden_gate_compatible: enzyme_id === 'BsaI' || special.includes(enzyme_id),
})) as unknown as RestrictionCatalogSummary[];
const catalog = { catalog_id: 'special-case-fixture', catalog_sha256: 'a'.repeat(64), counts: { total: records.length } } as RestrictionCatalogReceipt;
function batch(specialDsb = 1): RestrictionAnalysisBatch {
    const summaries = records.map((record, index) => ({
        enzyme_id: record.enzyme_id, canonical_name: record.canonical_name,
        recognition_site_count_definite: special.includes(record.enzyme_id) ? 11 : [1, 2, 3, 2, 1][index],
        recognition_site_count_possible: special.includes(record.enzyme_id) ? 7 : 0,
        double_strand_break_count: special.includes(record.enzyme_id) ? specialDsb : [1, 2, 3, 0, 1][index],
        nick_count: special.includes(record.enzyme_id) ? 13 : record.enzyme_id === 'Nt.BbvCI' ? 2 : 0,
    }));
    const counts = summaries.reduce((total, row) => ({
        recognition_site_count_definite: total.recognition_site_count_definite + row.recognition_site_count_definite,
        recognition_site_count_possible: total.recognition_site_count_possible + row.recognition_site_count_possible,
        double_strand_break_count: total.double_strand_break_count + row.double_strand_break_count,
        nick_count: total.nick_count + row.nick_count,
    }), { recognition_site_count_definite: 0, recognition_site_count_possible: 0, double_strand_break_count: 0, nick_count: 0 });
    return {
        source: { content_sha256: 'b'.repeat(64), topology: 'circular', content_length: sequence.sequence.length },
        catalog, chunks: [], complete: true, requested_enzyme_count: records.length,
        analysis: { counts, enzyme_summaries: summaries, occurrences: [], grouped_cleavages: [], warnings: [], limitations: [] },
    } as unknown as RestrictionAnalysisBatch;
}
const noop = () => undefined;
function expectModifiedSubstrate(text: string) {
    // Accept the actual chemical modification names, not only the generic word.
    expect(text).toMatch(/requires[^.]*\b(?:modified|methylated|hydroxymethylated)\b[^.]*cytosines/i);
    expect(text).toMatch(/no cleavage on unmodified DNA/i);
    expect(text).toMatch(/sequence motifs only|motif[- ]only/i);
}

describe('AbaSI/MspJI explicit special-case mapping', () => {
    let root: Root | undefined;
    let host: HTMLDivElement;
    let mapped: string[];
    afterEach(async () => { if (root) await act(async () => root?.unmount()); root = undefined; host?.remove(); });

    async function mount({ compactLandscape = false, specialDsb = 1 } = {}) {
        host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
        mapped = [];
        const analysis = batch(specialDsb);
        function Harness() {
            const [ids, setIds] = useState<string[]>([]);
            return <DigestPanel mobile compactLandscape={compactLandscape} sequenceData={sequence} sequenceId="special-fixture"
                onHighlight={noop} selectedEnzymes={ids} onEnzymesChange={next => { mapped = next; setIds(next); }}
                catalog={catalog} catalogRecords={records} analysis={analysis} authorityLoading={false} authorityError={null}
                digestSimulation={null} digestLoading={false} digestError={null} onDigestSelectionChange={noop}
                onSimulateDigest={noop} onAnalyzeAll={async () => analysis} />;
        }
        await act(async () => root?.render(<Harness />));
    }
    function button(label: string, within: ParentNode = host) {
        const target = [...within.querySelectorAll<HTMLButtonElement>('button')].find(element => element.textContent === label);
        expect(target, `button ${label}`).toBeTruthy();
        return target!;
    }
    async function click(label: string, within: ParentNode = host) {
        const target = button(label, within); expect(target.disabled).toBe(false);
        await act(async () => target.click());
    }
    function visibleIds() { return [...host.querySelectorAll('[data-enzyme-name]')].map(row => row.getAttribute('data-enzyme-name')); }
    async function search(value: string) {
        const input = host.querySelector<HTMLInputElement>('input[placeholder="Search enzyme or recognition site…"]')!;
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
            input.dispatchEvent(new Event('input', { bubbles: true }));
        });
    }

    it('excludes exactly the special pair from the default list and ordinary search', async () => {
        await mount(); expect(visibleIds()).toEqual(ordinary);
        await search('EcoRI'); expect(visibleIds()).toEqual(['EcoRI']);
        for (const id of special) { await search(id); expect(visibleIds()).toEqual([]); }
        await search(''); expect(visibleIds()).toEqual(ordinary);
    });

    it.each([false, true])('toggles an accessible Special cases button in compactLandscape=%s and respects search', async compactLandscape => {
        await mount({ compactLandscape });
        expect(button('Special cases').getAttribute('aria-pressed')).toBe('false');
        await click('Special cases');
        expect(button('Special cases').getAttribute('aria-pressed')).toBe('true');
        expect(visibleIds()).toEqual(special);
        await search('AbaSI'); expect(visibleIds()).toEqual(['AbaSI']);
        await click('Special cases'); expect(visibleIds()).toEqual([]);
        expect(button('Special cases').getAttribute('aria-pressed')).toBe('false');
        await search(''); expect(visibleIds()).toEqual(ordinary);
    });

    it('resets the old cut-count filter to All on entry and return', async () => {
        await mount(); await click('2x'); expect(visibleIds()).toEqual(['BamHI']);
        await click('Special cases'); expect(visibleIds()).toEqual(special);
        await click('Special cases'); expect(visibleIds()).toEqual(ordinary);
    });

    it('labels special rows as modification-dependent motifs, without DSB counts, and permits manual Map/Unmap', async () => {
        await mount(); await click('Special cases');
        for (const id of special) {
            const row = host.querySelector(`[data-enzyme-name="${id}"]`)!;
            expectModifiedSubstrate(row.textContent ?? '');
            expect(row.textContent).not.toMatch(/\b\d+\s+(?:predicted\s+)?DSBs?\b/i);
            await click('Map', row); expect(mapped).toEqual([id]);
            await click('Unmap', row); expect(mapped).toEqual([]);
        }
    });

    it.each([
        { label: 'Map all 1x', specialDsb: 1, expected: ['EcoRI', 'BsaI'] },
        { label: 'Map all 2x', specialDsb: 2, expected: ['BamHI'] },
        { label: 'Map all 3x+', specialDsb: 3, expected: ['HindIII'] },
        { label: 'Map nicking', specialDsb: 1, expected: ['Nt.BbvCI'] },
        { label: 'Map Golden Gate', specialDsb: 1, expected: ['BsaI'] },
    ])('$label excludes both special IDs even when native summaries fit the category', async ({ label, specialDsb, expected }) => {
        await mount({ specialDsb }); await click(label); expect(mapped).toEqual(expected);
        await click(label); expect(mapped).toEqual([]);
        // Special-case browsing offers motif mapping, not ordinary cutter controls.
        await click('Special cases');
        expect([...host.querySelectorAll('button')].some(button => button.textContent === label)).toBe(false);
        expect(mapped).toEqual([]);
    });

    it('bulk maps only ordinary enzymes by default and includes special motifs only on explicit special-view mapping', async () => {
        await mount(); await click(`Map filtered (${ordinary.length})`); expect(mapped).toEqual(ordinary);
        await click(`Clear map (${ordinary.length})`); expect(mapped).toEqual([]);
        await click('Special cases'); await click('Map filtered (2)'); expect(mapped).toEqual(special);
    });

    it('preserves manually mapped special enzymes when a general quick group is toggled on and off', async () => {
        await mount(); await click('Special cases');
        for (const id of special) await click('Map', host.querySelector(`[data-enzyme-name="${id}"]`)!);
        await click('Special cases'); await click('Map all 1x');
        expect(mapped).toEqual([...special, 'EcoRI', 'BsaI']);
        await click('Map all 1x'); expect(mapped).toEqual(special);
    });

    it('excludes special native totals from general summary counts and does not advertise special DSB counts', async () => {
        await mount();
        const counts = () => host.querySelector('[data-restriction-counts]')!;
        expect([...counts().querySelectorAll('strong')].map(node => node.textContent)).toEqual(['9', '0', '7', '2']);
        expect(counts().textContent).toContain('across 5 analyzed enzymes');
        await click('Special cases');
        expect(counts().textContent).toMatch(/motif/i);
        expect(counts().textContent).not.toMatch(/\b\d+\s+(?:predicted\s+)?DSBs?\b/i);
    });
});

describe('native special-case restriction annotations', () => {
    it('retains native intervals, segment IDs and directions with short names and modification-dependent motif descriptions', () => {
        const occurrences = [...special, 'EcoRI'].map(enzyme_id => ({
            occurrence_id: `fixture:${enzyme_id}`, enzyme_id, canonical_name: enzyme_id,
            orientation: 'reverse', certainty: 'definite', site_start: 10, site_end_unwrapped: 14,
            site_segments: [[10, 12], [0, 2]], wraps_origin: true,
            double_strand_events: [{ status: 'complete', top_boundary: 11, bottom_boundary: 1 }], nicks: [], limitations: [],
        })) as unknown as RestrictionOccurrence[];
        const before = JSON.stringify(occurrences);
        const annotations = buildRestrictionMapAnnotations({ occurrences, selectedEnzymes: [...special, 'EcoRI'], sequenceLength: 12, sourceDisplayStrand: 'plus', resolvedDisplayStrand: 'plus' });
        expect(annotations.map(({ id, name, start, end, direction }) => ({ id, name, start, end, direction }))).toEqual(
            occurrences.flatMap(occurrence => occurrence.site_segments.map(([start, end], index) => ({
                id: `${occurrence.occurrence_id}:segment:${index}`, name: occurrence.canonical_name, start, end, direction: -1,
            }))),
        );
        for (const annotation of annotations.filter(row => special.includes(row.name))) {
            expectModifiedSubstrate(annotation.description);
            expect(annotation.description).toMatch(/motif match/i);
            expect(annotation.description).not.toContain('definite DSB');
        }
        expect(annotations.filter(row => row.name === 'EcoRI').every(row => row.description.includes('definite DSB'))).toBe(true);
        expect(JSON.stringify(occurrences)).toBe(before);
    });
});
