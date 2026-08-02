export interface ModelIntegrationPresentation {
    model_name: string;
    checkpoint_label?: string | null;
    model_summary: string;
    workflows?: Record<string, {
        enabled_summary: string;
    }>;
}

export interface ModelIntegrationDetails {
    modelName: string;
    checkpointLabel: string | null;
    summary: string;
}

export interface ModelIntegrationSelection {
    value: boolean;
    hasExplicitSelection: boolean;
    defaultApplied: boolean;
}

export function getModelIntegrationDetails(
    enabled: boolean,
    integration: ModelIntegrationPresentation | undefined,
    workflowId: string,
): ModelIntegrationDetails | null {
    if (!enabled || !integration) return null;

    return {
        modelName: integration.model_name,
        checkpointLabel: integration.checkpoint_label || null,
        summary: integration.workflows?.[workflowId]?.enabled_summary || integration.model_summary,
    };
}

export function createModelIntegrationSelection(
    explicitValue: unknown,
    fallbackValue: boolean,
): ModelIntegrationSelection {
    const hasExplicitSelection = typeof explicitValue === 'boolean';
    return {
        value: hasExplicitSelection ? explicitValue : fallbackValue,
        hasExplicitSelection,
        defaultApplied: hasExplicitSelection,
    };
}

export function applyModelIntegrationDefault(
    selection: ModelIntegrationSelection,
    configuredDefault: unknown,
): ModelIntegrationSelection {
    if (
        selection.hasExplicitSelection
        || selection.defaultApplied
        || typeof configuredDefault !== 'boolean'
    ) {
        return selection;
    }

    return {
        ...selection,
        value: configuredDefault,
        defaultApplied: true,
    };
}

export function applyModelIntegrationChoice(
    selection: ModelIntegrationSelection,
    checked: boolean,
): ModelIntegrationSelection {
    return {
        ...selection,
        value: checked,
        hasExplicitSelection: true,
    };
}