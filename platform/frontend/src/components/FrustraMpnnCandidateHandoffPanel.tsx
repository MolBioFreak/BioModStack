import { useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { handoffFrustraMpnnCandidate, type FrustraMpnnCandidateHandoffRequest, type FrustraMpnnChildReceipt } from '../lib/frustraMpnnApi.js';

interface Props {
    parentJobId: string;
    parentInvocationId: string;
    parentLandscapeSha256: string;
    guidanceId?: string;
}

export default function FrustraMpnnCandidateHandoffPanel({ parentJobId, parentInvocationId, parentLandscapeSha256, guidanceId }: Props) {
    const inputRef = useRef<HTMLInputElement>(null);
    const [candidateId, setCandidateId] = useState('');
    const [producerId, setProducerId] = useState('');
    const [proteinSequenceSha256, setProteinSequenceSha256] = useState('');
    const [editSet, setEditSet] = useState('[]');
    const mutation = useMutation<FrustraMpnnChildReceipt, Error, { file: File; request: FrustraMpnnCandidateHandoffRequest }>({
        mutationFn: ({ file, request }) => handoffFrustraMpnnCandidate(file, request),
    });
    const submit = () => {
        const file = inputRef.current?.files?.[0];
        if (!file || !candidateId.trim() || !producerId.trim()) return;
        let nucleotideEditSet: Array<Record<string, unknown>> = [];
        try {
            const parsed: unknown = JSON.parse(editSet);
            if (!Array.isArray(parsed) || parsed.some((item) => typeof item !== 'object' || item == null || Array.isArray(item))) throw new Error('edit set must be a JSON list of objects');
            nucleotideEditSet = parsed as Array<Record<string, unknown>>;
        } catch (error) {
            mutation.reset();
            throw error;
        }
        mutation.mutate({
            file,
            request: {
                candidate_id: candidateId.trim(),
                producer_id: producerId.trim(),
                parent_job_id: parentJobId,
                parent_invocation_id: parentInvocationId,
                parent_landscape_sha256: parentLandscapeSha256,
                guidance_id: guidanceId,
                nucleotide_edit_set: nucleotideEditSet,
                protein_sequence_sha256: proteinSequenceSha256.trim() || undefined,
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
                <label className="text-xs text-slate-400">Structure snapshot (.pdb/.cif)<input ref={inputRef} type="file" accept=".pdb,.cif,.mmcif" className="mt-1 block w-full text-xs text-slate-300" /></label>
            </div>
            <label className="mt-3 block text-xs text-slate-400">Nucleotide edit set (JSON; empty list allowed)<textarea value={editSet} onChange={(event) => setEditSet(event.target.value)} className="mt-1 h-20 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 font-mono text-xs text-slate-200" /></label>
            <div className="mt-2 text-[11px] text-slate-500">Parent landscape: <span className="font-mono">{parentLandscapeSha256}</span></div>
            <button type="button" disabled={mutation.isPending || !candidateId.trim() || !producerId.trim()} onClick={submit} className="mt-3 rounded bg-amber-500 px-3 py-2 text-sm text-slate-950 disabled:opacity-40">{mutation.isPending ? 'Queueing reanalysis…' : 'Queue FrustraMPNN reanalysis'}</button>
            {mutation.isError && <div role="alert" className="mt-2 text-xs text-red-300">{mutation.error.message}</div>}
            {mutation.data && <div className="mt-3 rounded border border-emerald-500/30 bg-emerald-500/10 p-2 text-xs text-emerald-100">Child queued: <span className="font-mono">{mutation.data.child_job_id}</span></div>}
        </section>
    );
}
