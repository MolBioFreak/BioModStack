import { useState, type ChangeEvent } from 'react';

import {
    updateFrustraMpnnClassificationPolicy,
    type FrustraMpnnClassificationMode,
    type FrustraMpnnRequestedSettings,
} from './frustraMpnnSettingsState.js';

interface FrustraMpnnClassificationPolicyControlProps {
    value: FrustraMpnnRequestedSettings;
    onChange: (value: FrustraMpnnRequestedSettings) => void;
}

export function FrustraMpnnClassificationPolicyControl({
    value,
    onChange,
}: FrustraMpnnClassificationPolicyControlProps) {
    const [validationError, setValidationError] = useState<string | null>(null);
    const policy = value.classification_policy;

    const updateMode = (event: ChangeEvent<HTMLSelectElement>) => {
        const mode = event.target.value as FrustraMpnnClassificationMode;
        setValidationError(null);
        onChange(updateFrustraMpnnClassificationPolicy(value, mode === 'canonical'
            ? { mode, high_max: -1, minimal_min: 0.58 }
            : { mode, high_max: policy.high_max, minimal_min: policy.minimal_min }));
    };

    const updateThreshold = (field: 'high_max' | 'minimal_min', rawValue: string) => {
        const nextValue = Number(rawValue);
        if (!Number.isFinite(nextValue)) {
            setValidationError('Classification thresholds must be finite numbers.');
            return;
        }
        const nextPolicy = { ...policy, [field]: nextValue };
        if (nextPolicy.high_max >= nextPolicy.minimal_min) {
            setValidationError('High-frustration maximum must be less than minimal-frustration minimum.');
            return;
        }
        setValidationError(null);
        onChange(updateFrustraMpnnClassificationPolicy(value, nextPolicy));
    };

    return (
        <fieldset className="space-y-3 rounded border border-slate-200 p-3">
            <legend className="px-1 text-sm font-semibold text-slate-800">Classification policy</legend>
            <label className="block text-sm text-slate-700">
                Policy
                <select
                    data-frustrampnn-classification-mode
                    className="mt-1 block w-full rounded border border-slate-300 px-2 py-1.5"
                    value={policy.mode}
                    onChange={updateMode}
                >
                    <option value="canonical">Canonical thresholds</option>
                    <option value="custom">Custom thresholds</option>
                </select>
            </label>
            <p className="text-xs text-slate-500">
                Canonical classification marks scores at or below −1 as highly frustrated and scores at or above 0.58 as minimally frustrated. Scores between those limits are neutral. Custom thresholds change classification only; they do not alter the underlying FrustraMPNN scores.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
                <label className="block text-sm text-slate-700">
                    High maximum
                    <input
                        data-frustrampnn-high-max
                        type="number"
                        step="any"
                        disabled={policy.mode === 'canonical'}
                        className="mt-1 block w-full rounded border border-slate-300 px-2 py-1.5 disabled:bg-slate-100"
                        value={policy.high_max}
                        onChange={(event) => updateThreshold('high_max', event.target.value)}
                    />
                </label>
                <label className="block text-sm text-slate-700">
                    Minimal minimum
                    <input
                        data-frustrampnn-minimal-min
                        type="number"
                        step="any"
                        disabled={policy.mode === 'canonical'}
                        className="mt-1 block w-full rounded border border-slate-300 px-2 py-1.5 disabled:bg-slate-100"
                        value={policy.minimal_min}
                        onChange={(event) => updateThreshold('minimal_min', event.target.value)}
                    />
                </label>
            </div>
            {validationError && <p role="alert" className="text-xs text-red-700">{validationError}</p>}
            <p className="text-xs text-slate-500">Required ordering: high maximum &lt; minimal minimum.</p>
        </fieldset>
    );
}
