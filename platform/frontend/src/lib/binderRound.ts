import type { NativeBinderParameter } from './nativeBinderAuthoring';

export const binderRoundDesigners = ['proteinmpnn', 'fampnn', 'caliby_binder'] as const;
export const binderRoundPredictors = ['protenix', 'boltz2', 'esmfold2'] as const;
export type BinderRoundDesigner = typeof binderRoundDesigners[number];
export type BinderRoundPredictor = typeof binderRoundPredictors[number];
export interface BinderRoundStage { model_id: string; params: Record<string, UntypedApiValue> }
/** This envelope belongs to Jobs, never to the generator's native settings. */
export interface BinderRoundRequest {
    schema_version: 1;
    enabled: boolean;
    sequence_design: BinderRoundStage;
    prediction: BinderRoundStage;
    binder_chains: string[];
    target_chains: string[];
}
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
    for (const stage of [request.sequence_design, request.prediction]) {
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
    return { binder_round: { ...draft.binder_round, sequence_design: stage(draft.binder_round.sequence_design),
        prediction: stage(draft.binder_round.prediction) }, binder_round_drafts: drafts };
}

export function changeRoundStage(draft: BinderRoundDraft, role: 'sequence_design' | 'prediction', model: string, patch?: Record<string, UntypedApiValue>): BinderRoundDraft {
    const current = draft.binder_round[role];
    const params = roundStageParams(model, { ...draft.binder_round_drafts[model], ...(current.model_id === model ? current.params : {}), ...patch });
    return { binder_round: { ...draft.binder_round, [role]: { model_id: model, params } },
        binder_round_drafts: { ...draft.binder_round_drafts, [current.model_id]: current.params, [model]: params } };
}
