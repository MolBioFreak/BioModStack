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
            id: 'oligo_design',
            name: 'Oligo Designer',
            description: 'Design nucleoprotein assemblies with validation.',
            icon: 'dna',
            color: '#6366F1',
            stages: [{ tool: 'RFDpoly' }, { tool: 'Boltz-2' }, { tool: 'Filtering' }],
        },
];
export const launcherExperimentalTemplates = [
        {
            id: 'protein_modification_experimental',
            name: 'De Novo Design',
            description: 'Generate new proteins with native RFD3, iterate an existing structure, or generate into a shape blueprint.',
            icon: 'cube',
            color: '#22C55E',
            experimental: true,
            stages: [
                { tool: 'RFD3 (Preferred)' },
                { tool: 'RFD3 Iteration' },
                { tool: 'Shape Blueprint' },
                { tool: 'DISCO / La-Proteina (Backup)' },
            ],
        },

];

export function visibleLauncherTemplates<T extends { id: string }>(templates: T[], debug = false): T[] {
  return templates.filter(t => ![
    'structure_validation', 'structure_prediction', 'boltz_cp_experimental', 'binder_design',
    'protein_cad_experimental', 'protein_local_redesign', 'protein_hunter_experimental', 'confornets_experimental',
  ].includes(t.id) && (t.id !== 'dna_polymerase' || debug));
}
