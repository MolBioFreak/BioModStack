export interface FrustraMpnnSourceBinding {
    source_sha256: string;
    normalized_pdb_sha256: string;
}

export interface FrustraMpnnResolutionBinding {
    source_artifact_sha256: string;
    normalized_pdb_sha256: string;
    structure_map_sha256: string;
}

/** Original source and normalized model input are distinct byte identities. */
export function assertFrustraMpnnSourceBinding(
    resultSourceSha256: string,
    normalizedArtifactSha256: string,
    map: FrustraMpnnSourceBinding,
    mapArtifactSha256: string,
    resolution?: FrustraMpnnResolutionBinding | null,
): string {
    if (normalizedArtifactSha256 !== map.normalized_pdb_sha256) {
        throw new Error('normalized_structure_hash_conflict: structure and map refer to different normalized inputs.');
    }
    if (resolution && (
        resolution.source_artifact_sha256 !== map.source_sha256
        || resolution.normalized_pdb_sha256 !== map.normalized_pdb_sha256
        || resolution.structure_map_sha256 !== mapArtifactSha256
    )) {
        throw new Error('source_binding_conflict: settings and structure map refer to different source normalization.');
    }
    if (resultSourceSha256 !== map.source_sha256 && resultSourceSha256 !== map.normalized_pdb_sha256) {
        throw new Error('source_hash_conflict: result does not refer to the mapped source or normalized input.');
    }
    return map.source_sha256;
}

export interface TerminalSourceArtifactIdentity {
    sha256: string | null;
}

export function assertTerminalSourceAuthority(
    terminalSource: TerminalSourceArtifactIdentity | null | undefined,
    persistedSourceSha256: string,
): void {
    if (terminalSource && terminalSource.sha256 !== persistedSourceSha256) {
        throw new Error('terminal_source_hash_conflict: result and terminal source SHA-256 authorities disagree.');
    }
}
