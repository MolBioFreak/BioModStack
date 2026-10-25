import { BioXpSchemaInput } from './BioXpSchemaInput';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';
export const rfd3AdditionalConditionKeys = ['unindex', 'select_buried', 'select_exposed', 'select_partially_buried', 'symmetry', 'length', 'ori_token', 'infer_ori_strategy', 'plddt_enhanced', 'is_non_loopy', 'redesign_motif_sidechains'] as const;
export function retainedRfd3Conditions(value: Record<string, unknown> = {}) {
    return Object.fromEntries(rfd3AdditionalConditionKeys.filter(key => Object.hasOwn(value, key)).map(key => [key, value[key]]));
}
/** Complements the existing source-bound coordinate/recall workspace. */
export function Rfd3AdditionalConditions({ definitions, value, onChange }: {
    definitions: Record<string, Schema>; value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void;
}) {
    const patch = (key: string, next: unknown) => { const updated = { ...value }; if (next === undefined) delete updated[key]; else updated[key] = next; onChange(updated); };
    const schemaFor = (definition: Schema): Schema => {
        if (definition.type === 'selection') return { anyOf: [{ type: 'string' }, { type: 'boolean' }, { type: 'object', additionalProperties: { anyOf: [{ type: 'string' }, { type: 'array', items: { type: 'string' } }] } }, { type: 'null' }] };
        if (definition.type === 'vector3') return { type: 'array', items: { type: 'number' }, minItems: 3, maxItems: 3 };
        if (definition.type === 'boolean_or_string') return { anyOf: [{ type: 'boolean' }, { type: 'string' }] };
        return definition;
    };
    return <NativeSettingsDisclosure summary="Additional source conditions and unindexed atoms">{() => <div className="space-y-3">
        <p>Native selectors refer to the prepared source. These conditions are independent of the coordinate and sequence roles above; visual selection does not overwrite them. Partial diffusion time retains native units, not a percentage.</p>
        {rfd3AdditionalConditionKeys.filter(key => definitions[key]).map(key => key === 'symmetry'
            ? <p key={key}>Symmetry is retained unchanged: {JSON.stringify(value[key] ?? 'not requested')}. Full installed SymmetryConfig authoring is not yet available in this workspace.</p>
            : <div key={key}><BioXpSchemaInput label={key} nativeLabels schema={schemaFor(definitions[key])} value={value[key]} onChange={next => patch(key, next)} fallback={() => <p>Retained {key}: {JSON.stringify(value[key])}</p>} />{Object.hasOwn(value, key) && <button type="button" onClick={() => patch(key, undefined)}>Omit {key}</button>}</div>)}
    </div>}</NativeSettingsDisclosure>;
}
