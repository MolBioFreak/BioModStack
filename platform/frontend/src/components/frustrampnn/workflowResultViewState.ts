export type WorkflowResultModel = 'workflow' | 'frustrampnn';
export type FrustraMpnnResultScope = 'this-job' | 'whole-experiment';

export interface WorkflowResultViewState {
    model: WorkflowResultModel;
    scope: FrustraMpnnResultScope;
}

interface WorkflowResultViewAvailability {
    frustraMpnnAvailable: boolean;
    directFrustraMpnnJob: boolean;
}

export const parseWorkflowResultViewState = (
    search: string,
    availability: WorkflowResultViewAvailability,
): WorkflowResultViewState => {
    const params = new URLSearchParams(search);
    const requestedModel = params.get('result_model');
    const defaultModel: WorkflowResultModel = availability.frustraMpnnAvailable
        && availability.directFrustraMpnnJob
        ? 'frustrampnn'
        : 'workflow';
    const model: WorkflowResultModel = requestedModel === 'frustrampnn'
        && availability.frustraMpnnAvailable
        ? 'frustrampnn'
        : requestedModel === 'workflow' && !availability.directFrustraMpnnJob
            ? 'workflow'
            : defaultModel;
    const scope: FrustraMpnnResultScope = model === 'frustrampnn'
        && params.get('frustrampnn_scope') === 'whole-experiment'
        ? 'whole-experiment'
        : 'this-job';
    return { model, scope };
};

export interface FrustraMpnnExperimentContext {
    projectId: string;
    globalExperimentId: string;
    domainExperimentId: string;
}

export const parseFrustraMpnnExperimentContext = (
    search: string,
): FrustraMpnnExperimentContext | null => {
    const params = new URLSearchParams(search);
    const projectId = params.get('workspace_id')?.trim() ?? '';
    const globalExperimentId = params.get('global_experiment_id')?.trim() ?? '';
    const domainExperimentId = params.get('domain_experiment_id')?.trim() ?? '';
    return projectId && globalExperimentId && domainExperimentId
        ? { projectId, globalExperimentId, domainExperimentId }
        : null;
};

export const updateWorkflowResultViewSearch = (
    search: string,
    state: WorkflowResultViewState,
): string => {
    const params = new URLSearchParams(search);
    params.set('result_model', state.model);
    params.set('frustrampnn_scope', state.scope);
    const encoded = params.toString();
    return encoded ? `?${encoded}` : '';
};
