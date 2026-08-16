import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { handoffFrustraMpnnCandidate, validateFrustraMpnnUploadedSettings, type FrustraMpnnCandidateHandoffRequest, type FrustraMpnnChildReceipt, type FrustraMpnnRequestedSettings } from '../lib/frustraMpnnApi.js';
import { FrustraMpnnSettingsPanel } from './frustrampnn/FrustraMpnnSettingsPanel.js';
import { CANONICAL_FRUSTRAMPNN_SETTINGS } from './frustrampnn/frustraMpnnSettingsState.js';

interface Props {
    parentJobId: string;
    parentInvocationId: string;
    parentLandscapeSha256: string;
    guidanceId?: string;
}

export default function FrustraMpnnCandidateHandoffPanel({ parentJobId, parentInvocationId, parentLandscapeSha256, guidanceId }: Props) {
    const [file, setFile] = useState<File | null>(null);
    const [candidateId, setCandidateId] = useState('');
    const [producerId, setProducerId] = useState('');
    const [proteinSequenceSha256, setProteinSequenceSha256] = useState('');
    const [frustrampnnSettings, setFrustrampnnSettings] = useState<FrustraMpnnRequestedSettings>(CANONICAL_FRUSTRAMPNN_SETTINGS);
    const mutation = useMutation<FrustraMpnnChildReceipt, Error, { file: File; request: FrustraMpnnCandidateHandoffRequest }>({
        mutationFn: async ({ file: candidateFile, request }) => {
            await validateFrustraMpnnUploadedSettings(request.frustrampnn_settings, candidateFile);
            return handoffFrustraMpnnCandidate(candidateFile, request);
        },
    });
    const submit = () => {
        if (!file || !candidateId.trim() || !producerId.trim()) return;
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
            <h2 className="font-semibold">External candidate → structural reanalysis</h2>
            <p className="mt-1 text-xs text-slate-400">This accepts a producer-owned structure snapshot and queues a fresh scheduler-owned FrustraMPNN child. It does not generate a mutation library or control an instrument.</p>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
                <label className="text-xs text-slate-400">Candidate ID<input value={candidateId} onChange={(event) => setCandidateId(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200" placeholder="variant-1" /></label>
                <label className="text-xs text-slate-400">Producer ID<input value={producerId} onChange={(event) => setProducerId(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-slate-200" placeholder="external-redesign" /></label>
                <label className="text-xs text-slate-400">Protein sequence SHA-256 (optional)<input value={proteinSequenceSha256} onChange={(event) => setProteinSequenceSha256(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 font-mono text-slate-200" /></label>
                <label className="text-xs text-slate-400">Structure snapshot (.pdb/.cif)<input type="file" accept=".pdb,.cif,.mmcif" required onChange={(event) => { mutation.reset(); setFile(event.target.files?.[0] ?? null); }} className="mt-1 block w-full text-xs text-slate-300" /></label>
            </div>
            <p className="mt-3 text-xs text-slate-500">Nucleotide edits are optional and omitted from this structural handoff.</p>
            <FrustraMpnnSettingsPanel
                value={frustrampnnSettings}
                onChange={setFrustrampnnSettings}
                governedSource={file ? { kind: 'upload', file } : undefined}
            />
            <div className="mt-2 text-[11px] text-slate-500">Parent landscape: <span className="font-mono">{parentLandscapeSha256}</span></div>
            <button type="button" disabled={mutation.isPending || !file || !candidateId.trim() || !producerId.trim()} onClick={submit} className="mt-3 rounded bg-amber-500 px-3 py-2 text-sm text-slate-950 disabled:opacity-40">{mutation.isPending ? 'Queueing reanalysis…' : 'Queue FrustraMPNN reanalysis'}</button>
            {mutation.isError && <div role="alert" className="mt-2 text-xs text-red-300">{mutation.error?.message}</div>}
            {mutation.data && <div className="mt-3 rounded border border-emerald-500/30 bg-emerald-500/10 p-2 text-xs text-emerald-100">
                <div>Child queued: <span className="font-mono">{mutation.data.child_job_id}</span></div>
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
