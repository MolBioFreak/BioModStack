import { fetchMolecularRevisionSummaries } from '../../../lib/api';

export interface CurrentRevisionRef {
    sequenceId: string;
    revision_id: string;
    content_sha256: string;
    content_length: number;
}

/** Current revision of a saved molecule; the current one is not always on the first page. */
export async function fetchCurrentRevisionRef(sequenceId: string, signal?: AbortSignal): Promise<CurrentRevisionRef | null> {
    for (let offset = 0; offset < 5000; offset += 50) {
        const page = await fetchMolecularRevisionSummaries(sequenceId, 50, offset, signal);
        const current = page.find((item) => item.is_current);
        if (current) {
            return { sequenceId, revision_id: current.revision_id, content_sha256: current.content_sha256, content_length: current.content_length };
        }
        if (page.length < 50) break;
    }
    return null;
}
