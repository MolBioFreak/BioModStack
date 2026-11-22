import type React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
    fetchOntInstrumentRunLive,
    type OntRunLive,
    type OntRunLiveActivity,
    type OntRunLiveCumulative,
    type OntRunLiveReadLengths,
} from '../../lib/api';

const count = (value: number) => value.toLocaleString('en-US');

const bases = (value: number) => {
    if (value >= 1e9) return `${(value / 1e9).toFixed(2)} Gb`;
    if (value >= 1e6) return `${(value / 1e6).toFixed(2)} Mb`;
    if (value >= 1e3) return `${(value / 1e3).toFixed(1)} kb`;
    return `${value} b`;
};

const percent = (value: number) => `${Math.round(value * 100)}%`;


const ACTIVITY = [
    { key: 'sequencing', label: 'Sequencing', color: '#7ed321' },
    { key: 'pore', label: 'Open pore', color: '#2e7d32' },
    { key: 'recovering', label: 'Recovering', color: '#1565c0' },
    { key: 'inactive', label: 'Inactive', color: '#81d4fa' },
    { key: 'unclassified', label: 'Unclassified', color: '#6b7280' },
] as const;

const W = 560;
const H = 170;
const PAD = { left: 44, right: 8, top: 8, bottom: 22 };
const plotW = W - PAD.left - PAD.right;
const plotH = H - PAD.top - PAD.bottom;

const runTime = (seconds: number) => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return hours ? `${hours}h ${minutes}m` : `${minutes}m`;
};

function Chart({ title, note, label, legend, children }: {
    title: string; note?: string; label: string;
    legend?: ReadonlyArray<{ label: string; color: string }>; children: React.ReactNode;
}) {
    return (
        <figure className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3">
            <figcaption className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-semibold text-[var(--text-primary)]">{title}</span>
                {note ? <span>{note}</span> : null}
            </figcaption>
            <svg role="img" aria-label={label} viewBox={`0 0 ${W} ${H}`} className="mt-1 w-full">{children}</svg>
            {legend ? (
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                    {legend.map((item) => (
                        <span key={item.label} className="flex items-center gap-1">
                            <span className="inline-block h-2 w-2 rounded-sm" style={{ background: item.color }} />{item.label}
                        </span>
                    ))}
                </div>
            ) : null}
        </figure>
    );
}

function Axes({ yTop, yLabel, xStart, xEnd }: { yTop: string; yLabel: string; xStart: string; xEnd: string }) {
    const text = { fill: 'var(--chart-axis, currentColor)', fontSize: 10 };
    return (
        <g>
            <line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={PAD.top + plotH} stroke="var(--chart-grid, currentColor)" />
            <line x1={PAD.left} y1={PAD.top + plotH} x2={W - PAD.right} y2={PAD.top + plotH} stroke="var(--chart-grid, currentColor)" />
            <text x={PAD.left - 4} y={PAD.top + 8} textAnchor="end" {...text}>{yTop}</text>
            <text x={PAD.left - 4} y={PAD.top + plotH} textAnchor="end" {...text}>0</text>
            <text x={4} y={PAD.top + plotH / 2} {...text}>{yLabel}</text>
            <text x={PAD.left} y={H - 6} {...text}>{xStart}</text>
            <text x={W - PAD.right} y={H - 6} textAnchor="end" {...text}>{xEnd}</text>
        </g>
    );
}

function PoreActivity({ rows }: { rows: OntRunLiveActivity[] }) {
    const width = plotW / rows.length;
    return (
        <Chart title="Pore activity" label="Pore activity over time" legend={ACTIVITY}
            note={`${percent(rows[rows.length - 1].sequencing)} sequencing now`}>
            <Axes yTop="100%" yLabel="" xStart={runTime(rows[0].start_seconds)} xEnd={runTime(rows[rows.length - 1].start_seconds)} />
            {rows.map((row, index) => {
                let top = PAD.top + plotH;
                return ACTIVITY.map((state) => {
                    const height = Math.max(0, row[state.key]) * plotH;
                    top -= height;
                    return <rect key={`${row.start_seconds}-${state.key}`} data-state={state.key} x={PAD.left + index * width} y={top}
                        width={Math.max(0.5, width - 0.5)} height={height} fill={state.color} />;
                });
            })}
        </Chart>
    );
}

const CUMULATIVE = [
    { key: 'estimated_selected_bases', label: 'Estimated', color: '#60a5fa' },
    { key: 'basecalled_pass_bases', label: 'Basecalled, passed', color: '#7ed321' },
    { key: 'basecalled_fail_bases', label: 'Basecalled, failed', color: '#f87171' },
] as const;

