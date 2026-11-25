export type ModificationMode = 'de_novo_design' | 'redesign_structure' | 'resequence';

export function normalizeModificationMode(value: unknown): string | undefined {
    if (typeof value !== 'string') return undefined;
    if (value === 'rfd3_iteration' || value === 'rfd3_local_redesign') return 'redesign_structure';
    return value;
}

export interface DeNovoModificationModeCard {
    id: ModificationMode;
    label: string;
    description: string;
    cardClassName: string;
    labelClassName: string;
}

export const DE_NOVO_MODIFICATION_MODE_CARDS: DeNovoModificationModeCard[] = [
    {
        id: 'de_novo_design',
        label: 'Generate',
        description: 'Generate new proteins with the selected engine’s native controls.',
        cardClassName: 'border-[var(--border-primary)] bg-[var(--card-bg)] hover:border-[var(--accent-primary)]',
        labelClassName: 'text-[var(--text-primary)]',
    },
    {
        id: 'redesign_structure',
        label: 'Redesign structure',
        description: 'Load or simulate a complex, select residue roles in Mol*, then redesign with RFD4-Proteina or run downstream sequence design and validation.',
        cardClassName: 'border-[var(--border-primary)] bg-[var(--card-bg)] hover:border-[var(--accent-primary)]',
        labelClassName: 'text-[var(--text-primary)]',
    },
    {
        id: 'resequence',
        label: 'Resequence',
        description: 'Resequence a design from any generator with a chosen engine and residue selection, then validate it.',
        cardClassName: 'border-[var(--border-primary)] bg-[var(--card-bg)] hover:border-[var(--accent-primary)]',
        labelClassName: 'text-[var(--text-primary)]',
    },
];
