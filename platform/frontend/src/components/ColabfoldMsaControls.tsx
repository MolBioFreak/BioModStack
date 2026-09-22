import React from 'react';
import { MSA_POLICY, type ColabfoldMsaSettings } from '../lib/msaPolicy';
export function ColabfoldMsaControls({ value, onChange, disabled = false }: {
    value: ColabfoldMsaSettings;
    onChange: (value: ColabfoldMsaSettings) => void;
    disabled?: boolean;
}): React.JSX.Element {
    return <fieldset disabled={disabled} className="space-y-3 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3">
        <legend className="px-1 text-xs font-medium text-[var(--text-secondary)]">ColabFold API scientific settings</legend>
        {Object.entries(MSA_POLICY.colabfold_settings).map(([name, field]) => {
            const key = name as keyof ColabfoldMsaSettings;
            return <label key={key} className="block space-y-1 text-sm text-[var(--text-primary)]">
                <span className="block">{field.native_name}</span>
                {field.type === 'boolean' ? <input type="checkbox" checked={value[key] as boolean}
                    onChange={e => onChange({ ...value, [key]: e.target.checked })} />
                    : <select className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1 text-sm text-[var(--text-primary)]" value={String(value[key])} onChange={e => onChange({ ...value, [key]: e.target.value })}>
                        {'enum' in field && field.enum.map(option => <option key={option} value={option}>{option}</option>)}
                    </select>}
                <span className="block text-xs leading-relaxed text-[var(--text-muted)]">{field.description}. Default: {String(field.default)}</span>
            </label>;
        })}
    </fieldset>;
}
