import { useState, type ChangeEvent } from 'react';

import {
    addFrustraMpnnRegion,
    frustraMpnnEntitySelectorKey,
    frustraMpnnRegionSelectorKey,
    frustraMpnnResidueSelectorKey,
    getFrustraMpnnInspectionRegionBounds,
    getFrustraMpnnSelectionModeOptions,
    removeFrustraMpnnRegion,
    selectFrustraMpnnProteinSelectionMode,
    setFrustraMpnnEntitySelected,
    setFrustraMpnnRegion,
    setFrustraMpnnResidueSelected,
    type FrustraMpnnEntitySelector,
    type FrustraMpnnProteinSelectionMode,
    type FrustraMpnnRequestedSettings,
    type FrustraMpnnSourceInspection,
} from './frustraMpnnSettingsState.js';

interface FrustraMpnnProteinSelectionControlProps {
    value: FrustraMpnnRequestedSettings;
    onChange: (value: FrustraMpnnRequestedSettings) => void;
    inspection?: FrustraMpnnSourceInspection | null;
    allowIndividualResidues?: boolean;
}

type FrustraMpnnSelectionDisplayMode =
    | FrustraMpnnProteinSelectionMode
    | 'selected_sequence_positions';

const MODE_LABELS: Record<FrustraMpnnSelectionDisplayMode, string> = {
    all_protein_entities: 'All protein residues',
    selected_entities: 'Selected chains',
    selected_regions: 'Selected regions',
    selected_residues: 'Individual residues (advanced)',
    selected_sequence_positions: 'Individual residues (advanced)',
};

const inspectedEntitySelector = (
    entity: FrustraMpnnSourceInspection['protein_entities'][number],
): FrustraMpnnEntitySelector => ({
    entity_instance_id: entity.entity_instance_id,
    source_entity_id: entity.source_entity_id,
    label_asym_id: entity.label_asym_id,
    auth_asym_id: entity.auth_asym_id,
});

const sourceEntityLabel = (entity: FrustraMpnnEntitySelector): string => (
    entity.auth_asym_id
        ? `Chain ${entity.auth_asym_id} · ${entity.entity_instance_id}`
        : `Source instance ${entity.entity_instance_id}`
);

