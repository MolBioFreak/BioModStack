export interface WorkflowModelResultJob {
    readonly model_id: string;
    readonly params?: Record<string, unknown> | null;
}

export interface WorkflowModelResultDesign {
    readonly id: string;
    readonly provenance?: Record<string, unknown> | null;
    readonly artifact_class?: string | null;
}

export interface WorkflowModelResultSibling {
    readonly modelId: string;
    readonly label: string;
    readonly kind: 'primary' | 'validator' | 'frustrampnn' | 'model';
}

const labelForModel = (modelId: string): string => {
    const known: Record<string, string> = {
        structure_prediction: 'Structure Prediction',
        boltz2: 'Boltz-2',
        boltz_cp_experimental: 'Boltz-CP',
        protenix: 'Protenix',
        frustrampnn: 'FrustraMPNN',
        thermompnn: 'ThermoMPNN',
        proteinmpnn: 'ProteinMPNN',
        fampnn: 'FAMPNN',
        esmfold2: 'ESMFold2',
    };
    return known[modelId] ?? modelId.split(/[_-]+/).filter(Boolean).map((token) => token[0]?.toUpperCase() + token.slice(1)).join(' ');
};

const designModelId = (design: WorkflowModelResultDesign): string => {
    const value = design.provenance?.model_id;
    return typeof value === 'string' ? value.trim().toLowerCase() : '';
};

export const buildWorkflowModelResults = ({
    job,
    designs,
    frustraMpnnAvailable,
}: {
    job: WorkflowModelResultJob;
    designs: readonly WorkflowModelResultDesign[];
    frustraMpnnAvailable: boolean;
}): WorkflowModelResultSibling[] => {
    const primaryModelId = job.model_id.trim().toLowerCase();
    const validator = typeof job.params?.structure_validator === 'string'
        ? job.params.structure_validator.trim().toLowerCase()
        : '';
    const persisted = Array.from(new Set(designs.map(designModelId).filter(Boolean)));
    const siblings: WorkflowModelResultSibling[] = [{
        modelId: primaryModelId,
        label: primaryModelId === 'structure_prediction' ? 'Structure Prediction' : labelForModel(primaryModelId),
        kind: 'primary',
    }];
    if (validator && validator !== primaryModelId && (persisted.includes(validator) || designs.some((design) => design.artifact_class === 'validated_complex'))) {
        siblings.push({ modelId: validator, label: 'Validator', kind: 'validator' });
    }
    if (frustraMpnnAvailable && primaryModelId !== 'frustrampnn') {
        siblings.push({ modelId: 'frustrampnn', label: 'FrustraMPNN', kind: 'frustrampnn' });
    }
    for (const modelId of persisted) {
        if (siblings.some((item) => item.modelId === modelId)) continue;
        siblings.push({ modelId, label: labelForModel(modelId), kind: 'model' });
    }
    return siblings;
};

export const filterDesignsForResultModel = <T extends WorkflowModelResultDesign>(
    designs: readonly T[],
    modelId: string,
): T[] => designs.filter((design) => designModelId(design) === modelId);
