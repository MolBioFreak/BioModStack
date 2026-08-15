import type { ChangeEvent } from 'react';

import {
    frustraMpnnEntitySelectorKey,
    frustraMpnnResidueSelectorKey,
    getFrustraMpnnSelectionModeOptions,
    selectFrustraMpnnProteinSelectionMode,
    setFrustraMpnnEntitySelected,
    setFrustraMpnnResidueSelected,
    type FrustraMpnnProteinSelectionMode,
    type FrustraMpnnRequestedSettings,
    type FrustraMpnnSourceInspection,
} from './frustraMpnnSettingsState.js';

interface FrustraMpnnProteinSelectionControlProps {
    value: FrustraMpnnRequestedSettings;
    onChange: (value: FrustraMpnnRequestedSettings) => void;
    inspection?: FrustraMpnnSourceInspection | null;
}

const MODE_LABELS: Record<FrustraMpnnProteinSelectionMode, string> = {
    all_protein_entities: 'All protein entities',
    selected_entities: 'Selected entities / chains',
    selected_residues: 'Selected residues',
};

export function FrustraMpnnProteinSelectionControl({
    value,
    onChange,
    inspection,
}: FrustraMpnnProteinSelectionControlProps) {
    const options = getFrustraMpnnSelectionModeOptions(inspection);
    const selection = value.protein_selection;

    const handleMode = (event: ChangeEvent<HTMLSelectElement>) => {
        onChange(selectFrustraMpnnProteinSelectionMode(
            value,
            event.target.value as FrustraMpnnProteinSelectionMode,
            inspection,
        ));
    };

    return (
        <fieldset className="space-y-3 rounded border border-slate-200 p-3">
            <legend className="px-1 text-sm font-semibold text-slate-800">Protein selection</legend>
            <label className="block text-sm text-slate-700">
                Selection mode
                <select
                    data-frustrampnn-selection-mode
                    className="mt-1 block w-full rounded border border-slate-300 px-2 py-1.5"
                    value={selection.mode}
                    onChange={handleMode}
                >
                    {options.map((option) => (
                        <option key={option.mode} value={option.mode} disabled={!option.available}>
                            {MODE_LABELS[option.mode]}
                        </option>
                    ))}
                </select>
            </label>

            <p className="text-xs text-slate-500">
                All protein entities analyzes every mapped protein residue in the governed structure. Residues that cannot be mapped or scored remain outside the landscape, as do DNA, RNA, ligands, ions, and solvent. Choose a narrower mode to restrict analysis to exact chains or residues.
            </p>

            {!inspection && (
                <p className="text-xs text-slate-500">
                    Exact source entity and residue selectors are unavailable until source inspection is produced.
                </p>
            )}

            {selection.mode === 'selected_entities' && inspection && (
                <div className="space-y-2" aria-label="Inspected protein entities">
                    {inspection.protein_entities.map((entity) => {
                        const selectorKey = frustraMpnnEntitySelectorKey(entity);
                        const checked = selection.entities.some((item) => (
                            frustraMpnnEntitySelectorKey(item) === selectorKey
                        ));
                        return (
                            <label
                                key={selectorKey}
                                data-frustrampnn-entity-option
                                className="flex items-start gap-2 text-sm text-slate-700"
                            >
                                <input
                                    type="checkbox"
                                    checked={checked}
                                    disabled={checked && selection.entities.length === 1}
                                    onChange={(event) => onChange(setFrustraMpnnEntitySelected(
                                        value,
                                        entity,
                                        event.target.checked,
                                    ))}
                                />
                                <span>
                                    Chain {entity.auth_asym_id}
                                    {entity.label_asym_id ? ` · label ${entity.label_asym_id}` : ''}
                                    {' · '}{entity.entity_instance_id}
                                </span>
                            </label>
                        );
                    })}
                </div>
            )}

            {selection.mode === 'selected_residues' && inspection && (
                <div className="max-h-64 space-y-2 overflow-auto" aria-label="Inspected mapped residues">
                    {inspection.mapped_residues.map((residue) => {
                        const selectorKey = frustraMpnnResidueSelectorKey(residue);
                        const checked = selection.residues.some((item) => (
                            frustraMpnnResidueSelectorKey(item) === selectorKey
                        ));
                        const residueLabel = `${residue.wt} ${residue.auth_asym_id}:${residue.auth_seq_id}${residue.insertion_code}`;
                        return (
                            <label
                                key={selectorKey}
                                data-frustrampnn-residue-option
                                className="flex items-start gap-2 text-sm text-slate-700"
                            >
                                <input
                                    type="checkbox"
                                    checked={checked}
                                    disabled={checked && selection.residues.length === 1}
                                    onChange={(event) => onChange(setFrustraMpnnResidueSelected(
                                        value,
                                        residue,
                                        event.target.checked,
                                    ))}
                                />
                                <span>{residueLabel} · sequence index {residue.sequence_index}</span>
                            </label>
                        );
                    })}
                </div>
            )}
        </fieldset>
    );
}
