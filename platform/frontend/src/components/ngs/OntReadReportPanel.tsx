import React from 'react';
import Plot from 'react-plotly.js';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { describeNgsError, withAlignmentAccessRecovery } from '../../lib/ngsAlignmentSession';

export interface OntReadReport {
    schema: 'bms.ngs.read-report.v1';
    source_artifact: { artifact_id: string | null; kind: string | null; filename: string | null; sha256: string | null; size_bytes: number | null; url: string | null };
    truncated_at_reads: number | null;
    reads: number;
    total_bases: number;
    mean_read_length_bp: number;
    n50_read_length_bp: number;
    longest_read_bp: number;
    mean_qscore: number | null;
    median_qscore: number | null;
    reads_q10_or_better: number;
    reads_q20_or_better: number;
    read_length_histogram: { bin_width_bp: number; bins: Array<{ start_bp: number; end_bp_exclusive: number; read_count: number; base_count: number }> };
    alignment: {
        aligned: boolean; mapped_reads: number; unmapped_reads: number; secondary_records: number; supplementary_records: number;
        references: Array<{ name: string; length: number }>; reads_per_reference: Record<string, number>;
    };
    move_tagged_reads: number;
    modifications: { probability_threshold: number; by_code: Array<{ code: string; called_sites: number; modified_sites: number; reads: number; modified_fraction: number }> };
    programs: Array<{ id: string; program: string; version: string; command: string }>;
    read_groups: Array<{ id: string; basecall_model: string }>;
}

/** Plasmids from Dam+/Dcm+ E. coli typically show 6mA well above this; below it reads look unmethylated. */
export const METHYLATED_FRACTION_CUTOFF = 0.05;

export function fetchOntReadReport(jobId: string): Promise<OntReadReport> {
    return withAlignmentAccessRecovery(jobId, async () => {
        const response = await api.get<OntReadReport>(`/api/jobs/${encodeURIComponent(jobId)}/ngs-read-report`);
        if (response.data?.schema !== 'bms.ngs.read-report.v1') throw new Error('Unexpected read report shape.');
        return response.data;
    });
}

const pct = (value: number, digits = 1) => `${(value * 100).toFixed(digits)}%`;
const n = (value: number | null | undefined, suffix = '') => (value == null ? '—' : `${value.toLocaleString()}${suffix}`);

function basecallModel(report: OntReadReport): string {
    const description = report.read_groups[0]?.basecall_model ?? '';
    const model = /basecall_model=(\S+)/.exec(description)?.[1];
    const mods = /modbase_models=(\S+)/.exec(description)?.[1];
    return [model, mods ? `mods: ${mods.split(',').join(', ')}` : ''].filter(Boolean).join(' · ') || '—';
}

