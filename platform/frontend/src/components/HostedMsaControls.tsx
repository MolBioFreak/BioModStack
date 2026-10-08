import { ColabfoldMsaControls } from './ColabfoldMsaControls';
import { NeurosnapMsaControls } from './NeurosnapMsaControls';
import { MSA_POLICY, type HostedMsaSettings } from '../lib/msaPolicy';

/** Shared hosted inventory, not a model-local copy of provider science. */
export function HostedMsaControls({ value, onChange, disabled = false }: {
    value: HostedMsaSettings;
    onChange: (value: HostedMsaSettings) => void;
    disabled?: boolean;
}) {
    return <fieldset disabled={disabled} className="space-y-3 rounded border border-slate-600 p-3">
        <legend>Hosted protein MSA settings</legend>
        <p className="text-xs">{MSA_POLICY.disclosure}</p>
        <label className="block text-sm">MSA provider
            <select value={value.msa_provider} onChange={event => onChange({ ...value, msa_provider: event.target.value as HostedMsaSettings['msa_provider'] })}>
                {MSA_POLICY.enabled_search_backends.map(provider => <option key={provider} value={provider}>{provider}</option>)}
                {value.msa_provider === 'auto' && <option value="auto">Saved auto (ColabFold API)</option>}
                {value.msa_provider === 'local' && <option value="local">Saved local (unsupported; select hosted provider)</option>}
            </select>
        </label>
        {value.msa_provider === 'local' && <p role="alert">{MSA_POLICY.local_disabled}</p>}
        <p className="text-xs">Only the selected provider runs. Inactive provider settings remain editable and are preserved for saved requests and provider changes.</p>
        <ColabfoldMsaControls value={value} onChange={settings => onChange({ ...value, ...settings })} disabled={disabled} />
        <NeurosnapMsaControls value={value} onChange={settings => onChange({ ...value, ...settings })} disabled={disabled} />
    </fieldset>;
}
