import type { ComponentProps } from 'react';
import { BioXpSchemaInput } from './BioXpSchemaInput';
import { NativeSetting } from './NativeBinderGeneration';
import { roundControlGroups, roundControlLabel } from '../lib/binderRoundControls';
import { blindFixedParameters } from '../lib/binderRound';
import type { NativeBinderParameter } from '../lib/nativeBinderAuthoring';
import type { SelectedRoundModel } from '../lib/binderSelectedRound';

/** Display discovery defaults, but emit only deliberate sparse edits. */
export function InvocationNativeSettings({ call, fields, prediction, onChange }: {
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
                : field.schema && ['array', 'object', 'json'].includes(field.type)
                    ? <BioXpSchemaInput label={field.name} schema={field.schema} value={effective[field.name]} onChange={value => patch({ [field.name]: value })} fallback={() => <p>Saved native value retained; typed schema unavailable.</p>} />
                    : <NativeSetting parameter={roundControlLabel(field)} values={call.params} displayValues={effective} onPatch={patch} chains={[]} />}
            {explicit && <button type="button" onClick={() => { const params = { ...call.params }; delete params[field.name]; onChange(params); }}>Remove override: {roundControlLabel(field).label}</button>}
        </div>;
    };
    return <div className="min-w-0 space-y-3">
        <div className="grid min-w-0 gap-4 md:grid-cols-2">{groups.primary.map(render)}</div>
        {[...groups.groups].map(([name, parameters]) => <details key={name}><summary>{name}</summary><div className="grid min-w-0 gap-4 md:grid-cols-2">{parameters.map(render)}</div></details>)}
        {!!Object.keys(call.params).filter(key => !fields.some(f => f.name === key)).length && <details><summary>Additional retained native fields</summary><p>These values remain in the request; source-bound inputs are supplied by the existing adapter.</p><ul>{Object.keys(call.params).filter(key => !fields.some(f => f.name === key)).map(key => <li key={key}>{key} · retained</li>)}</ul></details>}
    </div>;
}
