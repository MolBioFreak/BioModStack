import { useEffect, useState } from 'react';
import {
    inspectFrustraMpnnOwnedSource,
    inspectFrustraMpnnUploadedSource,
    validateFrustraMpnnOwnedSettings,
    validateFrustraMpnnUploadedSettings,
    type FrustraMpnnOwnedSourceReference,
    type FrustraMpnnSettingsValidationPreview,
} from '../../lib/frustraMpnnApi.js';
import type {
    FrustraMpnnRequestedSettings,
    FrustraMpnnSourceInspection,
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
}

const shortHash = (value: string) => `${value.slice(0, 10)}…${value.slice(-8)}`;

export function FrustraMpnnSettingsPanel({
    value,
    onChange,
    inspection: suppliedInspection,
    governedSource,
    onValidationChange,
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
            className="mt-3 space-y-3 border-t border-slate-200 pt-3"
            aria-label="FrustraMPNN settings"
        >
            <FrustraMpnnProteinSelectionControl
                value={value}
                onChange={onChange}
                inspection={inspection}
            />
            <FrustraMpnnSourceStructurePolicyControl
                value={value}
                onChange={onChange}
                inspection={inspection}
            />
            <FrustraMpnnClassificationPolicyControl value={value} onChange={onChange} />
            {!governedSource && (
                <p className="text-[11px] text-amber-700" role="status">
                    Exact entity, residue, model, and altloc choices remain unavailable until governed server inspection metadata exists. All-protein defaults are preserved for launch-time source resolution.
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
        </section>
    );
}
