import { useEffect, useState } from 'react';
import {
    inspectFrustraMpnnOwnedSource,
    inspectFrustraMpnnUploadedSource,
    validateFrustraMpnnOwnedSettings,
    validateFrustraMpnnUploadedSettings,
    type FrustraMpnnOwnedSourceReference,
    type FrustraMpnnSettingsValidationPreview,
} from '../../lib/frustraMpnnApi.js';
import {
    updateFrustraMpnnSourceStructure,
    type FrustraMpnnRequestedSettings,
    type FrustraMpnnSourceInspection,
} from './frustraMpnnSettingsState.js';
import { FrustraMpnnClassificationPolicyControl } from './FrustraMpnnClassificationPolicyControl.js';
import { FrustraMpnnProteinSelectionControl } from './FrustraMpnnProteinSelectionControl.js';
import { FrustraMpnnSourceStructurePolicyControl } from './FrustraMpnnSourceStructurePolicyControl.js';
import { FrustraMpnnRequestedEffectiveSummary } from './FrustraMpnnRequestedEffectiveSummary.js';

export type FrustraMpnnGovernedSettingsSource =
    | { kind: 'owned'; reference: FrustraMpnnOwnedSourceReference }
    | { kind: 'upload'; file: File };

interface FrustraMpnnSettingsPanelProps {
    value: FrustraMpnnRequestedSettings;
    onChange: (value: FrustraMpnnRequestedSettings) => void;
    inspection?: FrustraMpnnSourceInspection | null;
    governedSource?: FrustraMpnnGovernedSettingsSource | null;
    onValidationChange?: (preview: FrustraMpnnSettingsValidationPreview | null) => void;
    sourceStructurePolicy?: 'operator' | 'derived';
    allowIndividualResidues?: boolean;
}

const shortHash = (value: string) => `${value.slice(0, 10)}…${value.slice(-8)}`;

const scopeSummary = (settings: FrustraMpnnRequestedSettings): string => {
    const selection = settings.protein_selection;
    if (selection.mode === 'all_protein_entities') return 'All mapped protein residues';
    if (selection.mode === 'selected_entities') {
        return selection.entities.map((entity) => (
            entity.auth_asym_id ?? `Source instance ${entity.entity_instance_id}`
        )).join(', ');
    }
    if (selection.mode === 'selected_regions') {
        if (selection.regions.every((region) => region.sequence_start === region.sequence_end)) {
            return `${selection.regions.length} individual sequence position${selection.regions.length === 1 ? '' : 's'}`;
        }
        return selection.regions.map((region) => (
            region.auth_asym_id
                ? `${region.auth_asym_id}:${region.sequence_start}–${region.sequence_end}`
                : `Source instance ${region.entity_instance_id} · ${region.sequence_start}–${region.sequence_end}`
        )).join(', ');
    }
    return `${selection.residues.length} individual residue${selection.residues.length === 1 ? '' : 's'}`;
};

const classificationSummary = (settings: FrustraMpnnRequestedSettings): string => (
    settings.classification_policy.mode === 'canonical'
        ? 'Canonical'
        : `Custom (${settings.classification_policy.high_max} / ${settings.classification_policy.minimal_min})`
);

