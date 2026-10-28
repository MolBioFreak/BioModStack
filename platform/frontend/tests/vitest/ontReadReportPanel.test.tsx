// @vitest-environment jsdom
import React from 'react';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const get = vi.fn();
vi.mock('react-plotly.js', () => ({ default: () => <div data-testid="plot" /> }));
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
        get.mockResolvedValue({ data: REPORT });
        const onOpenSignal = vi.fn();
        await mount(onOpenSignal);
        expect(get).toHaveBeenCalledWith('/api/jobs/j/ngs-read-report');
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
