import type { SelectedRoundModel, SelectedRoundPlan, SelectedRoundStage } from './binderSelectedRound';

export const invocationModes: Record<string, string[]> = {
    proteinmpnn: ['design'], fampnn: ['design', 'binder_design'], caliby_binder: ['design'],
    caliby_experimental: ['ensemble_design', 'sidechain_pack'], ligandmpnn: ['ligand_aware', 'ntp_aware', 'metal_aware', 'dna_aware'],
    protonpottsmpnn: ['redesign'], protenix: ['complex', 'predict'], boltz2: ['complex', 'predict'],
    esmfold2: ['predict'], boltz_cp_experimental: ['design'], molecular_dynamics: ['simulate'],
};
export interface InvocationDraftStage {
    stage: SelectedRoundStage; enabled: boolean;
    drafts: Record<string, Record<string, UntypedApiValue>>;
    taskDrafts?: Record<string, Record<string, UntypedApiValue>>;
    disabledInvocationIds?: string[];
}
/** Upgrade editor drafts only; never rewrite a submitted historical intent. */
export function invocationDrafts(stages: InvocationDraftStage[]): InvocationDraftStage[] {
    return stages.map(item => ({ ...item, stage: item.stage.kind === 'maturation' ? item.stage : {
        ...item.stage, models: item.stage.models.map((call, index) => ({ ...call,
            invocation_id: call.invocation_id ?? `${item.stage.id}-call-${index + 1}`,
            mode: call.mode ?? (call.model_id === 'fampnn' ? 'binder_design' : invocationModes[call.model_id]?.[0]),
        })),
    } }));
}
export function activeInvocationPlan(stages: InvocationDraftStage[]): SelectedRoundPlan {
    return { schema_version: 2, stages: invocationDrafts(stages).filter(item => item.enabled)
        .map(item => item.stage.kind === 'maturation' ? item.stage : ({ ...item.stage,
            models: item.stage.models.filter(call => !item.disabledInvocationIds?.includes(call.invocation_id!)) }))
        .filter(stage => stage.kind === 'maturation' || stage.models.length > 0) };
}
export function newInvocation(model: string, params: SelectedRoundModel['params'] = {}): SelectedRoundModel {
    return { invocation_id: crypto.randomUUID(), model_id: model, mode: invocationModes[model][0], params: structuredClone(params) };
}
export interface InitialInvocationDraft {
    enabled: boolean; stages: InvocationDraftStage[];
}
export function initialInvocationEnvelope(draft: InitialInvocationDraft) {
    return { schema_version: 2 as const, enabled: draft.enabled, round_plan: activeInvocationPlan(draft.stages) };
}
