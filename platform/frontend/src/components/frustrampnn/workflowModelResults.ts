export interface WorkflowModelResultJob {
    readonly model_id: string;
    readonly mode?: string | null;
    readonly params?: Record<string, unknown> | null;
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

export const primaryWorkflowResultModel = (job: WorkflowModelResultJob): string =>
    job.mode?.trim().toLowerCase() === 'structure_prediction' ? 'structure_prediction' : job.model_id.trim().toLowerCase();

export const buildWorkflowModelResults = ({
    job,
    modelCounts,
    frustraMpnnAvailable,
}: {
    job: WorkflowModelResultJob;
    modelCounts: Readonly<Record<string, number>>;
    frustraMpnnAvailable: boolean;
}): WorkflowModelResultSibling[] => {
    const primaryModelId = primaryWorkflowResultModel(job);
    const validator = typeof job.params?.structure_validator === 'string'
        ? job.params.structure_validator.trim().toLowerCase()
        : '';
    const persisted = Object.keys(modelCounts);
    const siblings: WorkflowModelResultSibling[] = [{
        modelId: primaryModelId,
        label: primaryModelId === 'structure_prediction' ? 'Structure Prediction' : labelForModel(primaryModelId),
        kind: 'primary',
    }];
    if (validator && validator !== primaryModelId && persisted.includes(validator)) {
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
