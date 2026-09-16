import React from 'react';
import { MSA_POLICY, type ColabfoldMsaSettings } from '../lib/msaPolicy';
export function ColabfoldMsaControls({ value, onChange, disabled = false }: {
    value: ColabfoldMsaSettings;
    onChange: (value: ColabfoldMsaSettings) => void;
    disabled?: boolean;
}): React.JSX.Element {
    return <fieldset disabled={disabled} className="space-y-3 rounded border border-slate-600 p-3">
        <legend>ColabFold API scientific settings</legend>
        {Object.entries(MSA_POLICY.colabfold_settings).map(([name, field]) => {
            const key = name as keyof ColabfoldMsaSettings;
            return <label key={key} className="block text-sm">{field.native_name}
                {field.type === 'boolean' ? <input type="checkbox" checked={value[key] as boolean}
                    onChange={e => onChange({ ...value, [key]: e.target.checked })} />
                    : <select value={String(value[key])} onChange={e => onChange({ ...value, [key]: e.target.value })}>
                        {'enum' in field && field.enum.map(option => <option key={option} value={option}>{option}</option>)}
                    </select>}
                <span className="block text-xs">{field.description}. Default: {String(field.default)}</span>
            </label>;
        })}
    </fieldset>;
}
