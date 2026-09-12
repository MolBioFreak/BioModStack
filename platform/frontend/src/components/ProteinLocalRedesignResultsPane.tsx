import { useQuery } from '@tanstack/react-query';
import { fetchProteinLocalRedesignResults, type Job } from '../lib/api';

interface ProteinLocalRedesignResultsPaneProps {
    job: Job;
}

export function isProteinLocalRedesignResultJob(job: Job | null | undefined): boolean {
    if (!job) return false;
    const modelId = String(job.model_id || '').toLowerCase();
    const mode = String(job.mode || '').toLowerCase();
    const stageFamily = String(job.stage_family || '').toLowerCase();
    const rfdMode = typeof job.params?.rfd_mode === 'string' ? job.params.rfd_mode.toLowerCase() : '';
    return modelId === 'protein_local_redesign'
        || (modelId === 'protein_modification_experimental'
            && (mode === 'region_redesign' || mode === 'local_redesign' || stageFamily.startsWith('protein_local_redesign') || rfdMode === 'protein_local_redesign'));
}

export default function ProteinLocalRedesignResultsPane({ job }: ProteinLocalRedesignResultsPaneProps) {
    const resultQuery = useQuery({
        queryKey: ['protein-local-redesign-results', job.id],
        queryFn: () => fetchProteinLocalRedesignResults(job.id).then((response) => response.data),
        enabled: Boolean(job.id),
        retry: false,
        staleTime: 30_000,
    });
    const surface = resultQuery.data;
    const statusText = resultQuery.isLoading ? 'Loading' : resultQuery.isError ? 'Unavailable' : surface?.job.status || 'Unknown';

    if (resultQuery.isLoading) {
        return <div className="rounded-2xl border border-slate-700 bg-slate-900/60 p-6 text-sm text-slate-400">Loading the BMS-owned Protein Local Redesign result surface…</div>;
    }
    if (resultQuery.isError || !surface) {
        return <div role="alert" className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-6 text-sm text-amber-100">The typed Protein Local Redesign result surface is not available for this job.</div>;
    }

    return (
        <div className="space-y-5">
            <section className="rounded-2xl border border-cyan-500/25 bg-cyan-500/5 p-5">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">BMS-owned workflow result</div>
                        <h2 className="mt-1 text-2xl font-semibold text-white">Protein Local Redesign</h2>
                        <p className="mt-1 text-sm text-slate-300">RFD3 generation, FA-MPNN sequence design, and independent structure validation.</p>
                    </div>
                    <div className="rounded-xl border border-slate-700 bg-slate-950/70 px-4 py-3 text-right text-xs text-slate-400">
                        <div>Status: <span className="font-semibold text-white">{statusText}</span></div>
                        <div>Composition <span className="font-mono text-cyan-200">{surface.composition.sha256.slice(0, 16)}…</span></div>
                        {surface.job.request_sha256 && <div>Request <span className="font-mono text-slate-300">{surface.job.request_sha256.slice(0, 16)}…</span></div>}
                    </div>
                </div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                    {surface.tabs.map((tab) => (
                        <div key={tab.id} className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                            <div className="text-[10px] uppercase tracking-[0.14em] text-slate-500">{tab.label}</div>
                            <div className="mt-1 text-xl font-semibold text-white">{tab.count}</div>
                            <div className="text-[11px] text-slate-400">{tab.candidate_count} candidates · {tab.status}</div>
                        </div>
                    ))}
                </div>
            </section>

            <details className="rounded-2xl border border-slate-700 bg-slate-900/50 p-5">
                <summary className="cursor-pointer text-sm font-semibold text-white">Workflow-native artifact inventory</summary>
                <p className="mt-2 text-xs text-slate-400">Select models and exact Designs in the shared results workbench below. This inventory retains workflow lineage and native downloads, not a second scientific viewer.</p>
                {surface.tabs.map((tab) => <section key={tab.id} className="mt-4">
                    <h3 className="text-sm text-cyan-200">{tab.label} · {tab.role}</h3>
                    {tab.items.map((item) => <div key={item.item_id} className="mt-2 border-t border-slate-800 pt-2 text-xs text-slate-300">
                        <div>{item.candidate_label} · {item.sample_index == null ? 'generation' : `sample ${item.sample_index}`} · Design {item.design_id || 'unavailable'}</div>
                        <div className="mt-1 flex flex-wrap gap-3">{[item.structure.artifact_id, item.metrics_artifact, item.confidence_artifact, item.msa_artifact, item.native_metadata_artifact]
                            .filter((id, index, ids) => typeof id === 'string' && ids.indexOf(id) === index)
                            .map((id) => surface.artifacts.find((artifact) => artifact.artifact_id === id))
                            .map((artifact) => artifact && <a key={artifact.artifact_id} href={artifact.content_url} target="_blank" rel="noreferrer" className="underline">{artifact.relative_path.split('/').pop()}</a>)}</div>
                    </div>)}
                </section>)}
            </details>

            <section className="rounded-2xl border border-slate-700 bg-slate-900/50 p-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                        <h3 className="text-lg font-semibold text-white">Source and validator receipt</h3>
                        <p className="mt-1 text-xs text-slate-500">All links resolve through the BMS-owned result contract.</p>
                    </div>
                    <div className="text-xs text-slate-400">Persisted design rows: {surface.counts.persisted_design_rows}</div>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                    {surface.source.artifacts.map((artifact) => (
                        <a key={artifact.artifact_id} href={artifact.content_url} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-700 bg-slate-950/70 px-3 py-2 text-xs text-slate-300 hover:border-cyan-400/60 hover:text-white">
                            {artifact.label} · {artifact.bytes.toLocaleString()} bytes
                        </a>
                    ))}
                </div>
                {surface.receipt && <pre className="mt-4 max-h-64 overflow-auto rounded-xl border border-slate-800 bg-slate-950 p-4 text-xs text-slate-300">{JSON.stringify(surface.receipt, null, 2)}</pre>}
            </section>
        </div>
    );
}
