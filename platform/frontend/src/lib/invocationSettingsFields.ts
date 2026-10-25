import type { BioXpOperatorJsonSchema as Schema } from './bioxpClient';
import type { BinderRoundCatalog } from './binderRound';
import { roundParameterIsBound } from './binderRound';
import type { NativeBinderParameter } from './nativeBinderAuthoring';

export type InvocationField = NativeBinderParameter & { schema?: Schema };
export type InvocationCatalog = Omit<BinderRoundCatalog, 'modes'> & { modes?: Array<{ id: string; params?: string[]; parameter_schema?: Schema | null }> };
export const invocationFieldIsBound = (name: string) => (roundParameterIsBound(name) && !['design_chain', 'target_chain', 'binder_chains', 'target_chains'].includes(name)) || ['ensembles', 'structures', 'input_json', 'caliby_request_dir', 'input_path', 'target_pdb'].includes(name);
/** Join the actual mode schema to catalog presentation, without changing requests. */
export function invocationSettingsFields(catalog: InvocationCatalog, modeId?: string): InvocationField[] {
    const mode = catalog.modes?.find(mode => mode.id === modeId);
    const root = mode?.parameter_schema;
    const branch = root?.oneOf?.[0] ?? root;
    const resolved = branch?.$ref?.startsWith('#/$defs/') ? root?.$defs?.[branch.$ref.slice(8)] : branch;
    const properties = resolved?.properties ?? {};
    // Only native chemistry supported by the blind round receiver is added.
    const chemistry = catalog.id === 'boltz2' ? ['constraints', 'properties'] : catalog.id === 'esmfold2' ? ['covalent_bonds'] : [];
    const names = [...new Set([...catalog.params.map(field => field.name), ...chemistry.filter(name => properties[name])])];
    return names.filter(name => !invocationFieldIsBound(name) && !['schema_version', 'task', 'constraint', 'templates', 'distogram_conditioning'].includes(name)).map(name => {
        const original = catalog.params.find(field => field.name === name);
        const nativeSchema = properties[name];
        const schema = catalog.id === 'boltz2' && name === 'constraints' && nativeSchema?.items && !Array.isArray(nativeSchema.items)
            ? { ...nativeSchema, items: nativeSchema.items.oneOf?.find(branch => branch.properties?.bond) ?? nativeSchema.items } : nativeSchema;
        return { ...original, name, type: original?.type ?? (typeof schema?.type === 'string' ? schema.type : 'json'),
            display_applicable: !mode?.params || mode.params.includes(name) || !!schema,
            ...(schema ? { schema: { ...schema, $defs: root?.$defs } } : {}) } as InvocationField;
    });
}