export function FrustraMpnnProteinSelectionControl({
    value,
    onChange,
    inspection,
    allowIndividualResidues = true,
}: FrustraMpnnProteinSelectionControlProps) {
    const [diagnostic, setDiagnostic] = useState<string | null>(null);
    const baseOptions = getFrustraMpnnSelectionModeOptions(inspection);
    const regionOption = baseOptions.find((option) => option.mode === 'selected_regions');
    const options: Array<{
        mode: FrustraMpnnSelectionDisplayMode;
        available: boolean;
        reason?: string;
    }> = [
        ...baseOptions.filter((option) => (
        allowIndividualResidues || option.mode !== 'selected_residues'
        )),
        ...(!allowIndividualResidues ? [{
            mode: 'selected_sequence_positions' as const,
            available: Boolean(regionOption?.available),
            ...(regionOption?.reason ? { reason: regionOption.reason } : {}),
        }] : []),
    ];
    const selection = value.protein_selection;
    const displayMode: FrustraMpnnSelectionDisplayMode = (
        !allowIndividualResidues
        && selection.mode === 'selected_regions'
        && selection.regions.every((region) => region.sequence_start === region.sequence_end)
    ) ? 'selected_sequence_positions' : selection.mode;

    const apply = (next: () => FrustraMpnnRequestedSettings) => {
        try {
            onChange(next());
            setDiagnostic(null);
        } catch (error: unknown) {
            setDiagnostic(error instanceof Error ? error.message : 'Region selection is invalid.');
        }
    };

    const handleMode = (event: ChangeEvent<HTMLSelectElement>) => {
        const mode = event.target.value as FrustraMpnnSelectionDisplayMode;
        apply(() => {
            if (mode !== 'selected_sequence_positions') {
                return selectFrustraMpnnProteinSelectionMode(value, mode, inspection);
            }
            const selected = selectFrustraMpnnProteinSelectionMode(
                value,
                'selected_regions',
                inspection,
            );
            const first = selected.protein_selection.mode === 'selected_regions'
                ? selected.protein_selection.regions[0]
                : null;
            if (!first) throw new Error('individual sequence positions require exact source inspection');
            return setFrustraMpnnRegion(selected, 0, {
                ...first,
                sequence_end: first.sequence_start,
            });
        });
    };

    return (
        <fieldset className="space-y-3 rounded-xl border border-cyan-200 bg-cyan-50/40 p-3">
            <legend className="px-1 text-sm font-semibold text-slate-800">Model execution scope</legend>
            <label className="block text-sm text-slate-700">
                Analyze
                <select
                    data-frustrampnn-selection-mode
                    className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-2"
                    value={displayMode}
                    onChange={handleMode}
                >
                    {options.map((option) => (
                        <option key={option.mode} value={option.mode} disabled={!option.available}>
                            {MODE_LABELS[option.mode]}
                        </option>
                    ))}
                </select>
            </label>

            <p className="text-xs leading-5 text-slate-600">
                This scope compiles to FrustraMPNN chain and zero-based position inputs. It changes which mapped protein residues are scored. DNA, RNA, ligands, ions, and solvent stay outside the analysis.
            </p>

            {!inspection && (
                <p className="text-xs text-slate-500">
                    Exact source entity, sequence-region, and residue selectors are unavailable until source inspection is produced.
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
                                    onChange={(event) => apply(() => setFrustraMpnnEntitySelected(
                                        value,
                                        entity,
                                        event.target.checked,
                                    ))}
                                />
                                <span>
                                    {sourceEntityLabel(entity)}
                                    {entity.label_asym_id ? ` · label ${entity.label_asym_id}` : ''}
                                </span>
                            </label>
                        );
                    })}
                </div>
            )}

            {selection.mode === 'selected_regions' && inspection && (
                <div className="space-y-3" aria-label="Selected protein regions">
                    {selection.regions.map((region, index) => {
                        const bounds = getFrustraMpnnInspectionRegionBounds(inspection, region);
                        return (
                            <div
                                key={`${frustraMpnnRegionSelectorKey(region)}-${index}`}
                                data-frustrampnn-region-row
                                className="grid gap-2 rounded-lg border border-slate-200 bg-white p-3 sm:grid-cols-[minmax(8rem,1fr)_minmax(6rem,.7fr)_minmax(6rem,.7fr)_auto]"
                            >
                                <label className="text-xs font-medium text-slate-600">
                                    Chain
                                    <select
                                        data-frustrampnn-region-chain
                                        className="mt-1 block w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
                                        value={frustraMpnnEntitySelectorKey(region)}
                                        onChange={(event) => {
                                            const entity = inspection.protein_entities.find((candidate) => (
                                                frustraMpnnEntitySelectorKey(candidate) === event.target.value
                                            ));
                                            if (!entity) return;
                                            const selector = inspectedEntitySelector(entity);
                                            const nextBounds = getFrustraMpnnInspectionRegionBounds(inspection, selector);
                                            if (!nextBounds) return;
                                            apply(() => setFrustraMpnnRegion(value, index, {
                                                ...selector,
                                                sequence_start: nextBounds.start,
                                                sequence_end: nextBounds.end,
                                            }));
                                        }}
                                    >
                                        {inspection.protein_entities
                                            .filter((entity) => getFrustraMpnnInspectionRegionBounds(inspection, entity) !== null)
                                            .map((entity) => (
                                                <option
                                                    key={frustraMpnnEntitySelectorKey(entity)}
                                                    value={frustraMpnnEntitySelectorKey(entity)}
                                                >
                                                    {sourceEntityLabel(entity)}
                                                </option>
                                            ))}
                                    </select>
                                </label>
                                <label className="text-xs font-medium text-slate-600">
                                    Start
                                    <input
                                        data-frustrampnn-region-start
                                        type="number"
                                        min={bounds?.start ?? 1}
                                        max={region.sequence_end}
                                        value={region.sequence_start}
                                        className="mt-1 block w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
                                        onChange={(event) => apply(() => setFrustraMpnnRegion(value, index, {
                                            ...region,
                                            sequence_start: Number(event.target.value),
                                        }))}
                                    />
                                </label>
                                <label className="text-xs font-medium text-slate-600">
                                    End
                                    <input
                                        data-frustrampnn-region-end
                                        type="number"
                                        min={region.sequence_start}
                                        max={bounds?.end}
                                        value={region.sequence_end}
                                        className="mt-1 block w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
                                        onChange={(event) => apply(() => setFrustraMpnnRegion(value, index, {
                                            ...region,
                                            sequence_end: Number(event.target.value),
                                        }))}
                                    />
                                </label>
                                <button
                                    type="button"
                                    data-frustrampnn-remove-region
                                    disabled={selection.regions.length === 1}
                                    className="self-end rounded border border-slate-300 px-2 py-1.5 text-xs text-slate-600 disabled:opacity-40"
                                    onClick={() => apply(() => removeFrustraMpnnRegion(value, index))}
                                >
                                    Remove
                                </button>
                                <div className="text-xs text-slate-500 sm:col-span-4">
                                    Effective source sequence: {region.auth_asym_id
                                        ? `${region.auth_asym_id}:${region.sequence_start}–${region.sequence_end}`
                                        : `source instance ${region.entity_instance_id} · ${region.sequence_start}–${region.sequence_end}`}. Use the same start and end for one residue.
                                </div>
                            </div>
                        );
                    })}
                    <button
                        type="button"
                        data-frustrampnn-add-region
                        className="rounded-lg border border-cyan-300 bg-white px-3 py-1.5 text-xs font-semibold text-cyan-800"
                        onClick={() => apply(() => addFrustraMpnnRegion(value, inspection))}
                    >
                        Add another region
                    </button>
                </div>
            )}

            {selection.mode === 'selected_residues' && inspection && allowIndividualResidues && (
                <details open data-frustrampnn-advanced-residues>
                    <summary className="cursor-pointer text-xs font-semibold text-slate-700">Advanced individual residues</summary>
                    <div className="mt-2 max-h-64 space-y-2 overflow-auto" aria-label="Inspected mapped residues">
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
                                        onChange={(event) => apply(() => setFrustraMpnnResidueSelected(
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
                </details>
            )}

            {diagnostic && <p className="text-xs text-red-700" role="alert">{diagnostic}</p>}
        </fieldset>
    );
}
