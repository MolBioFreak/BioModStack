import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchRFD3LocalRedesign } from '../lib/api';
import type { RFD3LocalRedesignReadModel } from '../lib/api';
import MolstarViewer from './MolstarViewer';
import { resolveRFD3LocalRedesignRequestView } from './rfd3LocalRedesignResultsView';

interface RFD3LocalRedesignResultsPaneProps {
    jobId: string;
}

const formatValue = (value: unknown): string => {
    if (value == null) return '—';
    if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4);
    if (typeof value === 'string') return value;
    return JSON.stringify(value);
};

const metricLabels: Record<string, string> = {
    ca_rmsd_to_input: 'Cα RMSD to input',
    backbone_rmsd: 'Backbone RMSD',
    insertion_rmsd: 'Insertion RMSD',
    summary_confidences: 'Summary confidence',
    diffused_index_map: 'Diffused index map',
    hbond_metrics: 'Hydrogen-bond metrics',
};

export function RFD3LocalRedesignResultsPane({ jobId }: RFD3LocalRedesignResultsPaneProps) {
    const resultQuery = useQuery({
        queryKey: ['rfd3-local-redesign', jobId],
        queryFn: () => fetchRFD3LocalRedesign(jobId),
        enabled: Boolean(jobId),
        retry: false,
    });
    const result = resultQuery.data?.data;
    const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
    const [selectedTrajectoryRole, setSelectedTrajectoryRole] = useState<'denoised_trajectory' | 'noisy_trajectory'>('denoised_trajectory');
    const requestView = resolveRFD3LocalRedesignRequestView(result);
    const request = requestView.request;
    const fixedAtoms = request?.rfd3 && typeof request.rfd3 === 'object'
        ? (request.rfd3 as Record<string, unknown>).select_fixed_atoms
        : undefined;
    const artifactsByCandidate = useMemo(() => {
        const map = new Map<string, RFD3LocalRedesignReadModel['artifacts']>();
        if (!result) return map;
        result.artifacts.forEach((artifact) => {
            if (!artifact.candidate_id) return;
            const existing = map.get(artifact.candidate_id) || [];
            existing.push(artifact);
            map.set(artifact.candidate_id, existing);
        });
        return map;
    }, [result]);

    if (resultQuery.isLoading) {
        return <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-6 text-sm text-slate-400">Loading the RFD3 local-redesign data plane…</div>;
    }
    if (resultQuery.isError || !result) {
        return <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-6 text-sm text-amber-100">The typed RFD3 local-redesign read model is not available yet.</div>;
    }

    const artifactUrl = (artifactId: string) => `/api/jobs/${jobId}/rfd3-local-redesign/artifacts/${artifactId}`;
    const rfd3 = request?.rfd3 && typeof request.rfd3 === 'object' ? request.rfd3 as Record<string, unknown> : {};
    const execution = request?.execution && typeof request.execution === 'object' ? request.execution as Record<string, unknown> : {};
    const activeCandidateId = selectedCandidateId && result.candidates.some((candidate) => candidate.candidate_id === selectedCandidateId)
        ? selectedCandidateId
        : result.candidates[0]?.candidate_id;
    const activeStructure = result.artifacts.find(
        (artifact) => artifact.candidate_id === activeCandidateId && artifact.role === 'structure',
    );
    const activeTrajectory = result.artifacts.find(
        (artifact) => artifact.candidate_id === activeCandidateId && artifact.role === selectedTrajectoryRole,
    );
    const sourceArtifact = result.artifacts.find((artifact) => artifact.role === 'source_structure');
    const sourceFormat = sourceArtifact?.media_type.includes('mmcif') ? 'cif' : 'pdb';

    return (
        <div className="space-y-5">
            <section className="rounded-2xl border border-emerald-500/25 bg-emerald-500/5 p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-emerald-300">Native RFD3 local redesign</div>
                        <h2 className="mt-1 text-2xl font-semibold text-white">{request?.redesign_mode}</h2>
                        <p className="mt-1 text-sm text-slate-300">Sequence policy: <span className="font-mono text-emerald-200">{request?.sequence_policy}</span></p>
                    </div>
                    <div className="rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2 text-right text-xs text-slate-400">
                        <div>Status: <span className="text-white">{requestView.status || '—'}</span></div>
                        <div className="font-mono">Request {requestView.requestSha256 ? `${requestView.requestSha256.slice(0, 16)}…` : '—'}</div>
                    </div>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-4">
                    <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"><div className="text-[10px] uppercase text-slate-500">Profile</div><div className="mt-1 text-sm text-white">{requestView.profileId || '—'}</div></div>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"><div className="text-[10px] uppercase text-slate-500">Designs</div><div className="mt-1 text-sm text-white">{formatValue(execution.num_designs)}</div></div>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"><div className="text-[10px] uppercase text-slate-500">Seed</div><div className="mt-1 text-sm text-white">{formatValue(execution.seed)}</div></div>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"><div className="text-[10px] uppercase text-slate-500">Profile registry</div><div className="mt-1 font-mono text-xs text-white">{requestView.profileRegistrySha256 ? `${requestView.profileRegistrySha256.slice(0, 16)}…` : '—'}</div></div>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"><div className="text-[10px] uppercase text-slate-500">Candidates</div><div className="mt-1 text-sm text-white">{result.candidates.length}</div></div>
                </div>
            </section>

            <section className="rounded-2xl border border-slate-700 bg-slate-900/50 p-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-lg font-semibold text-white">Producer input and fixed-coordinate map</h3>
                    <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-xs text-emerald-200">Sequence design not requested</span>
                </div>
                <div className="mt-4 grid gap-4 lg:grid-cols-2">
                    <div className="space-y-2 text-sm">
                        <div><span className="text-slate-500">Input:</span> <span className="font-mono text-slate-200">{formatValue(request?.input)}</span></div>
                        <div><span className="text-slate-500">Contig dialect:</span> <span className="font-mono text-slate-200">{formatValue(request?.contig_dialect)}</span></div>
                        <div><span className="text-slate-500">Contig:</span> <span className="font-mono text-slate-200">{formatValue(rfd3.contig)}</span></div>
                        <div><span className="text-slate-500">Partial t:</span> <span className="font-mono text-slate-200">{formatValue(rfd3.partial_t)}</span></div>
                        <div><span className="text-slate-500">Ligand:</span> <span className="font-mono text-slate-200">{formatValue(rfd3.ligand)}</span></div>
                    </div>
                    <div>
                        <div className="mb-2 text-xs uppercase tracking-[0.16em] text-slate-500">select_fixed_atoms</div>
                        <pre className="max-h-48 overflow-auto rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs text-emerald-100">{formatValue(fixedAtoms)}</pre>
                    </div>
                </div>
            </section>

            <section className="rounded-2xl border border-slate-700 bg-slate-900/50 p-5">
                <div className="flex items-center justify-between gap-2"><h3 className="text-lg font-semibold text-white">Candidate metrics</h3><span className="text-xs text-slate-500">Source: native RFD3 metadata</span></div>
                <div className="mt-4 grid gap-3 xl:grid-cols-2">
                    {result.candidates.map((candidate) => {
                        const candidateArtifacts = artifactsByCandidate.get(candidate.candidate_id) || [];
                        return (
                            <article key={candidate.candidate_id} className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
                                <div className="flex items-center justify-between gap-2">
                                    <div className="font-mono text-sm text-emerald-200">{candidate.candidate_id}</div>
                                    <span className="rounded-full border border-slate-700 px-2 py-1 text-[10px] uppercase text-slate-400">{candidate.status}</span>
                                </div>
                                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                                    {Object.entries(candidate.metrics).map(([key, value]) => (
                                        <div key={key} className="rounded-lg border border-slate-800 px-3 py-2">
                                            <div className="text-[10px] uppercase text-slate-500">{metricLabels[key] || key}</div>
                                            <div className="mt-1 max-h-20 overflow-auto font-mono text-xs text-slate-200">{formatValue(value)}</div>
                                        </div>
                                    ))}
                                </div>
                                <div className="mt-3 space-y-1">
                                    {candidateArtifacts.map((artifact) => (
                                        <a key={artifact.artifact_id} href={artifactUrl(artifact.artifact_id)} className="flex items-center justify-between rounded-lg border border-slate-800 px-3 py-2 text-xs text-slate-300 hover:border-emerald-500/50 hover:text-white">
                                            <span>{artifact.role}</span><span className="font-mono text-slate-500">{artifact.sha256.slice(0, 12)}…</span>
                                        </a>
                                    ))}
                                </div>
                            </article>
                        );
                    })}
                </div>
            </section>

            <section className="rounded-2xl border border-slate-700 bg-slate-900/50 p-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-lg font-semibold text-white">Candidate structure view</h3>
                    <div className="flex flex-wrap gap-2">
                        {result.candidates.map((candidate) => (
                            <button
                                key={candidate.candidate_id}
                                type="button"
                                onClick={() => setSelectedCandidateId(candidate.candidate_id)}
                                className={`rounded-lg border px-3 py-1.5 text-xs ${candidate.candidate_id === activeCandidateId ? 'border-emerald-400/60 bg-emerald-500/15 text-emerald-100' : 'border-slate-700 text-slate-400 hover:border-slate-500'}`}
                            >
                                {candidate.candidate_id}
                            </button>
                        ))}
                    </div>
                </div>
                <div className="mt-4 overflow-hidden rounded-xl border border-slate-800">
                    <MolstarViewer
                        structureUrl={activeStructure ? artifactUrl(activeStructure.artifact_id) : undefined}
                        overlayStructures={sourceArtifact ? [{ id: 'source-input', structureUrl: artifactUrl(sourceArtifact.artifact_id), format: sourceFormat, label: 'Source input' }] : undefined}
                        format="cif"
                        height={560}
                        showSequenceTrack
                        showComplexWorkbench
                    />
                </div>
            </section>

            {result.capabilities.trajectories.requested && (
                <section className="rounded-2xl border border-slate-700 bg-slate-900/50 p-5">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                            <h3 className="text-lg font-semibold text-white">Native diffusion trajectory</h3>
                            <p className="mt-1 text-xs text-slate-500">Mol* loads the producer multi-model mmCIF trajectory for the selected candidate.</p>
                        </div>
                        {result.capabilities.trajectories.available && (
                            <div className="flex gap-2">
                                {(['denoised_trajectory', 'noisy_trajectory'] as const).map((role) => (
                                    <button
                                        key={role}
                                        type="button"
                                        onClick={() => setSelectedTrajectoryRole(role)}
                                        className={`rounded-lg border px-3 py-1.5 text-xs ${selectedTrajectoryRole === role ? 'border-cyan-400/60 bg-cyan-500/15 text-cyan-100' : 'border-slate-700 text-slate-400 hover:border-slate-500'}`}
                                    >
                                        {role === 'denoised_trajectory' ? 'Denoised' : 'Noisy'}
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                    {!result.capabilities.trajectories.available || !activeTrajectory ? (
                        <div role="status" className="mt-4 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100">
                            Requested trajectory artifacts are unavailable for this candidate.
                        </div>
                    ) : (
                        <div className="mt-4 overflow-hidden rounded-xl border border-slate-800">
                            <MolstarViewer
                                structureUrl={artifactUrl(activeTrajectory.artifact_id)}
                                format="cif"
                                height={560}
                                label={`${activeCandidateId} ${selectedTrajectoryRole}`}
                                showSequenceTrack
                            />
                        </div>
                    )}
                </section>
            )}

            <section className="rounded-2xl border border-slate-700 bg-slate-900/50 p-5">
                <h3 className="text-lg font-semibold text-white">Immutable artifacts</h3>
                <div className="mt-3 space-y-2">
                    {result.artifacts.filter((artifact) => !artifact.candidate_id).map((artifact) => (
                        <a key={artifact.artifact_id} href={artifactUrl(artifact.artifact_id)} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 px-3 py-2 text-xs text-slate-300 hover:border-emerald-500/50 hover:text-white">
                            <span>{artifact.role}</span><span className="font-mono text-slate-500">{artifact.bytes.toLocaleString()} bytes · {artifact.sha256.slice(0, 16)}…</span>
                        </a>
                    ))}
                </div>
            </section>
        </div>
    );
}

export default RFD3LocalRedesignResultsPane;