function CumulativeOutput({ points }: { points: OntRunLiveCumulative[] }) {
    const top = Math.max(1, ...points.flatMap((point) => CUMULATIVE.map((series) => point[series.key])));
    const last = points[points.length - 1].seconds || 1;
    const path = (key: typeof CUMULATIVE[number]['key']) => points.map((point, index) =>
        `${index ? 'L' : 'M'}${(PAD.left + (point.seconds / last) * plotW).toFixed(1)},${(PAD.top + plotH - (point[key] / top) * plotH).toFixed(1)}`).join(' ');
    return (
        <Chart title="Cumulative output" label="Cumulative output over time" legend={CUMULATIVE} note={`${count(points[points.length - 1].read_count)} reads`}>
            <Axes yTop={bases(top)} yLabel="" xStart="0m" xEnd={runTime(last)} />
            {CUMULATIVE.map((series) => <path key={series.key} data-series={series.key} d={path(series.key)} fill="none" stroke={series.color} strokeWidth={1.8} />)}
        </Chart>
    );
}

function ReadLengths({ lengths }: { lengths: OntRunLiveReadLengths }) {
    const buckets = lengths.buckets;
    const top = Math.max(1, ...buckets.map((bucket) => bucket.value));
    const width = plotW / buckets.length;
    const split = buckets.some((bucket) => (bucket.rejected ?? 0) > 0);
    return (
        <Chart title="Read length" label="Read length histogram" note={`Estimated N50 ${bases(Math.round(lengths.n50))}`}
            legend={split ? [{ label: 'Kept', color: '#7ed321' }, { label: 'Ejected by adaptive sampling', color: '#c026d3' }] : undefined}>
            <Axes yTop={bases(top)} yLabel="" xStart={bases(buckets[0].start)} xEnd={bases(buckets[buckets.length - 1].end)} />
            {buckets.map((bucket, index) => {
                const rejected = Math.min(bucket.rejected ?? 0, bucket.value);
                const keptHeight = ((bucket.value - rejected) / top) * plotH;
                const rejectedHeight = (rejected / top) * plotH;
                const x = PAD.left + index * width;
                return (
                    <g key={bucket.start}>
                        <rect data-kind="kept" x={x} y={PAD.top + plotH - keptHeight} width={Math.max(0.5, width - 0.5)} height={keptHeight} fill="#7ed321" />
                        {rejected ? <rect data-kind="rejected" x={x} y={PAD.top + plotH - keptHeight - rejectedHeight} width={Math.max(0.5, width - 0.5)} height={rejectedHeight} fill="#c026d3" /> : null}
                    </g>
                );
            })}
        </Chart>
    );
}

function BarcodeReads({ barcodes }: { barcodes: NonNullable<OntRunLive['barcodes']> }) {
    const top = Math.max(1, ...barcodes.map((item) => item.basecalled_pass_read_count + item.basecalled_fail_read_count));
    const width = plotW / barcodes.length;
    return (
        <Chart title="Barcode read counts" label="Reads per barcode chart"
            legend={[{ label: 'Passed', color: '#7ed321' }, { label: 'Failed', color: '#f87171' }, { label: 'Unclassified', color: '#6b7280' }]}>
            <Axes yTop={count(top)} yLabel="" xStart={barcodes[0].barcode.replace('barcode', '')} xEnd={barcodes[barcodes.length - 1].barcode.replace('barcode', '')} />
            {barcodes.map((item, index) => {
                const passed = (item.basecalled_pass_read_count / top) * plotH;
                const failed = (item.basecalled_fail_read_count / top) * plotH;
                const x = PAD.left + index * width;
                return (
                    <g key={item.barcode}>
                        <title>{`${item.alias ?? item.barcode}: ${count(item.basecalled_pass_read_count)} passed, ${count(item.basecalled_fail_read_count)} failed`}</title>
                        <rect data-barcode={item.barcode} x={x} y={PAD.top + plotH - passed} width={Math.max(0.5, width - 1.5)} height={passed}
                            fill={item.barcode === 'unclassified' ? '#6b7280' : '#7ed321'} />
                        <rect x={x} y={PAD.top + plotH - passed - failed} width={Math.max(0.5, width - 1.5)} height={failed} fill="#f87171" />
                    </g>
                );
            })}
        </Chart>
    );
}

