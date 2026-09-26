import { useState } from 'react';
import MolstarViewer from './MolstarViewer';
import { useQuery } from '@tanstack/react-query';

import { fetchRFD3Generation, type RFD3GenerationRange, type RFD3GenerationReadModel } from '../lib/api';

interface RFD3GenerationResultsPaneProps {
    jobId: string;
}

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

// Same bounded first/previous/next/last pattern as the results table.
export function NativeCandidatePagination({ page, total, onPage }: { page: number; total: number; onPage: (page: number) => void }) {
    const pages = Math.max(1, Math.ceil(total / 10));
    return <nav aria-label="Candidate pages" className="flex flex-wrap items-center gap-3 p-3 text-sm text-slate-300">
        <span>{total === 0 ? 0 : (page - 1) * 10 + 1}–{Math.min(page * 10, total)} of {total} candidates</span>
        <button type="button" disabled={page === 1} onClick={() => onPage(1)}>First</button>
        <button type="button" disabled={page === 1} onClick={() => onPage(page - 1)}>Previous</button>
        <span>Page {page} / {pages}</span>
        <button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
        <button type="button" disabled={page >= pages} onClick={() => onPage(pages)}>Last</button>
    </nav>;
}

export function RFD3GenerationResultsContent({ result }: { result: RFD3GenerationReadModel }) {
    const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
    const [requestedPage, setPage] = useState(1);
    const page = Math.min(requestedPage, Math.max(1, Math.ceil(result.candidates.length / 10)));
    const visibleCandidates = result.candidates.slice((page - 1) * 10, page * 10);
    const selectedCandidate = selectedCandidateId === null ? result.candidates[0]
        : result.candidates.find((candidate) => candidate.candidate_id === selectedCandidateId);
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
                <p className="mt-3 text-xs text-slate-400">Accepted is the producer’s status: by default it checks requested length bounds; a supplied accepted-candidate set may use different criteria. It does not establish folding or binding validation.</p>
            </section>



            <section className="overflow-hidden rounded-2xl border border-slate-700 bg-slate-900/50">
                <div className="border-b border-slate-800 p-5">
                    <h3 className="text-lg font-semibold text-white">Candidates</h3>
                </div>
                <NativeCandidatePagination page={page} total={result.candidates.length} onPage={setPage} />
                <div className="max-h-80 overflow-auto">
                    <table className="w-full min-w-[760px] text-left text-sm">
                        <thead className="bg-slate-950/60 text-xs uppercase text-slate-500">
                            <tr><th className="px-4 py-3">Candidate</th><th className="px-4 py-3">Status</th><th className="px-4 py-3">Length</th><th className="px-4 py-3">Radius (Å)</th><th className="px-4 py-3">Helix</th><th className="px-4 py-3">Strand</th><th className="px-4 py-3">Structure</th></tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800">
                            {visibleCandidates.map((candidate) => (
                                <tr key={candidate.candidate_id} className="text-slate-300">
                                    <td className="px-4 py-3 font-mono text-emerald-200"><button type="button" aria-pressed={candidate === selectedCandidate} onClick={() => setSelectedCandidateId(candidate.candidate_id)}>{candidate.candidate_id}</button></td>
                                    <td className="px-4 py-3">{candidate.status}</td>
                                    <td className="px-4 py-3">{candidate.length}</td>
                                    <td className="px-4 py-3">{formatNumber(candidate.radius)}</td>
                                    <td className="px-4 py-3">{formatNumber(candidate.helix_count, 0)}</td>
                                    <td className="px-4 py-3">{formatNumber(candidate.strand_count, 0)}</td>
                                    <td className="px-4 py-3"><a className="text-cyan-300 hover:text-cyan-100" href={candidate.structure_url} download>Download mmCIF</a></td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                {selectedCandidate && <div className="border-t border-slate-800 p-5">
                    <h3 className="mb-3 text-lg font-semibold text-white">{selectedCandidate.candidate_id}</h3>
                    <MolstarViewer structureUrl={selectedCandidate.structure_url} format="cif" label={selectedCandidate.candidate_id} artifactJobId={result.job_id} height={500} />
                </div>}
                {selectedCandidateId !== null && !selectedCandidate && <p role="alert" className="p-6 text-sm text-amber-200">Selected candidate is unavailable. Choose another candidate.</p>}
                {result.candidates.length === 0 && <div className="p-6 text-sm text-slate-400">No generated candidates are available.</div>}
            </section>
            <details className="rounded-2xl border border-slate-700 bg-slate-900/50 p-5">
                <summary className="cursor-pointer text-lg font-semibold text-white">Producer aggregate metrics</summary>
                <p className="mt-1 text-xs text-slate-500">Minimum / mean / maximum across the complete generated set.</p>
                <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    <SummaryCard label="Length" value={formatRange(result.aggregates.length, ' residues')} />
                    <SummaryCard label="Radius" value={formatRange(result.aggregates.radius, ' Å')} />
                    <SummaryCard label="Helix" value={formatRange(result.aggregates.helix)} />
                    <SummaryCard label="Strand" value={formatRange(result.aggregates.strand)} />
                </div>
            </details>
            <details className="rounded-xl border border-slate-700 p-4">
                <summary className="cursor-pointer text-sm text-slate-300">Run details</summary>
                <p className="mt-3 break-all font-mono text-xs text-slate-400">Result manifest: {result.result_manifest_sha256}</p>
                <pre className="mt-3 max-h-80 overflow-auto text-xs text-slate-400">{JSON.stringify(result.request, null, 2)}</pre>
            </details>
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
    return <RFD3GenerationResultsContent key={jobId} result={result} />;
}

export default RFD3GenerationResultsPane;