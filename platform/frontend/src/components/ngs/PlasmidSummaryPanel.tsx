import Plot from 'react-plotly.js';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { withAlignmentAccessRecovery } from '../../lib/ngsAlignmentSession';
import { SequenceViewer } from '../MolBioToolkit/SequenceViewer';
import { DEFAULT_VISIBILITY } from '../MolBioToolkit/sequenceViewerConstants';
import { PileupChromatogram } from './PileupChromatogram';

export interface PlasmidSummaryFile { key: string; label: string; format: string; url: string | null; size_bytes: number | null; filename: string }
export interface PlasmidSummaryResponse {
    schema: 'bms.ngs.plasmid-summary-response.v1';
    job_id: string;
    summary: {
        schema: 'bms.ngs.plasmid-summary.v1';
        sample: string;
        expected_sequence_provided: boolean;
        coordinates: 'consensus' | 'reference';
        topology: 'circular' | 'linear';
        consensus: { name: string; length_bp: number; sequence: string; source: string };
        reads: { total_reads: number; total_bases: number; longest_read_bp: number; source: string };
        alignment: { mapped_reads: number | null; unmapped_reads: number | null; source: string | null };
        coverage: {
            positions: number; positions_with_reads: number; covered_fraction: number | null; mean_depth: number | null;
            minimum_depth: number | null; maximum_depth: number | null; source: string;
            points: Array<{ position_1based: number; end_1based: number; minimum_depth: number; maximum_depth: number }>;
        };
        read_length_histogram: { bin_width_bp: number; source: string; bins: Array<{ start_bp: number; end_bp_exclusive: number; read_count: number; base_count: number }> };
        features: Array<{ name: string; start: number; end: number; strand: 1 | -1 }>;
        features_source: string | null;
    };
    host_composition?: HostComposition | null;
    files: PlasmidSummaryFile[];
    download_all: { url: string; included: string[]; left_out: string[] };
}

interface HostCategory { reads: number; bases: number; read_fraction: number | null; base_fraction: number | null }
export interface HostComposition {
    schema: 'bms.ngs.host-composition.v1';
    denominator: { total_reads: number; total_bases: number; source: string };
    categories: Record<'plasmid' | 'host' | 'ambiguous' | 'unclassified', HostCategory>;
    estimate: { host_mass_fraction: number; host_molar_fraction: number; method: string; checked_against_known_mixture: boolean } | null;
}

const percent = (value: number | null | undefined) => (value == null ? '—' : `${(value * 100).toFixed(value > 0 && value < 0.001 ? 3 : 2)}%`);

/** The job's plasmid summary, or null when the job published none. */
export async function fetchPlasmidSummary(jobId: string): Promise<PlasmidSummaryResponse | null> {
    try {
        return await withAlignmentAccessRecovery(jobId, async () => {
            const response = await api.get<PlasmidSummaryResponse>(`/api/jobs/${encodeURIComponent(jobId)}/ngs-plasmid-summary`);
            return response.data?.schema === 'bms.ngs.plasmid-summary-response.v1' ? response.data : null;
        });
    } catch (error) {
        if ((error as { response?: { status?: number } })?.response?.status === 404) return null;
        throw error;
    }
}

const GEL_KB = [40, 20, 10, 5, 3, 2, 1];
function gelY(bp: number, top = 16, bottom = 196): number {
    const kb = Math.min(GEL_KB[0], Math.max(0.5, bp / 1000));
    const span = Math.log10(GEL_KB[0]) - Math.log10(0.5);
    return top + ((Math.log10(GEL_KB[0]) - Math.log10(kb)) / span) * (bottom - top);
}

const n = (value: number | null | undefined, suffix = '') => (value == null ? '—' : `${value.toLocaleString()}${suffix}`);

interface PlasmidSummaryPanelProps { jobId: string; onOpenIgv?: () => void; onOpenSignal?: () => void }

