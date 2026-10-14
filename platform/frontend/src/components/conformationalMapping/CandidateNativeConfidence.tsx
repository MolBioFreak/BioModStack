import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { cmArtifactUrl, type CmArtifact } from './conformationalMappingApi';
import type { CmCandidate } from './conformationalMappingSemantics';

const object = (value: unknown): Record<string, unknown> | null =>
    value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;

/** Only scalar interpretation is shared; backend sidecar schemas are not interchangeable. */
export function candidateConfidenceSummary(backend: string, candidate: CmCandidate, payload: unknown) {
    const body = object(payload);
    if (!body) throw new Error('Native confidence payload is not an object.');
    if (backend === 'confornets') {
        const coordinates = object(body.backend_coordinates);
        if (body.schema_name !== 'cm_confornets_confidence' || body.schema_version !== 1
            || body.candidate_id !== candidate.candidate_id || !coordinates
            || Object.keys(coordinates).length !== Object.keys(candidate.backend_coordinates).length
            || Object.entries(candidate.backend_coordinates).some(([key, value]) => coordinates[key] !== value)
            || typeof body.status !== 'string' || !object(body.metrics)) {
            throw new Error('ConforNets confidence schema or candidate identity does not match.');
        }
        return { metrics: body.status === 'computed' ? body.metrics as Record<string, unknown> : {}, status: body.status,
            reason: typeof body.reason === 'string' ? body.reason : null };
    }
    if (backend !== 'protenix_v2_ensemble' || 'schema_name' in body || !['plddt', 'gpde', 'ptm', 'iptm', 'ranking_score'].some(key => typeof body[key] === 'number')) {
        throw new Error('Native Protenix summary confidence is unavailable in this payload.');
    }
    return { metrics: body, status: 'retained native summary', reason: null };
}

const descriptors = [
    ['plddt', 'pLDDT', 'Native summary, 0–100; Protenix uses the model-atom mean.'],
    ['gpde', 'gPDE', 'Native global predicted distance error, not PAE. Protenix uses contact weighting.'],
    ['ptm', 'pTM', 'Native predicted TM score, 0–1.'],
    ['iptm', 'iPTM', 'Native interface predicted TM score, 0–1.'],
    ['ranking_score', 'Native ranking score', 'Producer ranking, not a calibrated probability; not clamped or comparable across backends.'],
] as const;

export function CandidateNativeConfidence({ requestId, backend, candidate, artifacts, artifactUrl = cmArtifactUrl }: {
    requestId: string;
    backend: string;
    candidate: CmCandidate;
    artifacts: CmArtifact[];
    artifactUrl?: typeof cmArtifactUrl;
}) {
    const [open, setOpen] = useState(false);
    const candidateArtifacts = artifacts.filter(artifact => artifact.candidate_id === candidate.candidate_id
        && candidate.sidecar_paths.includes(artifact.relative_path));
    const summaries = candidateArtifacts.filter(artifact => artifact.role === 'confidence_json');
    const summary = summaries.length === 1 ? summaries[0] : null;
    const supported = backend === 'protenix_v2_ensemble' || backend === 'confornets';
    const confidence = useQuery({
        queryKey: ['cm-candidate-native-confidence', requestId, candidate.candidate_id, candidate.authoritative_structure_sha256, summary?.artifact_id],
        queryFn: async () => candidateConfidenceSummary(backend, candidate, (await api.get(artifactUrl(requestId, summary!.artifact_id))).data),
        enabled: open && supported && Boolean(summary),
        retry: false,
        staleTime: Infinity,
    });
    return <details className="rounded-xl border border-slate-800 bg-slate-900/70 p-4" onToggle={event => setOpen(event.currentTarget.open)}>
        <summary className="cursor-pointer text-sm font-medium text-white">Selected candidate · {backend === 'protenix_v2_ensemble' ? 'Protenix' : backend === 'confornets' ? 'ConforNets' : 'Imported structure'} native confidence</summary>
        {open && <div className="mt-3 space-y-3 text-xs text-slate-400">
            <p>Independent saved hypothesis. Sample/run indices and saved optimization steps are not physical time, kinetics, occupancy or population weights.</p>
            <p className="break-all font-mono">{candidate.candidate_id} · coordinates SHA-256 {candidate.authoritative_structure_sha256}</p>
            {backend === 'confornets' && <p>ConforNets reports scalar gPDE and optional saved PDE tensors, not PAE. Its full_data_json role can contain evaluation evidence, not a confidence matrix.</p>}
            {backend === 'protenix_v2_ensemble' && <p>Protenix gPDE is distinct from token-pair PAE. Native full-data and chain fields retain their producer axes and units; no molecular identities are inferred here.</p>}
            {!supported && <p>Imported coordinates do not establish predictor confidence.</p>}
            {supported && !summary && <p>Candidate-bound native confidence summary unavailable{summaries.length > 1 ? ' (ambiguous artifact identity)' : ''}. Coordinate review remains available.</p>}
            {confidence.isLoading && <p>Loading selected candidate confidence…</p>}
            {confidence.isError && <p role="status">Native confidence unavailable: {confidence.error instanceof Error ? confidence.error.message : 'artifact read failed'}. Coordinate review remains available.</p>}
            {confidence.data && <>
                <p>Evidence status: <span className="text-slate-200">{confidence.data.status}</span>{confidence.data.reason && ` · ${confidence.data.reason}`}</p>
                <dl className="grid gap-3 sm:grid-cols-3">{descriptors.map(([key, label, note]) => {
                    const value = confidence.data.metrics[key];
                    return <div key={key} className="rounded-lg border border-slate-800 p-3" title={note}><dt>{label}</dt><dd className="mt-1 font-mono text-white">{typeof value === 'number' && Number.isFinite(value) ? String(value) : 'Unavailable'}</dd><p className="mt-1 text-[10px]">{note}</p></div>;
                })}</dl>
                <details><summary className="cursor-pointer">Native summary fields (unaltered units and producer indices)</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words">{JSON.stringify(confidence.data.metrics, null, 2)}</pre></details>
            </>}
            <div className="space-y-2">{candidateArtifacts.filter(artifact => ['confidence_json', 'full_data_json'].includes(artifact.role)).map(artifact => <a key={artifact.artifact_id} href={artifactUrl(requestId, artifact.artifact_id)} className="block break-all text-orange-300">
                {artifact.role === 'confidence_json' ? 'Native confidence summary' : backend === 'confornets' ? 'Native evaluation sidecar (not PAE)' : 'Native full-data sidecar (producer axes)'} · {artifact.relative_path}
                <span className="block text-[10px] text-slate-500">SHA-256 {artifact.sha256} · {artifact.bytes} bytes</span>
            </a>)}</div>
        </div>}
    </details>;
}
