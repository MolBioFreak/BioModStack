import React, { act, useCallback, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DigestPanel } from '../../src/components/MolBioToolkit/panels/DigestPanel';
import type { SequenceData } from '../../src/components/MolBioToolkit/types';
import type { RestrictionAnalysisBatch, RestrictionCatalogReceipt, RestrictionCatalogSummary } from '../../src/lib/restrictionAnalysis';

// Deliberately small UI fixtures; scientific group coverage is separately exercised
// against the real PL2190 source and native catalog in Android.
const sequence: SequenceData = { name: 'mapping fixture', sequence: 'ACGTACGT', sequenceType: 'dna', circular: true, features: [] };
const records = ['Unique', 'Double', 'Triple', 'Nick', 'Golden', 'Zero'].map(enzyme_id => ({
    enzyme_id, canonical_name: enzyme_id, aliases: [], recognition_site: 'ACGT',
    recognition_sites: ['ACGT'], recognition: { site_iupac: 'ACGT' },
    analysis_capability: enzyme_id === 'Nick' ? 'nicking_analysis' : 'digest_simulation',
    golden_gate_compatible: enzyme_id === 'Golden',
})) as unknown as RestrictionCatalogSummary[];
const catalog = { catalog_id: 'mapping-fixture', catalog_sha256: 'a'.repeat(64), counts: { total: records.length } } as RestrictionCatalogReceipt;
const full = {
    source: { content_sha256: 'b'.repeat(64), topology: 'circular', content_length: 8 }, catalog, chunks: [], complete: true, requested_enzyme_count: records.length,
    analysis: {
        counts: { recognition_site_count_definite: 9, recognition_site_count_possible: 0, double_strand_break_count: 7, nick_count: 2 },
        enzyme_summaries: records.map((record, i) => ({ enzyme_id: record.enzyme_id, canonical_name: record.canonical_name, recognition_site_count_definite: [1, 2, 3, 2, 1, 0][i], recognition_site_count_possible: 0, double_strand_break_count: [1, 2, 3, 0, 1, 0][i], nick_count: i === 3 ? 2 : 0 })),
        occurrences: [], grouped_cleavages: [], warnings: [], limitations: [],
    },
} as unknown as RestrictionAnalysisBatch;
const partial = { ...full, complete: true, requested_enzyme_count: 1, analysis: { ...full.analysis, enzyme_summaries: full.analysis.enzyme_summaries.slice(0, 1) } };
const noop = () => undefined;

describe('mapping groups from initially partial native analysis', () => {
    let root: Root | undefined;
    let host: HTMLDivElement;
    let mapped: string[];
    let finish: (batch: RestrictionAnalysisBatch | undefined) => void;
    let analyze: ReturnType<typeof vi.fn<() => Promise<RestrictionAnalysisBatch | undefined>>>;
    let rerender: (value: SequenceData) => Promise<void>;

    afterEach(async () => { if (root) await act(async () => root?.unmount()); root = undefined; host?.remove(); });

    async function mount(initialMapped: string[] = []) {
        host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
        mapped = initialMapped;
        const pending = new Promise<RestrictionAnalysisBatch | undefined>(resolve => { finish = resolve; });
        let publish: (batch: RestrictionAnalysisBatch) => void = noop;
        analyze = vi.fn(() => pending.then(batch => { if (batch) publish(batch); return batch; }));
        function Harness({ value }: { value: SequenceData }) {
            const [ids, setIds] = useState(initialMapped);
            const [batch, setBatch] = useState<RestrictionAnalysisBatch>(partial);
            publish = setBatch;
            const change = useCallback((next: string[]) => { mapped = next; setIds(next); }, []);
            return <DigestPanel mobile sequenceData={value} sequenceId="fixture" onHighlight={noop}
                selectedEnzymes={ids} onEnzymesChange={change} catalog={catalog} catalogRecords={records} analysis={batch}
                authorityLoading={false} authorityError={null} digestSimulation={null} digestLoading={false} digestError={null}
                onDigestSelectionChange={noop} onSimulateDigest={noop} onAnalyzeAll={analyze} />;
        }
        rerender = async value => { await act(async () => root?.render(<Harness value={value} />)); };
        await rerender(sequence);
    }
    function button(label: string) { return [...host.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === label)!; }
    async function click(label: string) { const target = button(label); expect(target, label).toBeTruthy(); await act(async () => target.click()); }

    it.each([
        ['Map all 1x', ['Unique', 'Golden']], ['Map all 2x', ['Double']], ['Map all 3x+', ['Triple']],
        ['Map nicking', ['Nick']], ['Map Golden Gate', ['Golden']],
    ])('%s completes the requested group rather than mapping just the analyzed seed', async (label, expected) => {
        await mount(); await click(label as string);
        expect(analyze).toHaveBeenCalledOnce(); expect(mapped).toEqual([]);
        expect(button(label as string).getAttribute('aria-busy')).toBe('true');
        await act(async () => finish(full));
        expect(mapped).toEqual(expected); expect(button(label as string).getAttribute('aria-pressed')).toBe('true');
        await click(label as string); expect(mapped).toEqual([]);
    });

    it('merges rapid group requests and preserves manual mappings when one group is removed', async () => {
        await mount(['Zero']);
        await click('Map all 1x'); await click('Map Golden Gate'); await click('Map nicking');
        await act(async () => finish(full));
        expect(new Set(mapped)).toEqual(new Set(['Zero', 'Unique', 'Golden', 'Nick']));
        await click('Map all 1x'); expect(new Set(mapped)).toEqual(new Set(['Zero', 'Golden', 'Nick']));
    });

    it.each(['Clear map (0)', 'Map all 2x'])('%s cancels a pending group without restoring it on late completion', async cancel => {
        await mount(); await click('Map all 2x');
        expect(button(cancel).disabled).toBe(false); await click(cancel);
        await act(async () => finish(full)); expect(mapped).toEqual([]);
    });

    it('changing construct cancels pending mapping intent', async () => {
        await mount(); await click('Map nicking');
        await rerender({ ...sequence, name: 'different construct', sequence: 'TTTT' });
        await act(async () => finish(full)); expect(mapped).toEqual([]);
    });

    it('count filters request missing analysis and filtered mapping uses the returned coverage', async () => {
        await mount(); await click('2x'); expect(analyze).toHaveBeenCalledOnce();
        await act(async () => finish(full));
        expect([...host.querySelectorAll('[data-enzyme-name]')].map(e => e.getAttribute('data-enzyme-name'))).toEqual(['Double']);
        await click('Map filtered (1)'); expect(mapped).toEqual(['Double']);
    });

    it('mapping the all filter requests full coverage and Clear wins over the pending result', async () => {
        await mount(); await click('Map filtered (1)'); expect(analyze).toHaveBeenCalledOnce();
        await click('Clear map (0)'); await act(async () => finish(full)); expect(mapped).toEqual([]);
    });

    it('failed analysis is not presented as an active, successfully mapped group', async () => {
        await mount(); await click('Map all 2x'); await act(async () => finish(undefined));
        expect(mapped).toEqual([]); expect(button('Map all 2x').getAttribute('aria-pressed')).toBe('false');
        expect(host.querySelector('[role="alert"]')?.textContent).toContain('did not finish');
    });
});