/** One summary per sample, restating outputs the run already wrote. Hidden when the job has none. */
export function PlasmidSummaryPanel({ jobId, onOpenIgv, onOpenSignal }: PlasmidSummaryPanelProps) {
    const query = useQuery({ queryKey: ['ngs-plasmid-summary', jobId], queryFn: () => fetchPlasmidSummary(jobId), retry: false, staleTime: 60_000 });
    if (query.isLoading) return null;
    if (query.isError) return <p className="text-xs text-[var(--text-secondary)]">The plasmid summary could not be loaded. The other results below are unaffected.</p>;
    const data = query.data;
    if (!data) return null;
    const { summary, files } = data;
    const file = (key: string) => files.find(item => item.key === key && item.url) ?? null;
    const coverage = summary.coverage;
    const bins = summary.read_length_histogram.bins;
    const brightest = Math.max(1, ...bins.map(bin => bin.base_count));
    const tiles: Array<[string, string, string]> = [
        ['Consensus length', n(summary.consensus.length_bp, ' bp'), summary.expected_sequence_provided ? 'assembled consensus' : 'assembled from reads'],
        ['Total reads', n(summary.reads.total_reads), 'integer read count'],
        ['Total bases', n(summary.reads.total_bases, ' bp'), 'sequenced bases'],
        ['Mapped reads', summary.alignment.mapped_reads == null ? '—' : `${n(summary.alignment.mapped_reads)} / ${n(summary.reads.total_reads)}`, 'from the alignment statistics'],
        ['Mean coverage', coverage.mean_depth == null ? '—' : `${Math.round(coverage.mean_depth).toLocaleString()}×`, 'aligned depth'],
        ['Covered', coverage.covered_fraction == null ? '—' : `${(coverage.covered_fraction * 100).toFixed(2)}%`, 'positions with reads'],
    ];
    const host = data.host_composition?.schema === 'bms.ngs.host-composition.v1' ? data.host_composition : null;
    tiles.push(
        ['E. coli genomic DNA', host ? percent(host.categories.host.read_fraction) : '—',
            host ? `${n(host.categories.host.reads)} of ${n(host.denominator.total_reads)} reads · ${percent(host.categories.host.base_fraction)} of bases` : 'needs a host reference'],
        ['Share of sample', host?.estimate ? `${percent(host.estimate.host_molar_fraction)} / ${percent(host.estimate.host_mass_fraction)}` : '— / —',
            host?.estimate ? 'host, molar / mass · estimate, not checked against a known mixture' : 'molar / mass'],
    );
    const perBase = file('per_base_table');
    return (
        <section className="space-y-3 rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3" data-testid="plasmid-summary" aria-label="Plasmid summary">
            <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                    <h4 className="text-sm font-semibold text-[var(--text-primary)]">Plasmid summary · {summary.sample}</h4>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                        {summary.expected_sequence_provided
                            ? 'Positions are in the coordinates of the expected sequence.'
                            : 'Assembled from reads. No expected sequence was given.'}
                    </p>
                </div>
                <div className="flex flex-wrap gap-2 text-xs">
                    <button type="button" disabled={!onOpenIgv} onClick={onOpenIgv}
                        className="rounded border border-[var(--border-primary)] px-3 py-1.5 text-[var(--text-primary)] disabled:opacity-50">View in IGV</button>
                    <button type="button" disabled={!onOpenSignal} onClick={onOpenSignal} title={onOpenSignal ? undefined : 'This result has no raw signal bound to it.'}
                        className="rounded border border-[var(--border-primary)] px-3 py-1.5 text-[var(--text-primary)] disabled:opacity-50">Open squiggles</button>
                    <a href={data.download_all.url} download data-testid="plasmid-download-all"
                        className="rounded border border-[var(--border-primary)] px-3 py-1.5 text-[var(--accent-primary)]">Download all</a>
                </div>
            </div>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8" aria-label="Summary numbers">
                {tiles.map(([label, value, detail]) => (
                    <div key={label} className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2">
                        <div className="text-[11px] text-[var(--text-secondary)]">{label}</div>
                        <div className="font-mono text-sm text-[var(--text-primary)]">{value}</div>
                        <div className="mt-1 text-[9px] leading-tight text-[var(--text-secondary)]">{detail}</div>
                    </div>
                ))}
            </div>
            <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
                <div className="rounded border border-[var(--border-primary)] p-2">
                    <h5 className="text-xs font-semibold text-[var(--text-primary)]">Plasmid map</h5>
                    <p className="text-[11px] text-[var(--text-secondary)]">
                        {summary.features.length} annotated feature{summary.features.length === 1 ? '' : 's'}{summary.features_source ? ` from ${summary.features_source}` : '; no annotation file was published'}.
                    </p>
                    <div className="relative mt-1 overflow-hidden" style={{ height: 300 }}>
                        <SequenceViewer
                            viewMode={summary.topology === 'circular' ? 'circular' : 'linear'}
                            visibility={DEFAULT_VISIBILITY}
                            sequenceData={{
                                name: summary.sample, sequence: summary.consensus.sequence, circular: summary.topology === 'circular',
                                sequenceType: 'dna', primers: [],
                                features: summary.features.map((feature, index) => ({ ...feature, id: `plasmid-feature-${index}`, type: 'misc_feature' })),
                            }}
                        />
                    </div>
                </div>
                <div className="rounded border border-[var(--border-primary)] p-2 xl:col-span-2">
                    <h5 className="text-xs font-semibold text-[var(--text-primary)]">Coverage across the {summary.coordinates === 'consensus' ? 'consensus' : 'expected sequence'}</h5>
                    <p className="text-[11px] text-[var(--text-secondary)]">Depth {n(coverage.minimum_depth)} to {n(coverage.maximum_depth)}. Each point is the lowest depth in its window, so a narrow dip stays visible.</p>
                    <Plot
                        data={[{ type: 'scatter', mode: 'lines', fill: 'tozeroy', x: coverage.points.map(point => point.position_1based), y: coverage.points.map(point => point.minimum_depth), line: { color: '#55a7ff' } }]}
                        layout={{ title: { text: 'Coverage' }, height: 260, margin: { l: 60, r: 10, t: 30, b: 40 }, xaxis: { title: { text: 'Position (bp)' } }, yaxis: { title: { text: 'Depth' }, rangemode: 'tozero' } }}
                        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} useResizeHandler
                    />
                </div>
            </div>
            <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
                <div className="rounded border border-[var(--border-primary)] p-2 xl:col-span-2">
                    <h5 className="text-xs font-semibold text-[var(--text-primary)]">Read-length distribution</h5>
                    <p className="text-[11px] text-[var(--text-secondary)]">Reads per {summary.read_length_histogram.bin_width_bp} bp. Longest read {n(summary.reads.longest_read_bp, ' bp')}.</p>
                    <Plot
                        data={[{ type: 'bar', x: bins.map(bin => (bin.start_bp + bin.end_bp_exclusive) / 2), y: bins.map(bin => bin.read_count), marker: { color: '#55a7ff' } }]}
                        layout={{ title: { text: 'Read count by read length' }, height: 260, margin: { l: 60, r: 10, t: 30, b: 40 }, xaxis: { title: { text: 'Read length (bp)' } }, yaxis: { title: { text: 'Reads' } } }}
                        config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }} useResizeHandler
                    />
                </div>
                <div className="rounded border border-[var(--border-primary)] p-2">
                    <h5 className="text-xs font-semibold text-[var(--text-primary)]">Virtual gel</h5>
                    <p className="text-[11px] text-[var(--text-secondary)]">This sample. Band brightness is the bases in each {summary.read_length_histogram.bin_width_bp} bp length bin, scaled to the brightest band.</p>
                    <svg role="img" aria-label="Virtual gel" viewBox="0 0 180 210" className="mt-1 w-full" style={{ maxHeight: 260, background: '#111' }} data-testid="plasmid-virtual-gel">
                        <text x={30} y={12} textAnchor="end" fontSize={9} fill="#ccc">kb</text>
                        {GEL_KB.map(kb => (
                            <g key={kb}>
                                <text x={30} y={gelY(kb * 1000) + 3} textAnchor="end" fontSize={9} fill="#ccc">{kb}</text>
                                <line x1={34} x2={42} y1={gelY(kb * 1000)} y2={gelY(kb * 1000)} stroke="#ccc" />
                            </g>
                        ))}
                        {bins.filter(bin => bin.base_count / brightest >= 0.01).map(bin => (
                            <rect key={bin.start_bp} x={64} width={84} height={3} y={gelY((bin.start_bp + bin.end_bp_exclusive) / 2) - 1.5}
                                fill="#d8ffd0" opacity={bin.base_count / brightest} data-gel-band={bin.start_bp} />
                        ))}
                    </svg>
                </div>
            </div>
            {host && (
                <p className="text-[11px] text-[var(--text-secondary)]" data-testid="plasmid-host-composition">
                    Of {n(host.denominator.total_reads)} reads: {n(host.categories.plasmid.reads)} plasmid, {n(host.categories.host.reads)} host,{' '}
                    {n(host.categories.ambiguous.reads)} ambiguous, {n(host.categories.unclassified.reads)} unclassified. Host identity is counted separately from monomer, multimer and fragment structure.
                    {host.estimate && ` Molar and mass shares are estimates from ${host.estimate.method}.`}
                </p>
            )}
            {perBase?.url && (
                <PileupChromatogram tableUrl={perBase.url} ab1Url={file('chromatogram')?.url ?? null}
                    coordinateLabel={summary.coordinates === 'consensus' ? 'consensus' : 'expected sequence'} />
            )}
            <div>
                <h5 className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Downloads</h5>
                <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
                    {files.map(item => (
                        <a key={item.key} href={item.url || undefined} download data-plasmid-file={item.key}
                            className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 text-xs">
                            <div className="font-semibold text-[var(--accent-primary)]">{item.label}</div>
                            <div className="mt-1 text-[var(--text-secondary)]">{item.format} · {n(item.size_bytes, ' bytes')}</div>
                        </a>
                    ))}
                </div>
                {data.download_all.left_out.length > 0 && (
                    <p className="mt-2 text-[11px] text-[var(--text-secondary)]">
                        Download all leaves out {data.download_all.left_out.map(key => files.find(item => item.key === key)?.label ?? key).join(' and ')} because the set is over 2 GB. Use the tiles above for those.
                    </p>
                )}
            </div>
        </section>
    );
}
