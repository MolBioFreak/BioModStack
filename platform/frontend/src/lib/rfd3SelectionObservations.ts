import type { Rfd3SourceSelection } from './rfd3SourceSelection';
import { projectRfd3SourceSelection } from './rfd3SourceSelection';

export type Rfd3SelectionObservation = {
    value: unknown; identities: Rfd3SourceSelection['identities'];
    source: { inputPath: string; sourceModel?: number; sourceMapping?: string; originalSha256: string; consumedSha256: string };
};
export type Rfd3SelectionObservations = Record<string, Rfd3SelectionObservation>;
export type Rfd3InspectionSource = Omit<Rfd3SourceSelection, 'identities'>;
const atomFields = new Set(['select_fixed_atoms', 'select_hbond_donor', 'select_hbond_acceptor', 'select_buried', 'select_exposed', 'select_partially_buried']);
const fingerprint = (visual: Rfd3InspectionSource) => {
    const receipt = visual.retainedBinding?.receipt;
    if (!receipt || typeof receipt.original_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(receipt.original_sha256) || !/^[a-f0-9]{64}$/.test(receipt.sha256)) return undefined;
    return { inputPath: visual.inputPath, sourceModel: visual.sourceModel, sourceMapping: visual.sourceMapping,
        originalSha256: receipt.original_sha256, consumedSha256: receipt.sha256 };
};

/** Retain only a proven Apply observation, outside native scientific settings. */
export function observeRfd3Selection(field: string, value: unknown, visual: Rfd3SourceSelection): Rfd3SelectionObservation | undefined {
    const source = fingerprint(visual), projected = projectRfd3SourceSelection(visual);
    if (!source || projected.issue || JSON.stringify(atomFields.has(field) ? projected.atoms : projected.residues) !== JSON.stringify(value)) return undefined;
    return { value, identities: visual.identities, source };
}

/** Cold readback uses the current verified receipt, not retained object references. */
export function replayRfd3Observation(field: string, value: unknown, observation: Rfd3SelectionObservation | undefined, source: Rfd3InspectionSource | undefined): Rfd3SourceSelection | undefined {
    if (!source || !observation || !Array.isArray(observation.identities) || observation.identities.some(identity => !identity || typeof identity !== 'object') || JSON.stringify(observation.value) !== JSON.stringify(value) ||
        JSON.stringify(observation.source) !== JSON.stringify(fingerprint(source))) return undefined;
    const visual = { ...source, identities: observation.identities.map(identity => ({ ...identity, documentId: source.source.documentId })) };
    return observeRfd3Selection(field, value, visual) ? visual : undefined;
}
