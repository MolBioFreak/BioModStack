import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

vi.mock('react-plotly.js', () => ({
    default: ({ data, layout }: { data?: Array<{ y?: number[] }>; layout?: { title?: { text?: string } } }) => (
        <div data-testid="scientific-plot" data-plot-title={layout?.title?.text} data-y={JSON.stringify(data?.[0]?.y)} />
    ),
}));
const viewer = vi.hoisted(() => ({ props: null as null | Record<string, any> }));
vi.mock('../../src/components/MolBioToolkit/SequenceViewer', () => ({
    SequenceViewer: (props: Record<string, any>) => { viewer.props = props; return <div data-testid="plasmid-map" />; },
}));
import { api } from '../../src/lib/api';
import { PlasmidSummaryPanel, type PlasmidSummaryResponse } from '../../src/components/ngs/PlasmidSummaryPanel';

const url = (name: string) => `/api/jobs/job-1/ngs-artifacts/${name}`;
function response(): PlasmidSummaryResponse {
    return {
        schema: 'bms.ngs.plasmid-summary-response.v1', job_id: 'job-1',
        summary: {
            schema: 'bms.ngs.plasmid-summary.v1', sample: 'JAN26-EL-Q2-01', expected_sequence_provided: false,
            coordinates: 'consensus', topology: 'circular',
            consensus: { name: 's', length_bp: 5569, sequence: 'ACGT', source: 's.final.fasta' },
            reads: { total_reads: 61708, total_bases: 315879481, longest_read_bp: 40210, source: 'source_reads.fastq' },
            alignment: { mapped_reads: 61500, unmapped_reads: 208, source: 'alignment_stats.tsv' },
            coverage: { positions: 5569, positions_with_reads: 5569, covered_fraction: 1, mean_depth: 56292.4, minimum_depth: 49295, maximum_depth: 56893, source: 'per_base_support.tsv',
                points: [{ position_1based: 1, end_1based: 10, minimum_depth: 56000, maximum_depth: 56893 }, { position_1based: 11, end_1based: 20, minimum_depth: 49295, maximum_depth: 56000 }] },
            read_length_histogram: { bin_width_bp: 100, source: 'source_reads.fastq', bins: [
                { start_bp: 5500, end_bp_exclusive: 5600, read_count: 42000, base_count: 233000000 },
                { start_bp: 11100, end_bp_exclusive: 11200, read_count: 1500, base_count: 16700000 },
                { start_bp: 300, end_bp_exclusive: 400, read_count: 20, base_count: 7000 },
            ] },
            features: [{ name: 'EGFP', start: 2434, end: 3154, strand: -1 }], features_source: 's.annotations.bed',
        },
        files: [
            { key: 'consensus', label: 'Consensus', format: 'FASTA', url: url('consensus'), size_bytes: 5600, filename: 's.final.fasta' },
            { key: 'chromatogram', label: 'Chromatogram', format: 'AB1', url: url('ab1'), size_bytes: 268048, filename: 'consensus_pileup.ab1' },
            { key: 'per_base_table', label: 'Per-base table', format: 'TSV', url: url('table'), size_bytes: 360000, filename: 'per_base_support.tsv' },
            { key: 'alignments', label: 'Alignments', format: 'BAM', url: url('bam'), size_bytes: 3_000_000_000, filename: 'aligned.bam' },
        ],
        download_all: { url: '/api/jobs/job-1/ngs-plasmid-summary/download-all', included: ['consensus', 'chromatogram', 'per_base_table'], left_out: ['alignments'] },
    };
}

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
beforeEach(() => {
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});
afterEach(async () => { await act(async () => root.unmount()); document.body.replaceChildren(); client.clear(); vi.restoreAllMocks(); });
async function mount(props: Partial<React.ComponentProps<typeof PlasmidSummaryPanel>> = {}) {
    await act(async () => root.render(<QueryClientProvider client={client}><PlasmidSummaryPanel jobId="job-1" {...props} /></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
}

it('shows the summary numbers, map, plots, gel, chromatogram and every published file', async () => {
    const get = vi.spyOn(api, 'get').mockImplementation(async (path: string) => (
        path.endsWith('/ngs-plasmid-summary') ? { data: response() } : { data: 'not a table' }) as never);
    const onOpenIgv = vi.fn();
    await mount({ onOpenIgv });
    expect(get).toHaveBeenCalledWith('/api/jobs/job-1/ngs-plasmid-summary');
    const text = container.textContent!;
    expect(text).toContain('Plasmid summary · JAN26-EL-Q2-01');
    expect(text).toContain('Assembled from reads. No expected sequence was given.');
    // No host reference was given: the two tiles say so instead of showing a number.
    expect(text).toContain('E. coli genomic DNA—needs a host reference');
    expect(text).toContain('Share of sample— / —molar / mass');
    expect(container.querySelector('[data-testid="plasmid-host-composition"]')).toBeNull();
    for (const value of ['5,569 bp', '61,708', '315,879,481 bp', '61,500 / 61,708', '56,292×', '100.00%', 'Depth 49,295 to 56,893.']) expect(text).toContain(value);
    // The existing plasmid viewer draws the map from the consensus and the annotation file.
    expect(viewer.props!.viewMode).toBe('circular');
    expect(viewer.props!.sequenceData).toMatchObject({ name: 'JAN26-EL-Q2-01', sequence: 'ACGT', circular: true });
    expect(viewer.props!.sequenceData.features).toEqual([{ name: 'EGFP', start: 2434, end: 3154, strand: -1, id: 'plasmid-feature-0', type: 'misc_feature' }]);
    const plots = Array.from(container.querySelectorAll('[data-testid="scientific-plot"]'));
    expect(plots.map(plot => plot.getAttribute('data-plot-title'))).toEqual(['Coverage', 'Read count by read length']);
    expect(plots[0].getAttribute('data-y')).toBe('[56000,49295]');
    // The gel draws the monomer and dimer bands and drops the bin under 1% of the brightest.
    expect(Array.from(container.querySelectorAll('[data-gel-band]')).map(node => node.getAttribute('data-gel-band'))).toEqual(['5500', '11100']);
    expect(container.querySelector('[data-testid="pileup-chromatogram"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="pileup-ab1-download"]')!.getAttribute('href')).toBe(url('ab1'));
    expect(Array.from(container.querySelectorAll('[data-plasmid-file]')).map(node => [node.getAttribute('data-plasmid-file'), node.getAttribute('href')])).toEqual([
        ['consensus', url('consensus')], ['chromatogram', url('ab1')], ['per_base_table', url('table')], ['alignments', url('bam')]]);
    expect(container.querySelector('[data-testid="plasmid-download-all"]')!.getAttribute('href')).toBe('/api/jobs/job-1/ngs-plasmid-summary/download-all');
    expect(text).toContain('Download all leaves out Alignments because the set is over 2 GB.');
    const buttons = Array.from(container.querySelectorAll('button'));
    const igv = buttons.find(button => button.textContent === 'View in IGV')!;
    await act(async () => igv.click());
    expect(onOpenIgv).toHaveBeenCalledTimes(1);
    expect(buttons.find(button => button.textContent === 'Open squiggles')!.disabled).toBe(true);
});

it('reports host DNA over the whole read population and labels the molar and mass shares as estimates', async () => {
    const withHost: PlasmidSummaryResponse = { ...response(), host_composition: {
        schema: 'bms.ngs.host-composition.v1',
        denominator: { total_reads: 4200, total_bases: 20291787, source: 'population.fastq' },
        categories: {
            plasmid: { reads: 3990, bases: 19650000, read_fraction: 0.95, base_fraction: 0.9684 },
            host: { reads: 200, bases: 600000, read_fraction: 200 / 4200, base_fraction: 0.02957 },
            ambiguous: { reads: 0, bases: 0, read_fraction: 0, base_fraction: 0 },
            unclassified: { reads: 10, bases: 41787, read_fraction: 10 / 4200, base_fraction: 0.00206 },
        },
        estimate: { host_mass_fraction: 0.0296, host_molar_fraction: 0.0000366, method: 'aligned bases divided by molecule length', checked_against_known_mixture: false },
    } };
    vi.spyOn(api, 'get').mockImplementation(async (path: string) => (path.endsWith('/ngs-plasmid-summary') ? { data: withHost } : { data: 'not a table' }) as never);
    await mount();
    const text = container.textContent!;
    expect(text).toContain('E. coli genomic DNA4.76%200 of 4,200 reads · 2.96% of bases');
    expect(text).toContain('Share of sample0.004% / 2.96%host, molar / mass · estimate, not checked against a known mixture');
    expect(container.querySelector('[data-testid="plasmid-host-composition"]')!.textContent).toContain('Of 4,200 reads: 3,990 plasmid, 200 host, 0 ambiguous, 10 unclassified.');
});

it('stays out of the way for a job that published no plasmid summary', async () => {
    vi.spyOn(api, 'get').mockRejectedValue({ response: { status: 404 } });
    await mount();
    expect(container.querySelector('[data-testid="plasmid-summary"]')).toBeNull();
    expect(container.textContent).toBe('');
});

it('says so without an alert when the summary cannot be loaded', async () => {
    vi.spyOn(api, 'get').mockRejectedValue({ response: { status: 500 } });
    await mount();
    expect(container.textContent).toContain('The plasmid summary could not be loaded.');
    expect(container.querySelector('[role="alert"]')).toBeNull();
});
