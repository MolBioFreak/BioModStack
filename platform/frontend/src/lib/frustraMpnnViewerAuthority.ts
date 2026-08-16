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