/** Report for basecalled/analysed reads when no plasmid verification package exists. */
export function OntReadReportPanel({ jobId, onOpenSignal }: { jobId: string; onOpenSignal?: () => void }) {
    const query = useQuery({ queryKey: ['ngs-read-report', jobId], queryFn: () => fetchOntReadReport(jobId), retry: false, staleTime: 60_000 });
    if (query.isLoading) return <p className="text-sm text-[var(--text-secondary)]">Reading the run's BAM…</p>;
    if (query.isError) return <p role="alert" className="text-sm text-[var(--warning)]">Read report unavailable: {describeNgsError(query.error, 'the reads BAM could not be summarised.')}</p>;
    const report = query.data!;
    const align = report.alignment;
    const bins = report.read_length_histogram.bins;
    const cards: Array<[string, string, string]> = [
        ['Reads', n(report.reads), report.truncated_at_reads ? `first ${n(report.truncated_at_reads)} primary reads` : 'primary records'],
        ['Yield', n(report.total_bases, ' bp'), 'sum of read lengths'],
        ['Read N50', n(report.n50_read_length_bp, ' bp'), `mean ${n(Math.round(report.mean_read_length_bp))} · max ${n(report.longest_read_bp)}`],
        ['Mean Q', report.mean_qscore == null ? '—' : report.mean_qscore.toFixed(1), `median ${report.median_qscore?.toFixed(1) ?? '—'}`],
        ['≥Q10 / ≥Q20', report.reads ? `${pct(report.reads_q10_or_better / report.reads, 0)} / ${pct(report.reads_q20_or_better / report.reads, 0)}` : '—', 'share of reads'],
        ['Mapped', align.aligned ? `${n(align.mapped_reads)} / ${n(report.reads)}` : 'not aligned', align.aligned ? `${align.references.length} reference contig(s)` : 'no reference supplied'],
        ['Move tables', n(report.move_tagged_reads), report.move_tagged_reads ? 'squiggle mapping possible' : 'enable "emit moves" for squiggles'],
    ];
    return (
        <div className="space-y-3" data-testid="ont-read-report">
            <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                        <h4 className="text-sm font-semibold text-[var(--text-primary)]">Read report</h4>
                        <p className="text-xs text-[var(--text-secondary)]">From <span className="font-mono">{report.source_artifact.filename ?? 'reads BAM'}</span> · {basecallModel(report)}</p>
                    </div>
                    <div className="flex gap-2">
                        {report.source_artifact.url && <a href={report.source_artifact.url} download className="rounded border border-[var(--border-primary)] px-2 py-1 text-xs">Download BAM</a>}
                        {onOpenSignal && report.move_tagged_reads > 0 && <button type="button" onClick={onOpenSignal} className="rounded border border-[var(--border-primary)] px-2 py-1 text-xs">Open squiggles</button>}
                    </div>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7" aria-label="Read summary">
                    {cards.map(([label, value, detail]) => (
                        <div key={label} className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2">
                            <div className="text-[11px] text-[var(--text-secondary)]">{label}</div>
                            <div className="font-mono text-sm text-[var(--text-primary)]">{value}</div>
                            <div className="mt-1 text-[9px] leading-tight text-[var(--text-secondary)]">{detail}</div>
                        </div>
                    ))}
                </div>
            </section>

            {report.modifications.by_code.length > 0 && (
                <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3" aria-label="Modification calls">
                    <h4 className="text-sm font-semibold text-[var(--text-primary)]">Modification calls</h4>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">Per-read MM/ML calls with P(modified) ≥ {report.modifications.probability_threshold}. Above {pct(METHYLATED_FRACTION_CUTOFF, 0)} 6mA usually indicates Dam-methylated template; run methylation tables for per-site positions.</p>
                    <table className="mt-2 w-full text-xs">
                        <thead><tr className="border-b border-[var(--border-primary)] text-left text-[var(--text-secondary)]">
                            <th className="px-2 py-1">Modification</th><th className="px-2 py-1">Called sites</th><th className="px-2 py-1">Modified</th><th className="px-2 py-1">Fraction</th><th className="px-2 py-1">Reads</th><th className="px-2 py-1">Call</th>
                        </tr></thead>
                        <tbody>
                            {report.modifications.by_code.map(row => (
                                <tr key={row.code} className="border-b border-[var(--border-primary)]/40" data-mod-code={row.code}>
                                    <td className="px-2 py-1 font-mono">{row.code}</td>
                                    <td className="px-2 py-1 font-mono">{n(row.called_sites)}</td>
                                    <td className="px-2 py-1 font-mono">{n(row.modified_sites)}</td>
                                    <td className="px-2 py-1 font-mono">{pct(row.modified_fraction, 2)}</td>
                                    <td className="px-2 py-1 font-mono">{n(row.reads)}</td>
                                    <td className="px-2 py-1">{row.modified_fraction >= METHYLATED_FRACTION_CUTOFF ? 'methylated' : 'not methylated'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </section>
            )}

            <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3">
                <h4 className="text-sm font-semibold text-[var(--text-primary)]">Read-length distribution</h4>
                <Plot
                    data={[
                        { type: 'bar', name: 'Reads', x: bins.map(bin => (bin.start_bp + bin.end_bp_exclusive) / 2), y: bins.map(bin => bin.read_count), marker: { color: '#55a7ff' } },
                        { type: 'scatter', mode: 'lines', name: 'Bases', yaxis: 'y2', x: bins.map(bin => (bin.start_bp + bin.end_bp_exclusive) / 2), y: bins.map(bin => bin.base_count), line: { color: '#fbbf24' } },
                    ]}
                    layout={{
                        title: { text: 'Reads and bases by read length' },
                        xaxis: { title: { text: 'Read length (bp)' } },
                        yaxis: { title: { text: 'Reads' } },
                        yaxis2: { title: { text: 'Bases' }, overlaying: 'y', side: 'right' },
                        margin: { l: 62, r: 62, t: 44, b: 56 }, height: 300, showlegend: false,
                        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { color: '#b8c4d8' },
                    }}
                    config={{ responsive: true, displaylogo: false }}
                    useResizeHandler
                    style={{ width: '100%', height: '300px' }}
                />
            </section>

            {align.aligned && Object.keys(align.reads_per_reference).length > 0 && (
                <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs">
                    <h4 className="text-sm font-semibold text-[var(--text-primary)]">Reads per reference</h4>
                    <ul className="mt-1 space-y-0.5 font-mono">
                        {Object.entries(align.reads_per_reference).map(([name, count]) => <li key={name}>{name}: {count.toLocaleString()}</li>)}
                    </ul>
                </section>
            )}

            <details className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs">
                <summary className="cursor-pointer font-semibold text-[var(--text-primary)]">Programs and read groups</summary>
                <div className="mt-2 space-y-1 break-all font-mono text-[10px] text-[var(--text-secondary)]">
                    {report.programs.map(program => <div key={program.id}>{program.program} {program.version}: {program.command}</div>)}
                    {report.read_groups.map(group => <div key={group.id}>RG {group.id}: {group.basecall_model}</div>)}
                </div>
            </details>
        </div>
    );
}
