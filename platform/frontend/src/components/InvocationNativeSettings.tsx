import type { ComponentProps } from 'react';
import { BioXpSchemaInput, schemaEditableValue } from './BioXpSchemaInput';
import { BioXpJsonInput } from './BioXpOperatorInput';
import { NativeSetting } from './NativeBinderGeneration';
import { roundControlGroups, roundControlLabel } from '../lib/binderRoundControls';
import { blindFixedParameters } from '../lib/binderRound';
import type { NativeBinderParameter } from '../lib/nativeBinderAuthoring';
import type { SelectedRoundModel } from '../lib/binderSelectedRound';

/** Display discovery defaults, but emit only deliberate sparse edits. */
export function InvocationNativeSettings({ call, fields, prediction, onChange, showAdditional = true }: {
    showAdditional?: boolean;
    call: SelectedRoundModel; fields: NativeBinderParameter[]; prediction: boolean;
    onChange: (params: SelectedRoundModel['params']) => void;
}) {
    const effective = { ...Object.fromEntries(fields.filter(f => Object.hasOwn(f, 'default')).map(f => [f.name, f.default])), ...call.params };
    const groups = roundControlGroups(call.model_id, fields, effective);
    const render = (field: NativeBinderParameter & { schema?: ComponentProps<typeof BioXpSchemaInput>['schema'] }) => {
        const explicit = Object.hasOwn(call.params, field.name);
        const patch = (values: Record<string, UntypedApiValue>) => onChange({ ...call.params, ...values, ...(prediction ? Object.fromEntries(fields.filter(f => blindFixedParameters.has(f.name)).map(f => [f.name, false])) : {}) });
        return <div key={field.name} className="min-w-0 space-y-2" data-operation-field={field.name}>
            <p className="text-xs">{explicit ? 'Requested override' : Object.hasOwn(field, 'default') ? 'Inherited native default' : 'Not specified · native owner resolves'}</p>
            {prediction && blindFixedParameters.has(field.name) ? <p>{roundControlLabel(field).label}: off for blind prediction (no generated-pose conditioning).</p>
                : field.schema
                    ? <><button type="button" onClick={() => patch({ [field.name]: schemaEditableValue(field.schema!) })}>Set explicit {field.name}</button><BioXpSchemaInput label={field.name} schema={field.schema} value={effective[field.name]} onChange={value => patch({ [field.name]: value })} fallback={(label, value, onChange) => <BioXpJsonInput label={label} value={value} onChange={onChange} />} /></>
                    : field.type === 'string_list' ? <BioXpSchemaInput label={field.name} schema={{ type: 'array', items: { type: 'string' } }} value={effective[field.name] === undefined ? [] : effective[field.name]} onChange={value => patch({ [field.name]: value })} fallback={(label, value, onChange) => <BioXpJsonInput label={label} value={value} onChange={onChange} />} />
                    : ['array', 'object', 'json'].includes(field.type) ? <BioXpJsonInput label={field.name} value={effective[field.name]} onChange={value => patch({ [field.name]: value })} />
                    : <NativeSetting parameter={roundControlLabel(field)} values={call.params} displayValues={effective} onPatch={patch} chains={[]} />}
            {explicit && <button type="button" onClick={() => { const params = { ...call.params }; delete params[field.name]; onChange(params); }}>Remove override: {roundControlLabel(field).label}</button>}
        </div>;
    };
    return <div className="min-w-0 space-y-3">
        {prediction && showAdditional && <p>Blind prediction excludes templates, Protenix pose constraints, Boltz pocket/contact restraints and ESMFold2 distogram conditioning. Native bond chemistry remains model-owned.</p>}
        <div className="grid min-w-0 gap-4 md:grid-cols-2">{groups.primary.map(render)}</div>
        {[...groups.groups].map(([name, parameters]) => <details key={name}><summary>{name}</summary><div className="grid min-w-0 gap-4 md:grid-cols-2">{parameters.map(render)}</div></details>)}
        {showAdditional && !!Object.keys(call.params).filter(key => !fields.some(f => f.name === key)).length && <details><summary>Additional retained native fields</summary><p>These values remain in the request; source-bound inputs are supplied by the existing adapter.</p><ul>{Object.keys(call.params).filter(key => !fields.some(f => f.name === key)).map(key => <li key={key}><BioXpJsonInput label={key} value={call.params[key]} onChange={value => onChange({ ...call.params, [key]: value })} /></li>)}</ul></details>}
    </div>;
}
