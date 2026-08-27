import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQueries } from '@tanstack/react-query';
import { downloadDesignPdb } from '../lib/api.js';
import {
    analyzeFrustraMpnnDesigns,
    fetchFrustraMpnnReceipt,
    inspectFrustraMpnnUploadedSource,
    validateFrustraMpnnUploadedSettings,
    type FrustraMpnnRequestedSettings,
    type FrustraMpnnStructureDatasetFanout,
} from '../lib/frustraMpnnApi.js';
import { useModelIntegrationConfig } from './ModelIntegrationControl';
import { FrustraMpnnSettingsPanel } from './frustrampnn/FrustraMpnnSettingsPanel.js';
import { CANONICAL_FRUSTRAMPNN_SETTINGS } from './frustrampnn/frustraMpnnSettingsState.js';

interface OwnedDesignSelection {
    id: string;
    name: string;
    pdb_path: string | null;
}

interface FrustraMpnnAnalysisControlsProps {
    parentJobId: string;
    selectedDesigns: readonly OwnedDesignSelection[];
    onOpenJob: (jobId: string) => void;
}

const errorMessage = (value: unknown): string => {
    if (value instanceof Error && value.message) return value.message;
    return 'FrustraMPNN child submission failed.';
};

const sha256Hex = async (buffer: ArrayBuffer): Promise<string> => {
    const digest = await crypto.subtle.digest('SHA-256', buffer);
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
};

