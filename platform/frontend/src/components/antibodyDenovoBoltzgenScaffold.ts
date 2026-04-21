export interface BoltzgenScaffoldFramework {
    pdbCode?: string | null;
    sequence?: string | null;
    cdrH3Length?: number | null;
}

export interface DeriveBoltzgenScaffoldSelectionUpdateArgs {
    framework?: BoltzgenScaffoldFramework | null;
    viewReferenceStructure?: boolean;
}

export interface BoltzgenScaffoldSelectionUpdate {
    nextFrameworkSequence: string | null;
    nextCdrH3Length: string | null;
    shouldOpenReferencePreview: boolean;
    referencePdbUrl: string | null;
}

export const buildRcsbPdbDownloadUrl = (pdbCode?: string | null): string | null => {
    const normalized = String(pdbCode || '').trim().toUpperCase();
    if (!normalized) {
        return null;
    }
    return `https://files.rcsb.org/download/${normalized}.pdb`;
};

export const deriveBoltzgenScaffoldSelectionUpdate = ({
    framework,
    viewReferenceStructure = false,
}: DeriveBoltzgenScaffoldSelectionUpdateArgs): BoltzgenScaffoldSelectionUpdate => {
    const nextFrameworkSequence = typeof framework?.sequence === 'string' && framework.sequence.trim()
        ? framework.sequence
        : null;
    const cdrH3Length = Number(framework?.cdrH3Length);
    const nextCdrH3Length = Number.isFinite(cdrH3Length) && cdrH3Length > 0
        ? `${Math.max(8, cdrH3Length - 3)}-${cdrH3Length + 3}`
        : null;
    const referencePdbUrl = viewReferenceStructure ? buildRcsbPdbDownloadUrl(framework?.pdbCode) : null;

    return {
        nextFrameworkSequence,
        nextCdrH3Length,
        shouldOpenReferencePreview: Boolean(referencePdbUrl),
        referencePdbUrl,
    };
};

export const resolveBoltzgenReferencePreviewEnabled = (saved?: Record<string, unknown>): boolean =>
    saved?.boltzgen_view_reference_structure === true;
