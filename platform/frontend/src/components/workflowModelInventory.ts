import { getModelDocumentationLinks, type DocumentationLink, type ModelDocumentationTopic } from './modelDocumentationRegistry.js';

export interface WorkflowModelInventoryEntry {
    workflowId: string;
    label: string;
    modelTopics: ModelDocumentationTopic[];
    sourceFiles: string[];
}

export interface UniqueWorkflowModelEntry {
    topic: ModelDocumentationTopic;
    links: DocumentationLink[];
}

export const WORKFLOW_MODEL_INVENTORY: WorkflowModelInventoryEntry[] = [
    {
        workflowId: 'mutagenesis',
        label: 'Mutagenesis Library',
        modelTopics: ['boltz2', 'rf3'],
        sourceFiles: [
            'platform/frontend/src/components/JobSubmission.tsx',
            'platform/frontend/src/components/MutagenesisTemplate.tsx',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'structure_prediction',
        label: 'Structure Prediction',
        modelTopics: ['boltz2', 'rf3', 'protenix'],
        sourceFiles: [
            'platform/api/config/templates/structure_prediction.yaml',
            'platform/frontend/src/components/StructurePredictionTemplate.tsx',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'antibody_denovo',
        label: 'De Novo Nanobody Toolkit',
        modelTopics: ['rfantibody', 'boltzgen', 'ppiflow', 'fampnn', 'caliby', 'proteinmpnn', 'protenix', 'boltz2'],
        sourceFiles: [
            'platform/api/config/models/antibody_denovo.yaml',
            'platform/frontend/src/components/AntibodyDenovoTemplate.tsx',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'boltzgen_design',
        label: 'BoltzGEN',
        modelTopics: ['boltzgen', 'diffdock', 'unidock'],
        sourceFiles: [
            'platform/api/config/models/boltzgen.yaml',
            'platform/api/config/templates/boltzgen_ligand.yaml',
            'platform/frontend/src/components/BoltzGenTemplate.tsx',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'bindcraft',
        label: 'BindCraft',
        modelTopics: ['bindcraft', 'alphafold2', 'proteinmpnn'],
        sourceFiles: [
            'platform/frontend/src/components/JobSubmission.tsx',
            'platform/frontend/src/components/BindCraftTemplate.tsx',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'oligo_design',
        label: 'Oligo Designer',
        modelTopics: ['rfdpoly', 'boltz2'],
        sourceFiles: [
            'platform/api/config/models/oligo_design.yaml',
            'platform/frontend/src/components/OligoDesignerTemplate.tsx',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'protein_local_redesign',
        label: 'Protein Local Redesign',
        modelTopics: ['rfdiffusion', 'fampnn', 'proteinmpnn', 'boltz2'],
        sourceFiles: [
            'platform/frontend/src/components/ProteinLocalRedesignTemplate.tsx',
            'platform/frontend/src/components/JobSubmission.tsx',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'boltz_cp_experimental',
        label: 'Fold-CP Experimental',
        modelTopics: ['fold_cp', 'boltz2'],
        sourceFiles: [
            'platform/api/config/models/boltz_cp_experimental.yaml',
            'platform/api/config/templates/boltz_cp_experimental.yaml',
            'platform/frontend/src/components/StructurePredictionTemplate.tsx',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'confornets_experimental',
        label: 'ConforNets Experimental',
        modelTopics: ['confornets'],
        sourceFiles: [
            'platform/api/config/models/confornets_experimental.yaml',
            'platform/api/config/templates/confornets_experimental.yaml',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'esmfold2_experimental',
        label: 'ESMFold2 Experimental',
        modelTopics: ['esmfold2'],
        sourceFiles: [
            'platform/api/config/models/esmfold2_experimental.yaml',
            'platform/api/config/templates/esmfold2_experimental.yaml',
            'workflows/esmfold2_experimental.nf',
            'modules/esmfold2_experimental.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'caliby_experimental',
        label: 'Caliby Experimental',
        modelTopics: ['caliby'],
        sourceFiles: [
            'platform/api/config/models/caliby_experimental.yaml',
            'platform/api/config/templates/caliby_experimental.yaml',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'protein_cad_experimental',
        label: 'Protein CAD Experimental',
        modelTopics: ['laproteina', 'disco'],
        sourceFiles: [
            'platform/api/config/models/protein_cad_experimental.yaml',
            'platform/api/config/templates/protein_cad_experimental.yaml',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'protein_hunter_experimental',
        label: 'Protein Hunter Experimental',
        modelTopics: ['protein_hunter', 'boltz2', 'chai1'],
        sourceFiles: [
            'platform/api/config/models/protein_hunter_experimental.yaml',
            'platform/api/config/templates/protein_hunter_experimental.yaml',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'dna_polymerase',
        label: 'DNA Polymerase Engineering',
        modelTopics: ['ligandmpnn', 'boltz2'],
        sourceFiles: [
            'platform/api/config/templates/dna_polymerase.yaml',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'binder_design',
        label: 'Protein Binder Design',
        modelTopics: ['rfdiffusion', 'proteinmpnn', 'alphafold2'],
        sourceFiles: [
            'platform/api/config/templates/binder_design.yaml',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'structure_validation',
        label: 'Structure Validation',
        modelTopics: ['alphafold2', 'boltz2', 'rf3'],
        sourceFiles: [
            'platform/api/config/templates/structure_validation.yaml',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'boltzgen_ligand',
        label: 'Ligand-Aware Binder',
        modelTopics: ['boltzgen', 'diffdock'],
        sourceFiles: [
            'platform/api/config/templates/boltzgen_ligand.yaml',
            'platform/frontend/src/components/BoltzGenTemplate.tsx',
            'main.nf',
            'nextflow.config',
        ],
    },
];

export const UNIQUE_WORKFLOW_MODEL_TOPICS = Array.from(
    new Set(WORKFLOW_MODEL_INVENTORY.flatMap((workflow) => workflow.modelTopics)),
).sort((left, right) => left.localeCompare(right)) as ModelDocumentationTopic[];

export const getWorkflowModelTopics = (workflowId: string | null | undefined): ModelDocumentationTopic[] => {
    if (!workflowId) return [];
    return WORKFLOW_MODEL_INVENTORY.find((workflow) => workflow.workflowId === workflowId)?.modelTopics ?? [];
};

export const getUniqueWorkflowModelInventory = (): UniqueWorkflowModelEntry[] =>
    UNIQUE_WORKFLOW_MODEL_TOPICS.map((topic) => ({
        topic,
        links: getModelDocumentationLinks([topic]),
    }));
