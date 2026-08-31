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
        modelTopics: ['boltz2', 'rf3', 'esmfold2'],
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
        modelTopics: ['boltz2', 'fold_cp', 'protenix', 'esmfold2', 'frustrampnn'],
        sourceFiles: [
            'platform/api/config/templates/structure_prediction.yaml',
            'platform/api/config/models/boltz_cp_experimental.yaml',
            'platform/frontend/src/components/StructurePredictionTemplate.tsx',
            'workflows/boltz_cp_experimental.nf',
            'main.nf',
            'nextflow.config',
        ],
    },
    {
        workflowId: 'antibody_denovo',
        label: 'De Novo Nanobody Toolkit',
        modelTopics: ['rfantibody', 'boltzgen', 'ppiflow', 'fampnn', 'caliby', 'proteinmpnn', 'protenix', 'boltz2', 'esmfold2'],
        sourceFiles: [
            'platform/api/config/models/antibody_denovo.yaml',
            'platform/frontend/src/components/AntibodyDenovoTemplate.tsx',
            'workflows/antibody_denovo.nf',
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
        workflowId: 'protein_modification_experimental',
        label: 'De Novo Design',
        modelTopics: ['rfdiffusion', 'laproteina', 'disco', 'fampnn', 'proteinmpnn', 'boltz2'],
        sourceFiles: [
            'platform/api/config/models/protein_modification_experimental.yaml',
            'platform/api/config/models/protein_local_redesign.yaml',
            'platform/frontend/src/components/ProteinModificationTemplate.tsx',
            'platform/frontend/src/components/ProteinLocalRedesignTemplate.tsx',
            'platform/frontend/src/components/RFD3LocalRedesignResultsPane.tsx',
            'platform/frontend/src/components/RFD3GenerationResultsPane.tsx',
            'workflows/protein_cad_experimental.nf',
            'workflows/protein_local_redesign.nf',
            'modules/rfd3.nf',
            'nextflow.config',
        ],
    },

    {
        workflowId: 'conformational_mapping',
        label: 'Conformational Mapping',
        modelTopics: ['confornets', 'protenix'],
        sourceFiles: [
            'platform/api/config/models/conformational_mapping.yaml',
            'platform/api/config/templates/conformational_mapping.yaml',
            'workflows/conformational_mapping.nf',
            'platform/frontend/src/components/conformationalMapping/ConformationalMappingLauncher.tsx',
            'platform/frontend/src/components/conformationalMapping/ConformationalMappingViewer.tsx',
            'platform/frontend/src/components/ResultsViewer.tsx',
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
        workflowId: 'structure_validation',
        label: 'Structure Validation',
        modelTopics: ['alphafold2', 'boltz2', 'rf3'],
        sourceFiles: [
            'platform/api/config/templates/structure_validation.yaml',
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
    const canonicalWorkflowId = workflowId === 'esmfold2_experimental' || workflowId === 'esmfold2'
        ? 'structure_prediction'
        : workflowId === 'protein_cad_experimental'
            ? 'protein_modification_experimental'
            : workflowId === 'protein_local_redesign'
                ? 'protein_modification_experimental'
            : workflowId === 'confornets_experimental'
                ? 'conformational_mapping'
                : workflowId;
    return WORKFLOW_MODEL_INVENTORY.find((workflow) => workflow.workflowId === canonicalWorkflowId)?.modelTopics ?? [];
};

export const getUniqueWorkflowModelInventory = (): UniqueWorkflowModelEntry[] =>
    UNIQUE_WORKFLOW_MODEL_TOPICS.map((topic) => ({
        topic,
        links: getModelDocumentationLinks([topic]),
    }));
