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
        label: 'De Novo Design',
        description: 'Generate new candidates with DISCO or La-Proteina.',
        cardClassName: 'border-cyan-500/40 bg-cyan-500/10 hover:border-cyan-300',
        labelClassName: 'text-cyan-100',
    },
    {
        id: 'rfd3_iteration',
        label: 'RFD3 Iteration Workbench',
        description: 'Load or simulate a complex, select residue roles in Mol*, then choose native RFD3 output or downstream sequence design and validation.',
        cardClassName: 'border-emerald-500/40 bg-emerald-500/10 hover:border-emerald-300',
        labelClassName: 'text-emerald-100',
    },
    {
        id: 'shape_blueprint',
        label: 'Shape Blueprint',
        description: 'Immutable geometry → RFD3 Cα shape-transfer → conditional sequence design → validator review.',
        cardClassName: 'border-violet-500/40 bg-violet-500/10 hover:border-violet-300',
        labelClassName: 'text-violet-100',
    },
];