/** Live reads, basecalled yield, run graphs and per-barcode output for one running MinKNOW run. */
export default function OntRunLiveStats({ runId }: { runId: string }) {
    const live = useQuery({
        queryKey: ['ont-instrument-run-live', runId],
        queryFn: async () => (await fetchOntInstrumentRunLive(runId)).data,
        retry: false,
        refetchInterval: 15000,
    });
    const data: OntRunLive | undefined = live.data;
    const cell = 'px-2 py-1 text-right tabular-nums';
    if (!data?.available || !data.totals) {
        return (
            <div data-testid="ont-run-live" className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4 text-xs text-[var(--text-secondary)]">
                <h3 className="text-sm font-semibold uppercase tracking-wide">Live run {runId}</h3>
                <p className="mt-1">{live.isLoading ? 'Reading live numbers from MinKNOW…' : 'MinKNOW has no live numbers for this run yet.'}</p>
            </div>
        );
    }
    const totals = data.totals;
    const passReads = totals.basecalled_pass_read_count;
    const barcodes = [...(data.barcodes ?? [])].sort((a, b) =>
        Number(a.barcode === 'unclassified') - Number(b.barcode === 'unclassified') || a.barcode.localeCompare(b.barcode));
    return (
        <div data-testid="ont-run-live" className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4 text-xs text-[var(--text-secondary)]">
            <h3 className="text-sm font-semibold uppercase tracking-wide">Live run {runId}</h3>
            <dl className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                <div><dt>Reads</dt><dd className="text-base font-semibold text-[var(--text-primary)]">{count(totals.read_count)}</dd></div>
                <div><dt>Estimated bases</dt><dd className="text-base font-semibold text-[var(--text-primary)]">{bases(totals.estimated_selected_bases)}</dd></div>
                {data.basecalling_enabled ? <>
                    <div><dt>Basecalled, passed</dt><dd className="text-base font-semibold text-[var(--text-primary)]">{count(passReads)} reads, {bases(totals.basecalled_pass_bases)}</dd></div>
                    <div><dt>Basecalled, failed</dt><dd className="text-base font-semibold text-[var(--text-primary)]">{count(totals.basecalled_fail_read_count)} reads, {bases(totals.basecalled_fail_bases)}</dd></div>
                </> : <div className="sm:col-span-2"><dt>Basecalling</dt><dd className="text-[var(--text-primary)]">Off for this run.</dd></div>}
            </dl>
            {data.basecalling_enabled && typeof data.fraction_basecalled === 'number'
                ? <p>MinKNOW has basecalled {percent(data.fraction_basecalled)} of the reads so far. A low share means basecalling is behind and will catch up after the run.</p> : null}
            {data.channels
                ? <p>Channel time in the latest full minute: {percent(data.channels.sequencing_fraction)} sequencing, {percent(data.channels.open_pore_fraction)} open pore, {percent(data.channels.other_fraction)} other.</p> : null}
            <div className="grid gap-3 lg:grid-cols-2" data-testid="ont-run-live-charts">
                {data.charts?.pore_activity.length ? <PoreActivity rows={data.charts.pore_activity} /> : null}
                {data.charts?.cumulative.length ? <CumulativeOutput points={data.charts.cumulative} /> : null}
                {data.charts?.read_lengths?.buckets.length ? <ReadLengths lengths={data.charts.read_lengths} /> : null}
                {barcodes.length ? <BarcodeReads barcodes={barcodes} /> : null}
            </div>
            {barcodes.length ? (
                <table aria-label="Reads per barcode" className="w-full border-collapse text-left">
                    <thead><tr className="border-b border-[var(--border-primary)]">
                        <th className="px-2 py-1">Barcode</th><th className="px-2 py-1">Sample</th>
                        <th className={cell}>Passed reads</th><th className={cell}>Passed bases</th><th className={cell}>Failed reads</th><th className={cell}>Share of passed reads</th>
                    </tr></thead>
                    <tbody>
                        {barcodes.map((item) => (
                            <tr key={item.barcode} className="border-b border-[var(--border-primary)] text-[var(--text-primary)]">
                                <td className="px-2 py-1 font-mono">{item.barcode}</td>
                                <td className="px-2 py-1">{item.alias ?? ''}</td>
                                <td className={cell}>{count(item.basecalled_pass_read_count)}</td>
                                <td className={cell}>{bases(item.basecalled_pass_bases)}</td>
                                <td className={cell}>{count(item.basecalled_fail_read_count)}</td>
                                <td className={cell}>{passReads ? percent(item.basecalled_pass_read_count / passReads) : ''}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            ) : data.basecalling_enabled ? <p>No reads have been assigned to a barcode yet.</p> : null}
        </div>
    );
}
