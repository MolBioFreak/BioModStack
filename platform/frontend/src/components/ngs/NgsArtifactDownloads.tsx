import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { describeNgsError, withAlignmentAccessRecovery } from '../../lib/ngsAlignmentSession';

interface NgsPackageArtifact {
    artifact_id: string | null;
    kind: string;
    source: string;
    filename?: string;
    state: string;
    url: string | null;
    size_bytes: number | null;
    unavailable_reason?: string | null;
}

/** Native job-scoped links: the generic files route refuses governed NGS output. */
export function NgsArtifactDownloads({ jobId }: { jobId: string }) {
    const query = useQuery({
        queryKey: ['ngs-package-artifacts', jobId],
        queryFn: () => withAlignmentAccessRecovery(jobId, async () =>
            (await api.get<{ job_id: string; artifacts: NgsPackageArtifact[] }>(`/api/jobs/${encodeURIComponent(jobId)}/ngs-artifacts`)).data),
        retry: false,
        staleTime: 30_000,
    });
    return <div className="space-y-2" aria-label="NGS artifact downloads">
        {query.isLoading && <p>Loading job-scoped downloads…</p>}
        {query.isError && <p role="alert">{describeNgsError(query.error, 'Job-scoped downloads could not be loaded.')} <button type="button" onClick={() => void query.refetch()}>Retry downloads</button></p>}
        {query.data?.artifacts.map((artifact, index) => <div key={artifact.artifact_id || `${artifact.source}:${artifact.kind}:${index}`} className="text-xs">
            {artifact.state === 'present' && artifact.url
                ? <a href={artifact.url} className="text-sky-300 underline">{artifact.filename || `${artifact.source} · ${artifact.kind}`}</a>
                : <span>{artifact.filename || artifact.kind}: {artifact.unavailable_reason || artifact.state}</span>}
        </div>)}
    </div>;
}
