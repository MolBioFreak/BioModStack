import { BioXpSchemaInput, schemaEditableValue } from './BioXpSchemaInput';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';
export const rfd3AdditionalConditionKeys = ['unindex', 'select_buried', 'select_exposed', 'select_partially_buried', 'symmetry', 'length', 'ori_token', 'infer_ori_strategy', 'plddt_enhanced', 'is_non_loopy', 'redesign_motif_sidechains'] as const;
export function retainedRfd3Conditions(value: Record<string, unknown> = {}) {
    return Object.fromEntries(rfd3AdditionalConditionKeys.filter(key => Object.hasOwn(value, key)).map(key => [key, value[key]]));
}
/** General generation passes its nested conditioning; redesign passes its existing flat draft. */
export function Rfd3AdditionalConditions({ definitions, value, onChange, general = false }: {
    definitions: Record<string, Schema>; value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void; general?: boolean;
}) {
    const patch = (key: string, next: unknown) => { const updated = { ...value }; if (next === undefined) delete updated[key]; else updated[key] = next; onChange(updated); };
    const schemaFor = (definition: Schema): Schema => {
        let schema = definition;
        if (definition.type === 'selection') schema = { ...definition, anyOf: [{ type: 'string' }, { type: 'boolean' }, { type: 'object', additionalProperties: { anyOf: [{ type: 'string' }, { type: 'array', items: { type: 'string' } }] } }] };
        if (definition.type === 'vector3') schema = { ...definition, type: 'array', items: { type: 'number' }, minItems: 3, maxItems: 3 };
        if (definition.type === 'boolean_or_string') schema = { ...definition, anyOf: [{ type: 'boolean' }, { type: 'string' }] };
        // Properties/defaults are the adopted SymmetryConfig inventory, never browser inventions.
        if (definition.type === 'symmetry') schema = { ...definition, type: 'object', properties: Object.fromEntries(Object.entries(definition.properties ?? {}).map(([name, leaf]) => {
            if (!Array.isArray(leaf.type)) return [name, leaf];
            // Keep the native nullable default for presence activation, but a
            // deliberate string variant must start a string, not reapply null.
            const { type, default: _default, ...branch } = leaf;
            return [name, { ...leaf, anyOf: type.map(kind => ({ ...branch, type: kind })) }];
        })) };
        return { ...schema, anyOf: [...(schema.anyOf ?? [schema]), { type: 'null' }] };
    };
    return <NativeSettingsDisclosure summary={general ? 'Conditions' : 'Additional source conditions and unindexed atoms'}>{() => <div className="space-y-3">
        <p>{general ? 'Conditions are optional. Symmetry can be used without an input structure. Source selectors refer to the prepared source and selected MODEL.' : 'Native selectors refer to the prepared source. These conditions are independent of the coordinate and sequence roles above; visual selection does not overwrite them. Partial diffusion time retains native units, not a percentage.'}</p>
        {(general ? Object.keys(definitions) : rfd3AdditionalConditionKeys).filter(key => definitions[key]).map(key => {
            const schema = schemaFor(definitions[key]);
            return <div key={key}>
                {!Object.hasOwn(value, key) ? <><span>{definitions[key].title ?? key}</span><button type="button" onClick={() => patch(key, schemaEditableValue(schema))}>Set {key}</button></> : <>
                    <BioXpSchemaInput label={key} nativeLabels schema={schema} value={value[key]} onChange={next => patch(key, next)} fallback={() => <p>Retained {key}: {JSON.stringify(value[key])}</p>} />
                    <button type="button" onClick={() => patch(key, undefined)}>Omit {key}</button>
                </>}
            </div>;
        })}
    </div>}</NativeSettingsDisclosure>;
}
