import type { ChangeEvent } from 'react';

import {
    updateFrustraMpnnSourceStructure,
    type FrustraMpnnRequestedSettings,
    type FrustraMpnnSourceInspection,
} from './frustraMpnnSettingsState.js';

interface FrustraMpnnSourceStructurePolicyControlProps {
    value: FrustraMpnnRequestedSettings;
    onChange: (value: FrustraMpnnRequestedSettings) => void;
    inspection?: FrustraMpnnSourceInspection | null;
}

export function FrustraMpnnSourceStructurePolicyControl({
    value,
    onChange,
    inspection,
}: FrustraMpnnSourceStructurePolicyControlProps) {
    const source = value.source_structure;
    const modelChoices = inspection?.source_models ?? [];
    const observedAltlocs = inspection?.observed_altlocs ?? [''];
    const hasCurrentModel = modelChoices.includes(source.selected_model_number);
    const hasCurrentAltloc = observedAltlocs.includes(source.preferred_altloc);

    const updateModel = (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
        const modelNumber = Number(event.target.value);
        if (!Number.isInteger(modelNumber) || modelNumber < 1) return;
        onChange(updateFrustraMpnnSourceStructure(value, {
            ...source,
            selected_model_number: modelNumber,
        }));
    };

    const updateAltloc = (event: ChangeEvent<HTMLSelectElement>) => {
        onChange(updateFrustraMpnnSourceStructure(value, {
            ...source,
            preferred_altloc: event.target.value,
        }));
    };

    return (
        <fieldset className="space-y-3 rounded border border-slate-200 p-3">
            <legend className="px-1 text-sm font-semibold text-slate-800">Source structure policy</legend>
            <label className="block text-sm text-slate-700">
                Source model
                {inspection ? (
                    <select
                        data-frustrampnn-source-model
                        className="mt-1 block w-full rounded border border-slate-300 px-2 py-1.5"
                        value={source.selected_model_number}
                        onChange={updateModel}
                    >
                        {!hasCurrentModel && (
                            <option value={source.selected_model_number} disabled>
                                Model {source.selected_model_number} (not observed)
                            </option>
                        )}
                        {modelChoices.map((modelNumber) => (
                            <option key={modelNumber} value={modelNumber}>Model {modelNumber}</option>
                        ))}
                    </select>
                ) : (
                    <input
                        data-frustrampnn-source-model
                        type="number"
                        min={1}
                        step={1}
                        className="mt-1 block w-full rounded border border-slate-300 px-2 py-1.5"
                        value={source.selected_model_number}
                        onChange={updateModel}
                    />
                )}
            </label>

            <label className="block text-sm text-slate-700">
                Preferred alternate location
                <select
                    data-frustrampnn-altloc
                    className="mt-1 block w-full rounded border border-slate-300 px-2 py-1.5"
                    value={source.preferred_altloc}
                    onChange={updateAltloc}
                >
                    {!hasCurrentAltloc && source.preferred_altloc !== '' && (
                        <option value={source.preferred_altloc} disabled>
                            {source.preferred_altloc} (not observed)
                        </option>
                    )}
                    {observedAltlocs.map((altloc) => (
                        <option key={altloc || 'blank'} value={altloc}>
                            {altloc === '' ? 'Blank (occupancy policy)' : altloc}
                        </option>
                    ))}
                </select>
            </label>
            {!inspection && (
                <p className="text-xs text-slate-500">
                    Source choices will be constrained after exact source inspection is produced.
                </p>
            )}
        </fieldset>
    );
}
