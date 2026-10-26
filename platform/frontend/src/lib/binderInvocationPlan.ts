import type { CandidateRef, SelectedRoundModel, SelectedRoundPlan, SelectedRoundStage } from './binderSelectedRound';

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
    stateDrafts?: Record<string, Record<string, import('./binderSelectedRound').CalibyConformerSettings | null>>;
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
            models: item.stage.models.filter(call => !item.disabledInvocationIds?.includes(call.invocation_id!)).map(call =>
                call.model_id === 'caliby_experimental' && call.mode === 'sidechain_pack' && call.input_groups
                    ? { ...call, input_groups: call.input_groups.map(group => ({ ...group,
                        states: group.states.map(({ settings: _settings, ...state }) => state) })) } : call) }))
        .filter(stage => stage.kind === 'maturation' || stage.models.length > 0) };
}
export function newInvocation(model: string, params: SelectedRoundModel['params'] = {}): SelectedRoundModel {
    return { invocation_id: crypto.randomUUID(), model_id: model, mode: invocationModes[model][0], params: structuredClone(params) };
}
export function invocationCandidateLabel(ref: CandidateRef, label?: string): string {
    const state = ref.kind === 'native_document' ? ref.target_state : ref.kind === 'design' ? ref.document?.target_state : undefined;
    if (label || state) return `${label || state}${ref.kind === 'native_document' && ref.model != null ? ` · MODEL ${ref.model}` : ref.kind === 'design' && ref.document?.model != null ? ` · MODEL ${ref.document.model}` : ''}`;
    if (ref.kind === 'native_document') return `Structure · MODEL ${ref.model ?? 'native'}`;
    if (ref.kind === 'native_sequence') return 'Selected sequence';
    if (ref.kind === 'native_observation') return 'Selected record';
    return `Design · MODEL ${ref.document?.model ?? 'native'}`;
}
export const invocationNames: Record<string, string> = {
    proteinmpnn: 'ProteinMPNN', fampnn: 'FA-MPNN', caliby_binder: 'Caliby binder',
    caliby_experimental: 'Caliby experimental', ligandmpnn: 'LigandMPNN', protonpottsmpnn: 'ProtonPottsMPNN',
    protenix: 'Protenix', boltz2: 'Boltz-2', esmfold2: 'ESMFold2', boltz_cp_experimental: 'Fold-CP experimental',
    molecular_dynamics: 'Molecular dynamics',
};
export function invocationLabel(call: SelectedRoundModel, index: number): string {
    return `${invocationNames[call.model_id] ?? call.model_id} · model ${index + 1}`;
}
/** Mode history belongs to the call identity, never the model shared by siblings. */
export function changeInvocationMode(item: InvocationDraftStage, id: string, mode: string): InvocationDraftStage {
    if (item.stage.kind === 'maturation') return item;
    const call = item.stage.models.find(value => value.invocation_id === id);
    if (!call || call.mode === mode) return item;
    // State identity, rather than row position, survives reordering and repeated calls.
    const stateDrafts = { ...item.stateDrafts };
    let input_groups = call.input_groups;
    if (call.model_id === 'caliby_experimental' && input_groups) {
        const designKey = `${id}:ensemble_design`;
        if (call.mode === 'ensemble_design') {
            stateDrafts[designKey] = Object.fromEntries(input_groups.flatMap(group => group.states)
                .filter(state => Object.hasOwn(state, 'settings'))
                .map(state => [state.state_id, structuredClone(state.settings!)]));
        } else {
            // Historical packing drafts may still contain design constraints.
            stateDrafts[designKey] = { ...Object.fromEntries(input_groups.flatMap(group => group.states)
                .filter(state => Object.hasOwn(state, 'settings'))
                .map(state => [state.state_id, structuredClone(state.settings!)])), ...stateDrafts[designKey] };
        }
        input_groups = input_groups.map(group => ({ ...group, states: group.states.map(({ settings: _settings, ...state }) => ({
            ...state, ...(mode === 'ensemble_design' && Object.hasOwn(stateDrafts[designKey] ?? {}, state.state_id)
                ? { settings: structuredClone(stateDrafts[designKey][state.state_id]) } : {}),
        })) }));
    }
    return { ...item, ...(call.model_id === 'caliby_experimental' ? { stateDrafts } : {}),
        drafts: { ...item.drafts, [`${id}:${call.mode}`]: structuredClone(call.params) },
        stage: { ...item.stage, models: item.stage.models.map(value => value.invocation_id === id
            ? { ...value, mode, params: structuredClone(item.drafts[`${id}:${mode}`] ?? {}), ...(input_groups ? { input_groups } : {}) } : value) } };
}
export function invocationPlacementLabel(call: SelectedRoundModel): string {
    return !Object.hasOwn(call, 'execution_target_id') ? 'Inherited round placement'
        : call.execution_target_id === null ? 'Local (explicit)' : `Remote worker: ${call.execution_target_id}`;
}
export interface InitialInvocationDraft {
    enabled: boolean; stages: InvocationDraftStage[];
}
export function initialInvocationEnvelope(draft: InitialInvocationDraft) {
    return { schema_version: 2 as const, enabled: draft.enabled, round_plan: activeInvocationPlan(draft.stages) };
}