const resolveOwnedSelection = async (design: OwnedDesignSelection) => {
    if (!design.pdb_path) throw new Error(`${design.name} has no BMS-owned structure artifact.`);
    const response = await fetch(downloadDesignPdb(design.id), { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`${design.name} structure authority could not be read (${response.status}).`);
    const bytes = await response.arrayBuffer();
    if (bytes.byteLength === 0) throw new Error(`${design.name} structure authority is empty.`);
    return {
        selection: { design_id: design.id, source_sha256: await sha256Hex(bytes) },
        file: new File([bytes], `${design.id}.pdb`, { type: 'chemical/x-pdb' }),
    };
};

const stateClass = (status: string): string => {
    if (status === 'completed') return 'border-emerald-500/40 bg-emerald-500/10 text-emerald-100';
    if (status === 'failed' || status === 'cancelled') return 'border-red-500/40 bg-red-500/10 text-red-100';
    return 'border-cyan-500/40 bg-cyan-500/10 text-cyan-100';
};

export default function FrustraMpnnAnalysisControls({
    parentJobId,
    selectedDesigns,
    onOpenJob,
}: FrustraMpnnAnalysisControlsProps) {
    const integrationQuery = useModelIntegrationConfig('frustrampnn');
    const integration = integrationQuery.data;
    const [fanout, setFanout] = useState<FrustraMpnnStructureDatasetFanout | null>(null);
    const [frustrampnnSettings, setFrustrampnnSettings] = useState<FrustraMpnnRequestedSettings>(CANONICAL_FRUSTRAMPNN_SETTINGS);
    const [governedPreview, setGovernedPreview] = useState<Awaited<ReturnType<typeof resolveOwnedSelection>> | null>(null);
    const [previewError, setPreviewError] = useState<string | null>(null);
    const selectionIsOwned = selectedDesigns.length > 0 && selectedDesigns.every((design) => Boolean(design.pdb_path));
    const orderedNames = useMemo(() => selectedDesigns.map((design) => design.name).join(' • '), [selectedDesigns]);
    const firstDesign = selectedDesigns[0];
    const firstDesignId = firstDesign?.id ?? null;
    const firstDesignName = firstDesign?.name ?? null;
    const firstDesignPath = firstDesign?.pdb_path ?? null;

    useEffect(() => {
        let current = true;
        setGovernedPreview(null);
        setPreviewError(null);
        if (!firstDesignId || !firstDesignName || !firstDesignPath) return () => { current = false; };
        resolveOwnedSelection({ id: firstDesignId, name: firstDesignName, pdb_path: firstDesignPath })
            .then((resolved) => { if (current) setGovernedPreview(resolved); })
            .catch((error: unknown) => {
                if (current) setPreviewError(errorMessage(error));
            });
        return () => { current = false; };
    }, [firstDesignId, firstDesignName, firstDesignPath]);

    const submission = useMutation({
        mutationFn: async () => {
            const resolvedSelections = [];
            for (const design of selectedDesigns) {
                resolvedSelections.push(
                    design.id === firstDesignId && governedPreview
                        ? governedPreview
                        : await resolveOwnedSelection(design),
                );
            }
            for (const resolved of resolvedSelections) {
                await inspectFrustraMpnnUploadedSource(resolved.file, frustrampnnSettings.source_structure);
                await validateFrustraMpnnUploadedSettings(frustrampnnSettings, resolved.file);
            }
            return analyzeFrustraMpnnDesigns(parentJobId, {
                selections: resolvedSelections.map((resolved) => resolved.selection),
                frustrampnn_settings: frustrampnnSettings,
            });
        },
        onSuccess: setFanout,
    });
    const activeReceipts = useQueries({
        queries: (fanout?.child_jobs ?? []).map((receipt) => ({
            queryKey: ['frustrampnn-child-receipt', receipt.child_job_id],
            queryFn: () => fetchFrustraMpnnReceipt(receipt.child_job_id),
            initialData: receipt,
            refetchInterval: (query: { state: { data?: { status?: string } } }) => {
                const status = query.state.data?.status;
                return status === 'queued' || status === 'running' ? 3000 : false;
            },
        })),
    });
    const currentReceipts = activeReceipts.map((query, index) => query.data ?? fanout!.child_jobs[index]!);

    if (selectedDesigns.length === 0) return null;
    return (
        <section className="mb-4 rounded-xl border border-cyan-500/25 bg-cyan-500/5 p-4" aria-labelledby="frustrampnn-analysis-heading">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0">
                    <h2 id="frustrampnn-analysis-heading" className="text-sm font-semibold text-cyan-100">
                        {integration?.operator_label || 'Frustration analysis'}
                    </h2>
                    {integration && (
                        <p className="mt-1 text-[11px] text-cyan-200/80">
                            {integration.model_name}{integration.checkpoint_label ? ` · ${integration.checkpoint_label}` : ''}
                        </p>
                    )}
                    <p className="mt-1 text-xs text-slate-400">
                        Submit the selected BMS-owned Design structures to the scheduler. Every scheduler child remains visible and is polled independently.
                    </p>
                    <p className="mt-2 truncate text-[11px] text-slate-500" title={orderedNames}>
                        Locked order: {orderedNames}
                    </p>
                    {!selectionIsOwned && (
                        <p role="status" className="mt-2 text-xs text-amber-200">
                            Unavailable: every selected row must have a governed BMS Design structure artifact.
                        </p>
                    )}
                </div>
                <button
                    type="button"
                    disabled={!selectionIsOwned || !governedPreview || submission.isPending}
                    onClick={() => submission.mutate()}
                    className="rounded-lg border border-cyan-500/50 bg-cyan-500/15 px-4 py-2 text-xs font-semibold text-cyan-100 hover:bg-cyan-500/25 disabled:cursor-not-allowed disabled:opacity-50"
                >
                    {submission.isPending ? 'Creating queued child…' : `Analyze ${selectedDesigns.length} selected`}
                </button>
            </div>
            <FrustraMpnnSettingsPanel
                value={frustrampnnSettings}
                onChange={setFrustrampnnSettings}
                governedSource={governedPreview ? { kind: 'upload', file: governedPreview.file } : undefined}
            />
            {previewError && (
                <div role="alert" className="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-xs text-red-100">
                    Governed source inspection failed: {previewError}
                </div>
            )}
            {submission.isError && (
                <div role="alert" className="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-xs text-red-100">
                    {errorMessage(submission.error)}
                </div>
            )}
            {fanout && (
                <div role="status" aria-live="polite" className="mt-3 space-y-2">
                    <div className="text-xs text-cyan-100">
                        {fanout.child_jobs.length} scheduler children for {fanout.selected_structure_count} structures
                    </div>
                    {currentReceipts.map((current, index) => (
                        <div key={current.child_job_id} className={`rounded-lg border p-3 text-xs ${stateClass(current.status)}`}>
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <span className="font-semibold">
                                    Child {index + 1}/{fanout.child_jobs.length} · {fanout.child_jobs[index]!.structure_count} structure{fanout.child_jobs[index]!.structure_count === 1 ? '' : 's'} · {current.status}
                                </span>
                                <button type="button" onClick={() => onOpenJob(current.result_job_id)} className="rounded border border-current/50 px-3 py-1.5 hover:bg-white/10">
                                    Open persisted results
                                </button>
                            </div>
                            <div className="mt-1 font-mono text-[10px] opacity-80">{current.child_job_id}</div>
                        </div>
                    ))}
                </div>
            )}
        </section>
    );
}
