import { useQuery } from '@tanstack/react-query';
import { fetchModelIntegration, type ModelIntegrationConfig } from '../lib/api';

interface ModelIntegrationControlProps {
    modelId: string;
    workflowId: string;
    checked: boolean;
    onChange: (checked: boolean) => void;
    fallbackLabel: string;
    integration?: ModelIntegrationConfig;
}

export const useModelIntegrationConfig = (modelId: string) => useQuery({
    queryKey: ['model-integration', modelId],
    queryFn: async () => (await fetchModelIntegration(modelId)).data,
    staleTime: 5 * 60 * 1000,
    retry: false,
});

/**
 * Shared workflow-stage control backed by the global model registry.
 * Workflow cards choose whether the stage is enabled; scientific wording,
 * model identity, checkpoint context, and workflow purpose stay centralized.
 */
export function ModelIntegrationControl({
    modelId,
    workflowId,
    checked,
    onChange,
    fallbackLabel,
    integration,
}: ModelIntegrationControlProps) {
    const workflow = integration?.workflows?.[workflowId];

    return (
        <div className="flex max-w-xl flex-col gap-2" data-model-integration={modelId}>
            <label className="flex cursor-pointer items-center gap-2 text-cyan-300 hover:text-cyan-200">
                <input
                    type="checkbox"
                    checked={checked}
                    onChange={(event) => onChange(event.target.checked)}
                    className="h-4 w-4 rounded border-slate-600 bg-slate-800 text-cyan-500 focus:ring-cyan-500"
                />
                <span className="text-sm font-medium">
                    {integration?.operator_label || fallbackLabel}
                </span>
            </label>

            {checked && integration && (
                <div className="ml-6 rounded-md border border-cyan-900/70 bg-cyan-950/25 px-3 py-2 text-xs text-slate-300">
                    <div className="mb-1 flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-cyan-300">{integration.model_name}</span>
                        {integration.checkpoint_label && (
                            <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[11px] text-slate-400">
                                {integration.checkpoint_label}
                            </span>
                        )}
                    </div>
                    <p>{workflow?.enabled_summary || integration.model_summary}</p>
                    <p className="mt-1 text-[11px] text-slate-500">Scheduler-managed; enabled analysis fails closed.</p>
                </div>
            )}
        </div>
    );
}