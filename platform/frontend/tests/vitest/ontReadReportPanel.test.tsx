// @vitest-environment jsdom
import React from 'react';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const get = vi.fn();
vi.mock('react-plotly.js', () => ({ default: (props: { layout?: { shapes?: unknown[] } }) => <div data-testid="plot" data-shapes={props.layout?.shapes?.length ?? 0} /> }));
vi.mock('../../src/lib/api', () => ({ api: { get: (...args: unknown[]) => get(...args) } }));
vi.mock('../../src/lib/ngsAlignmentSession', () => ({
    withAlignmentAccessRecovery: (_jobId: string, operation: () => Promise<unknown>) => operation(),
    describeNgsError: (reason: unknown, fallback: string) => (reason instanceof Error ? reason.message : fallback),
}));

import { OntReadReportPanel } from '../../src/components/ngs/OntReadReportPanel';


const REPORT = {
    schema: 'bms.ngs.read-report.v1',
    source_artifact: { artifact_id: 'a', kind: 'alignment_bam', filename: 'calls.bam', sha256: 'f'.repeat(64), size_bytes: 10, url: '/api/jobs/j/ngs-artifacts/a' },
    truncated_at_reads: null,
    reads: 12, total_bases: 65630, mean_read_length_bp: 5469.2, n50_read_length_bp: 5549, longest_read_bp: 6000,
    mean_qscore: 21.9, median_qscore: 22.1, reads_q10_or_better: 12, reads_q20_or_better: 9,
    read_length_histogram: { bin_width_bp: 100, bins: [{ start_bp: 5400, end_bp_exclusive: 5500, read_count: 12, base_count: 65630 }] },
    alignment: { aligned: false, mapped_reads: 0, unmapped_reads: 12, secondary_records: 0, supplementary_records: 0, references: [], reads_per_reference: {} },
    move_tagged_reads: 12,
    modifications: { probability_threshold: 0.5, by_code: [
        { code: 'A+6mA', called_sites: 4291, modified_sites: 334, reads: 12, modified_fraction: 0.077837 },
        { code: 'C+5mC', called_sites: 5258, modified_sites: 9, reads: 12, modified_fraction: 0.001712 },
    ] },
    programs: [{ id: 'basecaller', program: 'dorado', version: '2.1.2', command: 'dorado basecaller' }],
    read_groups: [{ id: 'rg', basecall_model: 'runid=r basecall_model=dna_r10.4.1_e8.2_400bps_hac@v6.0.0 modbase_models=x_6mA@v1,x_5mCG_5hmCG@v1' }],
};

const READS = { total_primary_reads: 1, reads: [{ read_id: 'r-long', length: 4, qscore: 20, has_moves: true, modified_calls: { 'A+6mA': 3 } }] };
const SQUIGGLE = {
    schema: 'bms.ngs.read-squiggle.v1', read_id: 'r-long', pod5_file: 'a.pod5', read_length: 4, stride: 6, trimmed_samples: 0,
    start_base: 0, end_base: 4, sample_offset: 0, signal_pa: Array.from({ length: 24 }, (_v, i) => 80 + i),
    bases: [
        { index: 0, base: 'G', sample_start: 0, sample_end: 6, modifications: {} },
        { index: 1, base: 'A', sample_start: 6, sample_end: 12, modifications: { 'A+6mA': 0.94 } },
        { index: 2, base: 'T', sample_start: 12, sample_end: 18, modifications: {} },
        { index: 3, base: 'C', sample_start: 18, sample_end: 24, modifications: { 'C+5mC': 0.1 } },
    ],
};

describe('OntReadReportPanel', () => {
    let container: HTMLDivElement;
    let root: Root;
    beforeEach(() => { container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container); get.mockReset(); });
    afterEach(() => { act(() => root.unmount()); container.remove(); });

    async function mount(onOpenSignal?: () => void) {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        await act(async () => root.render(<QueryClientProvider client={client}><OntReadReportPanel jobId="j" onOpenSignal={onOpenSignal} /></QueryClientProvider>));
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    }

    it('shows yield, quality, model and calls methylation with the 5% cutoff', async () => {
        get.mockImplementation((url: string) => Promise.resolve({ data: url.endsWith('ngs-read-report') ? REPORT : url.endsWith('/reads') ? READS : SQUIGGLE }));
        const onOpenSignal = vi.fn();
        await mount(onOpenSignal);
        expect(get).toHaveBeenCalledWith('/api/jobs/j/ngs-read-report');
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
        expect(get).toHaveBeenCalledWith('/api/jobs/j/ngs-squiggle/reads/r-long', { params: { start_base: 0, bases: 120 } });
        const viewer = container.querySelector('[aria-label="Squiggle viewer"]')!;
        expect(viewer.querySelector('select')?.textContent).toContain('A+6mA 3');
        expect(viewer.querySelector('[aria-label="Called bases"]')?.textContent).toBe('GATC');
        expect(viewer.querySelector('[data-base-index="1"]')?.getAttribute('title')).toContain('A+6mA 94%');
        expect(viewer.querySelector('[data-testid="plot"]')?.getAttribute('data-shapes')).toBe('5');
        const summary = container.querySelector('[aria-label="Read summary"]')!;
        expect(summary.textContent).toContain('65,630 bp');
        expect(summary.textContent).toContain('5,549 bp');
        expect(container.textContent).toContain('dna_r10.4.1_e8.2_400bps_hac@v6.0.0');
        expect(container.querySelector('[data-mod-code="A+6mA"]')?.textContent).toContain('methylated');
        expect(container.querySelector('[data-mod-code="A+6mA"]')?.textContent).not.toContain('not methylated');
        expect(container.querySelector('[data-mod-code="C+5mC"]')?.textContent).toContain('not methylated');
        const open = Array.from(container.querySelectorAll('button')).find(node => node.textContent === 'Open squiggles')!;
        await act(async () => open.click());
        expect(onOpenSignal).toHaveBeenCalled();
    });

    it('reports a missing BAM instead of rendering an empty report', async () => {
        get.mockRejectedValue(new Error('This job published no reads BAM.'));
        await mount();
        expect(container.querySelector('[role="alert"]')?.textContent).toContain('no reads BAM');
    });
});
