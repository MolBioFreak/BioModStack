import React from 'react';
import { MSA_POLICY, type NeurosnapMsaSettings } from '../lib/msaPolicy';

export function NeurosnapMsaControls({ value, onChange, disabled = false }: {
    value: NeurosnapMsaSettings;
    onChange: (value: NeurosnapMsaSettings) => void;
    disabled?: boolean;
}): React.JSX.Element {
    return <fieldset disabled={disabled} className="space-y-3 rounded border border-slate-600 p-3">
        <legend>Neurosnap mmseqs2 MSA Generation</legend>
        <p className="text-xs">{MSA_POLICY.disclosure} Output compatibility is validated before submission. Uppercase and padding are not supported for Protenix A3M consumption.</p>
        {Object.entries(MSA_POLICY.neurosnap_settings).map(([name, field]) => {
            const key = name as keyof NeurosnapMsaSettings;
            return <label key={key} className="block text-sm">
                {field.native_name}
                {field.type === 'boolean' ? <input type="checkbox" checked={value[key] as boolean}
                    onChange={event => onChange({ ...value, [key]: event.target.checked })} />
                    : <input type="number" value={value[key] as number}
                        min={'minimum' in field ? field.minimum : undefined}
                        max={'maximum' in field ? field.maximum : undefined}
                        step={key === 'msa_neurosnap_max_sequences' ? 1 : 'any'}
                        onChange={event => onChange({ ...value, [key]: event.target.valueAsNumber })} />}
                <span className="block text-xs">{field.description}. Default: {String(field.default)}
                    {'minimum' in field ? `; range ${field.minimum}–${field.maximum}; provider UI step ${field.ui_step}` : ''}</span>
            </label>;
        })}
    </fieldset>;
}
