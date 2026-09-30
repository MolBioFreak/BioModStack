import type { NativeBinderParameter } from './nativeBinderAuthoring';

export const binderRoundDesigners = ['proteinmpnn', 'fampnn', 'caliby_binder'] as const;
export const binderRoundPredictors = ['protenix', 'boltz2', 'esmfold2'] as const;
export type BinderRoundDesigner = typeof binderRoundDesigners[number];
export type BinderRoundPredictor = typeof binderRoundPredictors[number];
export interface BinderRoundStage { model_id: string; params: Record<string, UntypedApiValue> }
/** This envelope belongs to Jobs, never to the generator's native settings. */
interface BinderRoundCommon {
    enabled: boolean;
    binder_chains: string[];
    target_chains: string[];
}
export type BinderRoundRequest = BinderRoundCommon & (
    | { schema_version: 1; sequence_design: BinderRoundStage; prediction: BinderRoundStage }
    | { schema_version: 2; sequence_design: BinderRoundStage[]; prediction: BinderRoundStage[] }
);
export type BinderRoundRole = 'sequence_design' | 'prediction';
export const roundStages = (value: BinderRoundStage | BinderRoundStage[]): BinderRoundStage[] => Array.isArray(value) ? value : [value];
export interface BinderRoundDraft {
    binder_round: BinderRoundRequest;
    binder_round_drafts: Record<string, Record<string, UntypedApiValue>>;
}
export interface BinderRoundCatalog {
    id: string; name?: string; params: NativeBinderParameter[];
    modes?: Array<{ id: string; params?: string[] }>;
}

// These slots are supplied by the producer-bound continuation owner, not by a
// second source picker. Target/template acquisition stays in the native form.
const boundInputs = new Set(['input_pdb', 'pdb_paths', 'source_identity_json', 'design_chain', 'target_chain',
    'binder_chains', 'target_chains', 'sequence', 'sequence_name', 'chain_id', 'pdb_sequence_path', 'pdb_chain_ids',
    'dna_sequence', 'dna_chain_id', 'rna_sequence', 'rna_chain_id', 'ligand_smiles', 'ligand_ccd', 'ligand_chain_id',
    'complex_components', 'complex_components_json']);
export const roundParameterIsBound = (name: string) => boundInputs.has(name);
export const blindFixedParameters = new Set(['protenix_use_template', 'colabfold_use_templates',
    'protenix_anchor_target', 'protenix_anchor_strict', 'boltz_anchor_target', 'boltz_anchor_strict']);
export const roundCountParameter = (model: string) => model === 'caliby_binder' ? 'caliby_num_seqs_per_pdb' : 'seqs_per_design';

export function roundStageParams(model: string, values: Record<string, UntypedApiValue>) {
    const prediction = (binderRoundPredictors as readonly string[]).includes(model);
    return Object.fromEntries(Object.entries(values).filter(([key]) => !roundParameterIsBound(key))
        .map(([key, value]) => [key, prediction && blindFixedParameters.has(key) ? false : value]));
}

export function hydrateBinderRound(values?: Record<string, UntypedApiValue>): BinderRoundDraft {
    const saved = values?.binder_round as BinderRoundRequest | undefined;
    const request: BinderRoundRequest = saved ? structuredClone(saved) : {
        schema_version: 1, enabled: true,
        sequence_design: { model_id: 'fampnn', params: {} },
        prediction: { model_id: 'protenix', params: {} }, binder_chains: [], target_chains: [],
    };
    const drafts = structuredClone(values?.binder_round_drafts ?? {}) as BinderRoundDraft['binder_round_drafts'];
    for (const stage of [...roundStages(request.sequence_design), ...roundStages(request.prediction)]) {
        stage.params = roundStageParams(stage.model_id, stage.params);
        drafts[stage.model_id] = { ...drafts[stage.model_id], ...stage.params };
    }
    return { binder_round: request, binder_round_drafts: drafts };
}

/** Merge catalog defaults underneath exact saved values; false/zero/null survive. */
export function withRoundCatalogs(draft: BinderRoundDraft, catalogs: BinderRoundCatalog[]): BinderRoundDraft {
    const drafts = { ...draft.binder_round_drafts };
    for (const catalog of catalogs) {
        const defaults = Object.fromEntries(catalog.params.filter(parameter => Object.hasOwn(parameter, 'default'))
            .map(parameter => [parameter.name, parameter.default]));
        drafts[catalog.id] = roundStageParams(catalog.id, { ...defaults, ...drafts[catalog.id] });
    }
    const stage = (value: BinderRoundStage): BinderRoundStage => ({ ...value,
        params: roundStageParams(value.model_id, { ...drafts[value.model_id], ...value.params }) });
    const request = draft.binder_round;
    return { binder_round: request.schema_version === 1
        ? { ...request, sequence_design: stage(request.sequence_design), prediction: stage(request.prediction) }
        : { ...request, sequence_design: request.sequence_design.map(stage), prediction: request.prediction.map(stage) },
        binder_round_drafts: drafts };
}

/** Legacy selector switches the sole engine; a multi-engine edit updates only its branch. */
export function changeRoundStage(draft: BinderRoundDraft, role: BinderRoundRole, model: string, patch?: Record<string, UntypedApiValue>): BinderRoundDraft {
    const selected = roundStages(draft.binder_round[role]);
    const current = selected.find(stage => stage.model_id === model);
    const params = roundStageParams(model, { ...draft.binder_round_drafts[model], ...current?.params, ...patch });
    const stage = { model_id: model, params };
    const stages = selected.length === 1 ? [stage] : selected.map(value => value.model_id === model ? stage : value);
    const drafts = { ...draft.binder_round_drafts, ...Object.fromEntries(selected.map(value => [value.model_id, value.params])), [model]: params };
    return replaceRoundStages(draft, role, stages, drafts);
}

function replaceRoundStages(draft: BinderRoundDraft, role: BinderRoundRole, stages: BinderRoundStage[], drafts: BinderRoundDraft['binder_round_drafts']): BinderRoundDraft {
    const request = draft.binder_round;
    // Once promoted, retain v2 even after removing back to a single engine.
    const binder_round: BinderRoundRequest = request.schema_version === 1 && stages.length === 1
        ? { ...request, [role]: stages[0] }
        : { ...request, schema_version: 2,
            sequence_design: role === 'sequence_design' ? stages : roundStages(request.sequence_design),
            prediction: role === 'prediction' ? stages : roundStages(request.prediction) };
    return { binder_round, binder_round_drafts: drafts };
}

export function toggleRoundStage(draft: BinderRoundDraft, role: BinderRoundRole, model: string): BinderRoundDraft {
    const selected = roundStages(draft.binder_round[role]);
    const current = selected.find(stage => stage.model_id === model);
    if (current && selected.length === 1) return draft;
    const drafts = { ...draft.binder_round_drafts, ...Object.fromEntries(selected.map(stage => [stage.model_id, stage.params])) };
    const stages = current ? selected.filter(stage => stage.model_id !== model)
        : [...selected, { model_id: model, params: roundStageParams(model, drafts[model] ?? {}) }];
    return replaceRoundStages(draft, role, stages, drafts);
}
