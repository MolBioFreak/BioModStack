import { useQuery } from '@tanstack/react-query';
import { getProject } from './projectManager';
import { fetchBinderCandidateData, type ProjectCandidateData } from './binderResultFamily';
export { buildProjectCandidateData, candidateHref, validationKind } from './binderResultFamily';
export type { CandidateSource, CandidateAttempt, ProjectCandidate, ProjectCandidateData, ValidationKind } from './binderResultFamily';
const text = (v: unknown): string => typeof v === 'string' ? v : '';
const object = (v: unknown): Record<string, unknown> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {};
const rows = (v: unknown): Record<string, unknown>[] => Array.isArray(v) ? v.map(object) : [];
export function projectSourceReferences(project: unknown): { id: string; label: string }[] {
    const found = new Map<string, { id: string; label: string }>();
    for (const ref of rows(object(object(project).payload).external_references)) {
        const match = /^\/designs\/([a-zA-Z0-9-]+)$/.exec(text(ref.value));
        if (match) found.set(match[1], { id: match[1], label: text(ref.label) });
    }
    return [...found.values()];
}
export async function fetchProjectCandidateData(projectId: string, signal?: AbortSignal): Promise<ProjectCandidateData> {
    return fetchBinderCandidateData(projectSourceReferences(await getProject(projectId, signal)), signal);
}
export function useProjectCandidateData(projectId: string, enabled: boolean) {
    return useQuery({ queryKey: ['project-candidate-overview', projectId], queryFn: ({ signal }) => fetchProjectCandidateData(projectId, signal),
        enabled: enabled && Boolean(projectId), retry: false, staleTime: Infinity, refetchInterval: false, refetchOnWindowFocus: false, refetchOnReconnect: false });
}
