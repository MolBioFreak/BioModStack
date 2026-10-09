import { useQuery } from '@tanstack/react-query';
import { api } from './api';
import { withAlignmentAccessRecovery } from './ngsAlignmentSession';

export interface NgsPackageArtifact {
    artifact_id: string | null;
    kind: string;
    source: string;
    filename?: string;
    state: string;
    url: string | null;
    size_bytes: number | null;
    unavailable_reason?: string | null;
}

export function useNgsArtifacts(jobId: string | null, enabled = true) {
    return useQuery({
        queryKey: ['ngs-package-artifacts', jobId],
        queryFn: () => withAlignmentAccessRecovery(jobId!, async () =>
            (await api.get<{ job_id: string; artifacts: NgsPackageArtifact[] }>(`/api/jobs/${encodeURIComponent(jobId!)}/ngs-artifacts`)).data),
        enabled: Boolean(jobId) && enabled,
        retry: false,
        staleTime: 30_000,
    });
}

/** Resolve only a public catalog URL, never construct a generic file route. */
export function ngsArtifactUrl(path: string, jobId: string | undefined, artifacts: NgsPackageArtifact[] = []): string | null {
    if (!jobId) return null;
    const prefix = `/api/jobs/${encodeURIComponent(jobId)}/ngs-artifacts/`;
    const present = artifacts.filter(a => a.state === 'present' && a.url?.startsWith(prefix));
    const normalized = path.replace(/\\/g, '/');
    const exact = present.filter(a => a.url === path || (a.filename && (normalized === a.filename || normalized.endsWith(`/${a.filename}`))));
    if (exact.length === 1) return exact[0].url;
    // Older catalog owners publish a basename only. Never guess between duplicates.
    const basename = normalized.split('/').pop();
    const matches = present.filter(a => a.filename?.split('/').pop() === basename);
    return matches.length === 1 ? matches[0].url : null;
}
