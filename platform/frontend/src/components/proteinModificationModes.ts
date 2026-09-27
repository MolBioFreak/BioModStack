export type ModificationMode = 'de_novo_design' | 'rfd3_iteration' | 'shape_blueprint';

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
        id: 'rfd3_iteration',
        label: 'Redesign structure',
        description: 'Load or simulate a complex, select residue roles in Mol*, then choose native RFD3 output or downstream sequence design and validation.',
        cardClassName: 'border-[var(--border-primary)] bg-[var(--card-bg)] hover:border-[var(--accent-primary)]',
        labelClassName: 'text-[var(--text-primary)]',
    },
    {
        id: 'shape_blueprint',
        label: 'Shape',
        description: 'Author a geometry blueprint using the existing shape workbench.',
        cardClassName: 'border-[var(--border-primary)] bg-[var(--card-bg)] hover:border-[var(--accent-primary)]',
        labelClassName: 'text-[var(--text-primary)]',
    },
];
