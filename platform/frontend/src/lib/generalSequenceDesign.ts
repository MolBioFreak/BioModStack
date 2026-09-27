import { fetchModelById } from './api';
import type { NativeBinderParameter } from './nativeBinderAuthoring';
import type { BioXpOperatorJsonSchema as Schema } from './bioxpClient';

export const generalSequenceDesigners = ['proteinmpnn', 'fampnn', 'caliby_experimental'] as const;
export type GeneralSequenceDesigner = typeof generalSequenceDesigners[number];
export const generalSequenceModes = { proteinmpnn: 'design', fampnn: 'design', caliby_experimental: 'ensemble_design' } as const;
export const generalSequenceNames = { proteinmpnn: 'ProteinMPNN', fampnn: 'FA-MPNN', caliby_experimental: 'Caliby' };
export interface GeneralSequenceSettings {
    params: Record<string, UntypedApiValue>;
    input_settings?: Record<string, UntypedApiValue>;
}
export interface GeneralSequenceDesignRequest extends GeneralSequenceSettings {
    schema_version: 1;
    enabled: boolean;
    model_id: GeneralSequenceDesigner;
}
export interface GeneralSequenceDesignDraft {
    sequence_design: GeneralSequenceDesignRequest;
    sequence_design_drafts: Partial<Record<GeneralSequenceDesigner, GeneralSequenceSettings>>;
}
export interface GeneralSequenceParameter extends NativeBinderParameter { schema?: Schema }
export interface GeneralSequenceInventory {
    model_id: GeneralSequenceDesigner;
    mode: string;
    parameters: GeneralSequenceParameter[];
    inputParameters: GeneralSequenceParameter[];
}
// Exact source documents and roles belong to the generated-candidate adapter.
// Never seed FA-MPNN's catalogue chain A, or import binder-role authoring here.
const bound = new Set(['input_pdb', 'pdb_paths', 'source_identity_json', 'ensembles', 'structures',
    'design_chain', 'target_chain', 'binder_chains', 'target_chains', 'state_id', 'path', 'task', 'schema_version']);
const unbound = (values: Record<string, UntypedApiValue>) => Object.fromEntries(Object.entries(values).filter(([key]) => !bound.has(key)));

function resolve(schema: Schema, root: Schema): Schema {
    const seen = new Set<string>();
    while (schema.$ref?.startsWith('#/') && !seen.has(schema.$ref)) {
        seen.add(schema.$ref);
        let target: UntypedApiValue = root;
        for (const part of schema.$ref.slice(2).split('/')) target = target?.[part.replace(/~1/g, '/').replace(/~0/g, '~')];
        if (!target || typeof target !== 'object') break;
        const { $ref: _ref, ...rest } = schema;
        schema = { ...target, ...rest };
    }
    return schema;
}
function schemaParameters(schema: Schema, root: Schema, catalog: GeneralSequenceParameter[] = []): GeneralSequenceParameter[] {
    return Object.entries(resolve(schema, root).properties ?? {}).filter(([name]) => !bound.has(name)).map(([name, raw]) => {
        const field = resolve(raw, root);
        const metadata = catalog.find(parameter => parameter.name === name);
        return { ...metadata, ...field, name, type: typeof field.type === 'string' ? field.type : metadata?.type ?? 'object',
            schema: { ...field, $defs: root.$defs } } as GeneralSequenceParameter;
    });
}
/** Use the advertised public mode, not a binder integration or Shape's contextual defaults. */
export async function fetchGeneralSequenceInventory(model_id: GeneralSequenceDesigner): Promise<GeneralSequenceInventory> {
    const { data } = await fetchModelById(model_id);
    const mode = generalSequenceModes[model_id];
    const selected = data.modes?.find((item: UntypedApiValue) => item.id === mode);
    if (!selected || !Array.isArray(data.params)) throw new Error(`No native ${model_id}/${mode} settings returned.`);
    const parameters: GeneralSequenceParameter[] = data.params.filter((parameter: UntypedApiValue) => selected.params.includes(parameter.name) && !bound.has(parameter.name));
    if (model_id !== 'caliby_experimental') return { model_id, mode, parameters, inputParameters: [] };
    const root = selected.parameter_schema as Schema;
    if (!root) throw new Error('Caliby native ensemble schema is unavailable. Saved settings remain intact.');
    const branch = root.discriminator?.mapping?.[mode];
    const request = resolve(branch ? { $ref: branch } : root, root);
    const ensembles = resolve(request.properties?.ensembles ?? {}, root);
    const ensemble = resolve(ensembles.items ?? {}, root);
    const states = resolve(ensemble.properties?.states ?? {}, root);
    const conformer = resolve(states.items ?? {}, root);
    return { model_id, mode, parameters: schemaParameters(request, root, parameters), inputParameters: schemaParameters(conformer, root) };
}

