export type ProteinLocalRedesignSequenceMethod = 'skip' | 'fampnn' | 'mpnn';

export interface ProteinLocalRedesignUiState {
    sequenceDesignEnabled: boolean;
    showSequenceSampling: boolean;
    showLegacyOptionalStages: boolean;
    sequenceSectionLabel: 'Optional Sequence Redesign' | 'Sequence Redesign';
}

export const getProteinLocalRedesignUiState = (
    isNativeLocalRedesign: boolean,
    sequenceMethod: ProteinLocalRedesignSequenceMethod,
): ProteinLocalRedesignUiState => {
    const sequenceDesignEnabled = !isNativeLocalRedesign && sequenceMethod !== 'skip';

    return {
        sequenceDesignEnabled,
        showSequenceSampling: sequenceDesignEnabled,
        showLegacyOptionalStages: !isNativeLocalRedesign,
        sequenceSectionLabel: isNativeLocalRedesign ? 'Optional Sequence Redesign' : 'Sequence Redesign',
    };
};
