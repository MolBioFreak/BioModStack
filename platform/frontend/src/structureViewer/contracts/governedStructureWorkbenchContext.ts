import type { Design, ViewerVolumeInventoryV1 } from '../../lib/api.js';

/**
 * Exact viewer identities for a result whose structure and spatial resources are
 * governed by the same completed job. The selected result's structure hash must
 * match exactly one supplied registration before its document identity is used.
 */
export interface GovernedStructureWorkbenchContext {
    readonly jobId: string;
    readonly artifactJobId: string;
    readonly structureDocumentId: string;
}

interface ResolveGovernedStructureWorkbenchContextInput {
    readonly activeJobId: string | null | undefined;
    readonly design: Pick<Design, 'job_id' | 'provenance' | 'review_artifact_manifest'> | null | undefined;
    readonly inventory: Pick<ViewerVolumeInventoryV1, 'jobId' | 'registrations'> | null | undefined;
}

const SHA256 = /^[0-9a-f]{64}$/i;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const structureHashFromResult = (design: ResolveGovernedStructureWorkbenchContextInput['design']): string | null => {
    const candidates = [
        design?.provenance?.sha256,
        (design?.review_artifact_manifest?.artifacts?.structure as Record<string, unknown> | undefined)?.sha256,
    ];
    for (const candidate of candidates) {
        if (typeof candidate === 'string' && SHA256.test(candidate)) return candidate.toLowerCase();
    }
    return null;
};

export function resolveGovernedStructureWorkbenchContext({
    activeJobId,
    design,
    inventory,
}: ResolveGovernedStructureWorkbenchContextInput): GovernedStructureWorkbenchContext | null {
    if (!activeJobId || !design || !inventory) return null;
    if (design.job_id !== activeJobId || inventory.jobId !== activeJobId) return null;

    const structureSha256 = structureHashFromResult(design);
    if (!structureSha256) return null;

    const registrations = inventory.registrations.filter((registration) => (
        UUID.test(registration.structureDocumentId)
        && SHA256.test(registration.structureSha256)
        && registration.structureSha256.toLowerCase() === structureSha256
    ));
    if (registrations.length !== 1) return null;

    return {
        jobId: inventory.jobId,
        artifactJobId: inventory.jobId,
        structureDocumentId: registrations[0]!.structureDocumentId,
    };
}
