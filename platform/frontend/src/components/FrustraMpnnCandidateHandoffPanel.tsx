import { useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { handoffFrustraMpnnCandidate, validateFrustraMpnnUploadedSettings, type FrustraMpnnCandidateHandoffRequest, type FrustraMpnnChildReceipt, type FrustraMpnnRequestedSettings } from '../lib/frustraMpnnApi.js';
import { FrustraMpnnSettingsPanel } from './frustrampnn/FrustraMpnnSettingsPanel.js';
import { CANONICAL_FRUSTRAMPNN_SETTINGS } from './frustrampnn/frustraMpnnSettingsState.js';

interface Props {
    parentJobId: string;
    parentInvocationId: string;
    parentLandscapeSha256: string;
    guidanceId?: string;
    onOpenJob?: (jobId: string) => void;
}

export default function FrustraMpnnCandidateHandoffPanel(props: Props) {
    return <CandidateHandoffForm key={JSON.stringify([props.parentJobId, props.parentInvocationId, props.parentLandscapeSha256, props.guidanceId])} {...props} />;
}

function CandidateHandoffForm({ parentJobId, parentInvocationId, parentLandscapeSha256, guidanceId, onOpenJob }: Props) {
    const pending = useRef<AbortController | null>(null);
    useEffect(() => () => pending.current?.abort(), []);
    const sourceReady = Boolean(parentJobId.trim() && parentInvocationId.trim() && /^[0-9a-f]{64}$/.test(parentLandscapeSha256));
    const [file, setFile] = useState<File | null>(null);
    const [candidateId, setCandidateId] = useState('');
    const [producerId, setProducerId] = useState('');
    const [proteinSequenceSha256, setProteinSequenceSha256] = useState('');
    const [frustrampnnSettings, setFrustrampnnSettings] = useState<FrustraMpnnRequestedSettings>(CANONICAL_FRUSTRAMPNN_SETTINGS);
    const mutation = useMutation<FrustraMpnnChildReceipt, Error, { file: File; request: FrustraMpnnCandidateHandoffRequest }>({
        mutationFn: async ({ file: candidateFile, request }) => {
            const controller = new AbortController();
            pending.current = controller;
            await validateFrustraMpnnUploadedSettings(request.frustrampnn_settings, candidateFile, controller.signal);
            if (controller.signal.aborted) throw new Error('Parent selection changed; reanalysis was not submitted.');
            return handoffFrustraMpnnCandidate(candidateFile, request, controller.signal);
        },
    });
    const submit = () => {
        if (!sourceReady || mutation.isPending || !file || !candidateId.trim() || !producerId.trim()) return;
        mutation.mutate({
            file,
            request: {
                candidate_id: candidateId.trim(),
                producer_id: producerId.trim(),
                parent_job_id: parentJobId,
                parent_invocation_id: parentInvocationId,
                parent_landscape_sha256: parentLandscapeSha256,
                guidance_id: guidanceId,
                nucleotide_edit_set: [],
                protein_sequence_sha256: proteinSequenceSha256.trim() || undefined,
                frustrampnn_settings: frustrampnnSettings,
            },
        });
    };
    return (
        <section aria-label="FrustraMPNN external candidate handoff" className="rounded-xl border border-amber-500/30 bg-amber-950/10 p-4">
            <h2 className="font-semibold">Analyze an updated structure</h2>
            <p className="mt-1 text-xs text-slate-400">Upload a PDB or mmCIF candidate to run a new analysis linked to this result.</p>
            <fieldset disabled={mutation.isPending} className="mt-3 grid gap-3 md:grid-cols-2">
                <label className="text-xs text-slate-400">Candidate name<input value={candidateId} onChange={(event) => { mutation.reset(); setCandidateId(event.target.value); }} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200" placeholder="variant-1" /></label>
                <label className="text-xs text-slate-400">Produced by<input value={producerId} onChange={(event) => { mutation.reset(); setProducerId(event.target.value); }} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200" placeholder="external-redesign" /></label>
                <label className="text-xs text-slate-400">Protein sequence SHA-256 (optional)<input value={proteinSequenceSha256} onChange={(event) => { mutation.reset(); setProteinSequenceSha256(event.target.value); }} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 font-mono text-slate-200" /></label>
                <label className="text-xs text-slate-400">Structure snapshot (.pdb/.cif)<input type="file" accept=".pdb,.cif,.mmcif" required onChange={(event) => { mutation.reset(); setFile(event.target.files?.[0] ?? null); }} className="mt-1 block w-full text-xs text-slate-300" /></label>
            </fieldset>
            <fieldset disabled={mutation.isPending}>
            <FrustraMpnnSettingsPanel
                value={frustrampnnSettings}
                onChange={(settings) => { mutation.reset(); setFrustrampnnSettings(settings); }}
                governedSource={file ? { kind: 'upload', file } : undefined}
            />
            </fieldset>
            <details className="mt-2 text-xs text-slate-500">
                <summary className="cursor-pointer">Source provenance</summary>
                <dl className="mt-2 space-y-1 break-all">
                    <div><dt className="inline">Parent job: </dt><dd className="inline font-mono">{parentJobId}</dd></div>
                    <div><dt className="inline">Invocation: </dt><dd className="inline font-mono">{parentInvocationId}</dd></div>
                    <div><dt className="inline">Landscape SHA-256: </dt><dd className="inline font-mono">{parentLandscapeSha256}</dd></div>
                    {guidanceId && <div><dt className="inline">Guidance: </dt><dd className="inline font-mono">{guidanceId}</dd></div>}
                </dl>
            </details>
            <button type="button" disabled={!sourceReady || mutation.isPending || !file || !candidateId.trim() || !producerId.trim()} onClick={submit} className="mt-3 rounded bg-amber-500 px-3 py-2 text-sm text-slate-950 disabled:opacity-40">{mutation.isPending ? 'Queueing reanalysis…' : 'Queue FrustraMPNN reanalysis'}</button>
            {mutation.isError && <div role="alert" className="mt-2 text-xs text-red-300">{mutation.error?.message}</div>}
            {mutation.data && <div className="mt-3 rounded border border-emerald-500/30 bg-emerald-500/10 p-2 text-xs text-emerald-100">
                <div>Analysis queued: <span className="font-mono">{mutation.data.child_job_id}</span>. Results will appear when analysis completes.</div>
                {onOpenJob && <button type="button" onClick={() => onOpenJob(mutation.data!.result_job_id)} className="mt-2 underline">Open analysis results</button>}
                {mutation.data.handoff && <dl className="mt-2 grid gap-1 text-[11px] sm:grid-cols-2">
                    <div><dt className="inline text-emerald-300">Parent candidate: </dt><dd className="inline font-mono">{mutation.data.handoff.parent_candidate_id}</dd></div>
                    <div><dt className="inline text-emerald-300">Producer: </dt><dd className="inline font-mono">{mutation.data.handoff.producer_id}</dd></div>
                    <div><dt className="inline text-emerald-300">Guidance: </dt><dd className="inline font-mono">{mutation.data.handoff.guidance_id ?? 'none'}</dd></div>
                    <div className="sm:col-span-2"><dt className="inline text-emerald-300">Parent landscape SHA-256: </dt><dd className="inline break-all font-mono">{mutation.data.handoff.parent_landscape_sha256}</dd></div>
                </dl>}
            </div>}
        </section>
    );
}
