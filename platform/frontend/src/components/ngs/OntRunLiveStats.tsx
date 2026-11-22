import { useQuery } from '@tanstack/react-query';
import { fetchOntInstrumentRunLive, type OntRunLive } from '../../lib/api';

const count = (value: number) => value.toLocaleString('en-US');

const bases = (value: number) => {
    if (value >= 1e9) return `${(value / 1e9).toFixed(2)} Gb`;
    if (value >= 1e6) return `${(value / 1e6).toFixed(2)} Mb`;
    if (value >= 1e3) return `${(value / 1e3).toFixed(1)} kb`;
    return `${value} b`;
};

const percent = (value: number) => `${Math.round(value * 100)}%`;

/** Live reads, basecalled yield, channel use and per-barcode output for one running MinKNOW run. */
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
            {barcodes.length ? (
                <table aria-label="Reads per barcode" className="w-full border-collapse text-left">
                    <thead><tr className="border-b border-[var(--border-primary)]">
                        <th className="px-2 py-1">Barcode</th><th className="px-2 py-1">Sample</th>
                        <th className={cell}>Passed reads</th><th className={cell}>Passed bases</th><th className={cell}>Failed reads</th><th className={cell}>Share of passed</th>
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
