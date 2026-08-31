import { useQuery } from '@tanstack/react-query';

import { fetchRFD3Generation, type Job, type RFD3GenerationRange, type RFD3GenerationReadModel } from '../lib/api';

interface RFD3GenerationResultsPaneProps {
    jobId: string;
}

export const isRFD3GenerationResultJob = (job: Job | null | undefined): boolean =>
    job?.model_id === 'protein_modification_experimental' && job?.mode === 'de_novo_design';

const formatNumber = (value: number | null, digits = 2): string =>
    value == null ? '—' : value.toFixed(digits);

const formatRange = (range: RFD3GenerationRange, suffix = ''): string =>
    `${formatNumber(range.min)} / ${formatNumber(range.mean)} / ${formatNumber(range.max)}${suffix}`;

const SummaryCard = ({ label, value, detail }: { label: string; value: string | number; detail?: string }) => (
    <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">{label}</div>
        <div className="mt-1 text-xl font-semibold text-white">{value}</div>
        {detail && <div className="mt-1 text-xs text-slate-500">{detail}</div>}
    </div>
);

export function RFD3GenerationResultsContent({ result }: { result: RFD3GenerationReadModel }) {
    return (
        <div className="space-y-5" data-bms-result-pane="rfd3-generation">
            <section className="rounded-2xl border border-emerald-500/25 bg-emerald-500/5 p-5">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-emerald-300">Native RFD3 de novo generation</div>
                <h2 className="mt-1 text-2xl font-semibold text-white">Generation summary</h2>
                <div className="mt-4 grid gap-3 sm:grid-cols-3">
                    <SummaryCard label="Requested" value={result.counts.requested} />
                    <SummaryCard label="Generated" value={result.counts.generated} />
                    <SummaryCard label="Accepted" value={result.counts.accepted} />
                </div>
            </section>

            <section className="rounded-2xl border border-slate-700 bg-slate-900/50 p-5">
                <h3 className="text-lg font-semibold text-white">Producer aggregate metrics</h3>
                <p className="mt-1 text-xs text-slate-500">Minimum / mean / maximum across the complete generated set.</p>
                <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    <SummaryCard label="Length" value={formatRange(result.aggregates.length, ' residues')} />
                    <SummaryCard label="Radius" value={formatRange(result.aggregates.radius, ' Å')} />
                    <SummaryCard label="Helix" value={formatRange(result.aggregates.helix)} />
                    <SummaryCard label="Strand" value={formatRange(result.aggregates.strand)} />
                </div>
            </section>

            <section className="overflow-hidden rounded-2xl border border-slate-700 bg-slate-900/50">
                <div className="border-b border-slate-800 p-5">
                    <h3 className="text-lg font-semibold text-white">Candidates</h3>
                </div>
                <div className="overflow-x-auto">
                    <table className="w-full min-w-[760px] text-left text-sm">
                        <thead className="bg-slate-950/60 text-xs uppercase text-slate-500">
                            <tr><th className="px-4 py-3">Candidate</th><th className="px-4 py-3">Status</th><th className="px-4 py-3">Length</th><th className="px-4 py-3">Radius (Å)</th><th className="px-4 py-3">Helix</th><th className="px-4 py-3">Strand</th><th className="px-4 py-3">Structure</th></tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800">
                            {result.candidates.map((candidate) => (
                                <tr key={candidate.candidate_id} className="text-slate-300">
                                    <td className="px-4 py-3 font-mono text-emerald-200">{candidate.candidate_id}</td>
                                    <td className="px-4 py-3">{candidate.status}</td>
                                    <td className="px-4 py-3">{candidate.length}</td>
                                    <td className="px-4 py-3">{formatNumber(candidate.radius)}</td>
                                    <td className="px-4 py-3">{formatNumber(candidate.helix_count, 0)}</td>
                                    <td className="px-4 py-3">{formatNumber(candidate.strand_count, 0)}</td>
                                    <td className="px-4 py-3"><a className="text-cyan-300 hover:text-cyan-100" href={candidate.structure_url}>Open structure</a></td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                {result.candidates.length === 0 && <div className="p-6 text-sm text-slate-400">No generated candidates are available.</div>}
            </section>
        </div>
    );
}

export function RFD3GenerationResultsPane({ jobId }: RFD3GenerationResultsPaneProps) {
    const resultQuery = useQuery({
        queryKey: ['rfd3-generation', jobId],
        queryFn: () => fetchRFD3Generation(jobId),
        enabled: Boolean(jobId),
        retry: false,
    });
    const result = resultQuery.data?.data;

    if (resultQuery.isLoading) {
        return <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-6 text-sm text-slate-400">Loading the RFD3 generation read model…</div>;
    }
    if (resultQuery.isError || !result || result.schema !== 'bms.rfd3.generation.read-model.v1' || result.job_id !== jobId) {
        return <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-6 text-sm text-amber-100">The typed RFD3 generation read model is not available yet.</div>;
    }
    return <RFD3GenerationResultsContent result={result} />;
}

export default RFD3GenerationResultsPane;