export function hydrateGeneralSequenceDesign(values?: Record<string, UntypedApiValue>): GeneralSequenceDesignDraft {
    const saved = values?.sequence_design as GeneralSequenceDesignRequest | undefined;
    const sequence_design: GeneralSequenceDesignRequest = saved ? structuredClone(saved) : {
        schema_version: 1, enabled: false, model_id: 'fampnn', params: {},
    };
    sequence_design.params = unbound(sequence_design.params ?? {});
    if (sequence_design.input_settings) sequence_design.input_settings = unbound(sequence_design.input_settings);
    const drafts = structuredClone(values?.sequence_design_drafts ?? {}) as GeneralSequenceDesignDraft['sequence_design_drafts'];
    const current = drafts[sequence_design.model_id];
    sequence_design.params = { ...current?.params, ...sequence_design.params };
    if (sequence_design.model_id === 'caliby_experimental') sequence_design.input_settings = { ...current?.input_settings, ...sequence_design.input_settings };
    drafts[sequence_design.model_id] = { params: sequence_design.params, ...(sequence_design.input_settings !== undefined ? { input_settings: sequence_design.input_settings } : {}) };
    return { sequence_design, sequence_design_drafts: drafts };
}
const defaults = (parameters: GeneralSequenceParameter[]) => Object.fromEntries(parameters.filter(parameter => Object.hasOwn(parameter, 'default')).map(parameter => [parameter.name, parameter.default]));
export function withGeneralSequenceInventory(draft: GeneralSequenceDesignDraft, inventory: GeneralSequenceInventory): GeneralSequenceDesignDraft {
    const previous = draft.sequence_design_drafts[inventory.model_id];
    const settings = { params: unbound({ ...defaults(inventory.parameters), ...previous?.params }),
        ...(inventory.model_id === 'caliby_experimental' ? { input_settings: { ...defaults(inventory.inputParameters), ...previous?.input_settings } } : {}) };
    return { sequence_design: draft.sequence_design.model_id === inventory.model_id ? { ...draft.sequence_design, ...settings } : draft.sequence_design,
        sequence_design_drafts: { ...draft.sequence_design_drafts, [inventory.model_id]: settings } };
}
export function changeGeneralSequenceDesigner(draft: GeneralSequenceDesignDraft, model_id: GeneralSequenceDesigner, patch?: Partial<GeneralSequenceSettings>): GeneralSequenceDesignDraft {
    const previous = draft.sequence_design_drafts[model_id];
    const settings = { params: unbound({ ...previous?.params, ...patch?.params }),
        ...(model_id === 'caliby_experimental' ? { input_settings: unbound({ ...previous?.input_settings, ...patch?.input_settings }) } : {}) };
    return { sequence_design: { schema_version: 1, enabled: draft.sequence_design.enabled, model_id, ...settings },
        sequence_design_drafts: { ...draft.sequence_design_drafts, [model_id]: settings } };
}
/** Only the active stage crosses Jobs/placement. Disabled authoring stays saved, not launched. */
export function generalSequenceRequest(draft: GeneralSequenceDesignDraft): { sequence_design?: GeneralSequenceDesignRequest } {
    return draft.sequence_design.enabled ? { sequence_design: draft.sequence_design } : {};
}
