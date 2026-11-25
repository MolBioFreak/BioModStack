export const launcherWorkflowTemplates = [
        {
            id: 'mutagenesis',
            name: 'Mutagenesis Library',
            description: 'Build variant libraries and predict structures.',
            icon: 'dna',
            color: '#8B5CF6',
            stages: [{ tool: 'Library Gen' }, { tool: 'Structure Prediction' }],
        },
        {
            id: 'structure_prediction',
            name: 'Structure Prediction',
            description: 'Predict proteins, nucleic acids, and complexes.',
            icon: 'microscope',
            color: '#F59E0B',
            stages: [{ tool: 'Boltz-2 / Fold-CP / Boltz API / Protenix / ESMFold2' }],
        },

        {
            id: 'antibody_denovo',
            name: 'De Novo Binder Design',
            description: 'Choose BC2, BoltzGen, PPIFlow or RFantibody with model-native inputs and settings, then review candidates for optional selected refinement or GROMACS MD.',
            icon: 'flask',
            color: '#14B8A6',
            stages: [
                { tool: 'BindCraft2 / BoltzGen / PPIFlow / RFantibody' },
                { tool: 'Candidate review' },
                { tool: 'Compatible refinement (optional)' }
            ],
        },

        {
            id: 'protein_modification_experimental',
            name: 'De Novo Design',
            description: 'Start with RFD4-Proteina, choose another generation engine, or open a redesign, resequencing or shape editor.',
            icon: 'cube',
            color: '#22C55E',
            stages: [
                { tool: 'Generate · RFD4-Proteina' },
                { tool: 'Redesign structure' },
                { tool: 'Shape' },
                { tool: 'Resequence' },
            ],
        },
];
export const launcherExperimentalTemplates: Array<{ id: string; name: string; description: string; icon: string; color: string; experimental: boolean; stages: Array<{ tool: string }> }> = [];

export function visibleLauncherTemplates<T extends { id: string }>(templates: T[], debug = false): T[] {
  return templates.filter(t => ![
    'structure_validation', 'structure_prediction', 'boltz_cp_experimental', 'binder_design',
    'protein_cad_experimental', 'protein_local_redesign', 'confornets_experimental',
  ].includes(t.id) && (t.id !== 'dna_polymerase' || debug));
}
