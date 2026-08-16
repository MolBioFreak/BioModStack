import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import {
    analyzeFrustraMpnnUpload,
    validateFrustraMpnnUploadedSettings,
    type FrustraMpnnChildReceipt,
    type FrustraMpnnRequestedSettings,
} from '../lib/frustraMpnnApi.js';
import { FrustraMpnnSettingsPanel } from './frustrampnn/FrustraMpnnSettingsPanel.js';
import { CANONICAL_FRUSTRAMPNN_SETTINGS } from './frustrampnn/frustraMpnnSettingsState.js';

interface Props {
    onOpenJob: (jobId: string) => void;
}

const errorMessage = (value: unknown): string => (
    value instanceof Error && value.message ? value.message : 'Uploaded FrustraMPNN analysis could not be queued.'
);

export default function FrustraMpnnUploadAnalysisPanel({ onOpenJob }: Props) {
    const [file, setFile] = useState<File | null>(null);
    const [settings, setSettings] = useState<FrustraMpnnRequestedSettings>(CANONICAL_FRUSTRAMPNN_SETTINGS);
    const mutation = useMutation<FrustraMpnnChildReceipt, Error>({
        mutationFn: async () => {
            if (!file) throw new Error('Select a governed PDB or mmCIF structure first.');
            await validateFrustraMpnnUploadedSettings(settings, file);
            return analyzeFrustraMpnnUpload(file, settings);
        },
    });

    return (
        <section aria-label="Uploaded FrustraMPNN structure analysis" className="mb-4 rounded-xl border border-violet-500/25 bg-violet-500/5 p-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                    <h2 className="text-sm font-semibold text-violet-100">Analyze an uploaded structure</h2>
                    <p className="mt-1 text-xs text-slate-400">Inspect a local PDB or mmCIF, choose exact governed settings, then queue scheduler-owned analysis.</p>
                    <p className="mt-2 text-[11px] font-medium text-violet-200">Required analysis · a selected structure and complete settings are mandatory.</p>
                </div>
                <label className="text-xs text-slate-300">Structure file (.pdb/.cif/.mmcif)
                    <input
                        type="file"
                        accept=".pdb,.cif,.mmcif"
                        required
                        onChange={(event) => {
                            mutation.reset();
                            setFile(event.target.files?.[0] ?? null);
                        }}
                        className="mt-1 block w-full text-xs text-slate-300"
                    />
                </label>
            </div>
            <FrustraMpnnSettingsPanel
                value={settings}
                onChange={setSettings}
                governedSource={file ? { kind: 'upload', file } : undefined}
            />
            <button
                type="button"
                disabled={!file || mutation.isPending}
                onClick={() => mutation.mutate()}
                className="mt-3 rounded-lg border border-violet-400/50 bg-violet-500/15 px-4 py-2 text-xs font-semibold text-violet-100 disabled:opacity-40"
            >
                {mutation.isPending ? 'Queueing uploaded analysis…' : 'Analyze uploaded structure'}
            </button>
            {mutation.isError && <div role="alert" className="mt-2 text-xs text-red-300">{errorMessage(mutation.error)}</div>}
            {mutation.data && <div role="status" className="mt-3 rounded border border-emerald-500/30 bg-emerald-500/10 p-2 text-xs text-emerald-100">Child queued: <span className="font-mono">{mutation.data.child_job_id}</span><button type="button" onClick={() => onOpenJob(mutation.data!.result_job_id)} className="ml-2 underline">Open persisted results</button></div>}
        </section>
    );
}
