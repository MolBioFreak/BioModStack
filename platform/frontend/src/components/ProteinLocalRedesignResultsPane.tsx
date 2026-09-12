import { useState } from 'react';
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
    const [expanded, setExpanded] = useState(false);
    const resultQuery = useQuery({
        queryKey: ['protein-local-redesign-results', job.id],
        queryFn: () => fetchProteinLocalRedesignResults(job.id).then((response) => response.data),
        enabled: expanded && Boolean(job.id),
        retry: false,
        staleTime: 30_000,
    });
    const surface = resultQuery.data;

    return (
        <details className="rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-3" onToggle={(event) => setExpanded(event.currentTarget.open)}>
            <summary className="cursor-pointer text-sm text-slate-300">Protein Local Redesign files</summary>
            {expanded && resultQuery.isLoading && <p className="mt-3 text-sm text-slate-400">Loading native files…</p>}
            {expanded && resultQuery.isError && <p role="alert" className="mt-3 text-sm text-amber-200">Native file inventory could not be loaded. <button type="button" className="underline" onClick={() => resultQuery.refetch()}>Retry</button></p>}
            {expanded && surface && <div className="mt-3 space-y-4 text-xs text-slate-300">
                <section>
                    <h3 className="font-semibold text-slate-200">Source files</h3>
                    <div className="mt-2 flex flex-wrap gap-3">{surface.source.artifacts.map((artifact) => <a key={artifact.artifact_id} href={artifact.content_url} target="_blank" rel="noreferrer" className="underline">{artifact.label}</a>)}</div>
                </section>
                {surface.tabs.map((tab) => <section key={tab.id}>
                    <h3 className="font-semibold text-slate-200">{tab.label} · {tab.count} outputs</h3>
                    {tab.items.map((item) => <div key={item.item_id} className="mt-2 border-t border-slate-800 pt-2">
                        <div>{item.candidate_label}{item.sample_index == null ? '' : ` · sample ${item.sample_index}`}</div>
                        <div className="mt-1 flex flex-wrap gap-3">{[item.structure.artifact_id, item.metrics_artifact, item.confidence_artifact, item.msa_artifact, item.native_metadata_artifact]
                            .filter((id, index, ids) => typeof id === 'string' && ids.indexOf(id) === index)
                            .map((id) => surface.artifacts.find((artifact) => artifact.artifact_id === id))
                            .map((artifact) => artifact && <a key={artifact.artifact_id} href={artifact.content_url} target="_blank" rel="noreferrer" className="underline">{artifact.relative_path.split('/').pop()}</a>)}</div>
                    </div>)}
                </section>)}
            </div>}
        </details>
    );
}
