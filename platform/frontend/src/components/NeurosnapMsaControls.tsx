import React from 'react';
import { MSA_POLICY, type NeurosnapMsaSettings } from '../lib/msaPolicy';

export function NeurosnapMsaControls({ value, onChange, disabled = false }: {
    value: NeurosnapMsaSettings;
    onChange: (value: NeurosnapMsaSettings) => void;
    disabled?: boolean;
}): React.JSX.Element {
    return <fieldset disabled={disabled} className="space-y-3 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3">
        <legend className="px-1 text-xs font-medium text-[var(--text-secondary)]">Neurosnap mmseqs2 MSA Generation</legend>
        <p className="text-xs leading-relaxed text-[var(--text-muted)]">{MSA_POLICY.disclosure} Output compatibility is validated before submission. Uppercase and padding are not supported for Protenix A3M consumption.</p>
        {Object.entries(MSA_POLICY.neurosnap_settings).map(([name, field]) => {
            const key = name as keyof NeurosnapMsaSettings;
            return <label key={key} className="block space-y-1 text-sm text-[var(--text-primary)]">
                <span className="block">{field.native_name}</span>
                {field.type === 'boolean' ? <input type="checkbox" checked={value[key] as boolean}
                    onChange={event => onChange({ ...value, [key]: event.target.checked })} />
                    : <input type="number" className="w-32 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1 text-sm text-[var(--text-primary)]" value={value[key] as number}
                        min={'minimum' in field ? field.minimum : undefined}
                        max={'maximum' in field ? field.maximum : undefined}
                        step={key === 'msa_neurosnap_max_sequences' ? 1 : 'any'}
                        onChange={event => onChange({ ...value, [key]: event.target.valueAsNumber })} />}
                <span className="block text-xs leading-relaxed text-[var(--text-muted)]">{field.description}. Default: {String(field.default)}
                    {'minimum' in field ? `; range ${field.minimum}–${field.maximum}; provider UI step ${field.ui_step}` : ''}</span>
            </label>;
        })}
    </fieldset>;
}