export function FrustraMpnnSettingsPanel({
    value,
    onChange,
    inspection: suppliedInspection,
    governedSource,
    onValidationChange,
    sourceStructurePolicy = 'operator',
    allowIndividualResidues = true,
}: FrustraMpnnSettingsPanelProps) {
    const [liveInspection, setLiveInspection] = useState<FrustraMpnnSourceInspection | null>(null);
    const [preview, setPreview] = useState<FrustraMpnnSettingsValidationPreview | null>(null);
    const [diagnostic, setDiagnostic] = useState<string | null>(null);
    const [validating, setValidating] = useState(false);
    const inspection = suppliedInspection ?? liveInspection;
    const ownedJobId = governedSource?.kind === 'owned' ? governedSource.reference.job_id : null;
    const ownedInvocationId = governedSource?.kind === 'owned' ? governedSource.reference.invocation_id : null;
    const uploadFile = governedSource?.kind === 'upload' ? governedSource.file : null;

    useEffect(() => {
        if (
            sourceStructurePolicy === 'derived'
            && (value.source_structure.selected_model_number !== 1 || value.source_structure.preferred_altloc !== '')
        ) {
            onChange(updateFrustraMpnnSourceStructure(value, {
                selected_model_number: 1,
                preferred_altloc: '',
            }));
        }
    }, [onChange, sourceStructurePolicy, value]);

    useEffect(() => {
        if (!ownedJobId && !uploadFile) {
            setLiveInspection(null);
            setPreview(null);
            setDiagnostic(null);
            onValidationChange?.(null);
            return;
        }
        const controller = new AbortController();
        const timer = window.setTimeout(() => {
            setValidating(true);
            setDiagnostic(null);
            setPreview(null);
            onValidationChange?.(null);
            const inspect = uploadFile
                ? inspectFrustraMpnnUploadedSource(uploadFile, value.source_structure, controller.signal)
                : inspectFrustraMpnnOwnedSource(
                    { job_id: ownedJobId!, invocation_id: ownedInvocationId! },
                    value.source_structure,
                    controller.signal,
                );
            inspect.then(async (sourceInspection) => {
                if (controller.signal.aborted) return;
                setLiveInspection(sourceInspection);
                const validation = uploadFile
                    ? await validateFrustraMpnnUploadedSettings(value, uploadFile, controller.signal)
                    : await validateFrustraMpnnOwnedSettings(
                        value,
                        { job_id: ownedJobId!, invocation_id: ownedInvocationId! },
                        controller.signal,
                    );
                if (controller.signal.aborted) return;
                setPreview(validation);
                onValidationChange?.(validation);
            }).catch((error: unknown) => {
                if (controller.signal.aborted) return;
                const message = error instanceof Error && error.message ? error.message : 'Settings validation failed.';
                setPreview(null);
                setDiagnostic(message);
                onValidationChange?.(null);
            }).finally(() => {
                if (!controller.signal.aborted) setValidating(false);
            });
        }, 200);
        return () => {
            window.clearTimeout(timer);
            controller.abort();
        };
    }, [ownedInvocationId, ownedJobId, uploadFile, value, onValidationChange]);

    return (
        <section
            data-frustrampnn-settings-panel
            className="mt-3 rounded-xl border border-slate-200 bg-white"
            aria-label="FrustraMPNN analysis"
        >
            <details data-frustrampnn-settings-details>
                <summary className="cursor-pointer list-none px-4 py-3 marker:hidden">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                            <div className="text-sm font-semibold text-slate-900">FrustraMPNN analysis</div>
                            <div className="mt-1 text-xs text-slate-500">Model scope and post-score classification</div>
                        </div>
                        <div className="flex flex-wrap gap-2 text-xs">
                            <span className="rounded-full bg-cyan-50 px-2.5 py-1 font-medium text-cyan-800">
                                Scope: {scopeSummary(value)}
                            </span>
                            <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-700">
                                Classification: {classificationSummary(value)}
                            </span>
                            <span className="self-center font-medium text-cyan-700">Edit</span>
                        </div>
                    </div>
                </summary>

                <div className="space-y-3 border-t border-slate-200 p-4">
                    <FrustraMpnnProteinSelectionControl
                        value={value}
                        onChange={onChange}
                        inspection={inspection}
                        allowIndividualResidues={allowIndividualResidues}
                    />

                    <section className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/60 p-3" aria-label="Result classification">
                        <div>
                            <h4 className="text-sm font-semibold text-slate-800">Result classification</h4>
                            <p className="mt-1 text-xs leading-5 text-slate-600">
                                Classification relabels model scores after inference. It does not change FrustraMPNN scoring.
                            </p>
                        </div>
                        <FrustraMpnnClassificationPolicyControl value={value} onChange={onChange} />
                    </section>

                    {sourceStructurePolicy === 'operator' ? (
                        <details data-frustrampnn-input-normalization className="rounded-xl border border-slate-200 bg-slate-50/60 p-3">
                            <summary className="cursor-pointer text-xs font-semibold text-slate-700">Advanced input normalization</summary>
                            <div className="mt-3">
                                <FrustraMpnnSourceStructurePolicyControl
                                    value={value}
                                    onChange={onChange}
                                    inspection={inspection}
                                />
                            </div>
                        </details>
                    ) : (
                        <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-3 text-xs leading-5 text-slate-600" data-frustrampnn-derived-source-policy>
                            <span className="font-semibold text-slate-700">Input normalization: </span>
                            Derived from each canonical generated conformer. Effective model and alternate-location handling are recorded as read-only provenance.
                        </div>
                    )}

                    {!governedSource && !suppliedInspection && (
                        <p className="text-[11px] text-amber-700" role="status">
                            Exact source entity, region, residue, model, and altloc choices remain unavailable until governed server inspection metadata exists. All-protein defaults are preserved for launch-time source resolution.
                        </p>
                    )}
                    {validating && <p className="text-[11px] text-cyan-700" role="status">Validating requested settings against the governed source…</p>}
                    {diagnostic && <p className="text-[11px] text-red-700" role="alert">Validation diagnostic: {diagnostic}</p>}
                    {preview && (
                        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-[11px] text-slate-600" data-frustrampnn-validation-preview>
                            <div className="font-semibold text-emerald-700">Requested → effective settings validated</div>
                            <div className="mt-2"><FrustraMpnnRequestedEffectiveSummary effective={preview.effective_settings} /></div>
                            <dl className="mt-2 grid gap-1 sm:grid-cols-2">
                                <div><dt className="inline font-medium">Settings authority: </dt><dd className="inline font-mono">{shortHash(preview.hashes.settings_sha256)}</dd></div>
                                <div><dt className="inline font-medium">Effective authority: </dt><dd className="inline font-mono">{shortHash(preview.hashes.effective_settings_sha256)}</dd></div>
                            </dl>
                        </div>
                    )}
                </div>
            </details>
        </section>
    );
}
