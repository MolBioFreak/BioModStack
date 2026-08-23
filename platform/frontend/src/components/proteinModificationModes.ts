export type ModificationMode = 'de_novo_design' | 'rfd3_local_redesign' | 'region_redesign' | 'shape_blueprint';

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
        id: 'rfd3_local_redesign',
        label: 'RFD3 Native Local Edit',
        description: 'RFD3 only. Edits selected coordinates and preserves the source sequence; no sequence redesign or validation pipeline.',
        cardClassName: 'border-emerald-500/40 bg-emerald-500/10 hover:border-emerald-300',
        labelClassName: 'text-emerald-100',
    },
    {
        id: 'region_redesign',
        label: 'Validated Region Redesign',
        description: 'Full pipeline: RFD3 backbone editing, FA-MPNN sequence design, then ESMFold2 and Protenix V2 validation.',
        cardClassName: 'border-amber-500/40 bg-amber-500/10 hover:border-amber-300',
        labelClassName: 'text-amber-100',
    },
    {
        id: 'shape_blueprint',
        label: 'Shape Blueprint',
        description: 'Immutable geometry → RFD3 Cα shape-transfer → conditional sequence design → validator review.',
        cardClassName: 'border-violet-500/40 bg-violet-500/10 hover:border-violet-300',
        labelClassName: 'text-violet-100',
    },
];
