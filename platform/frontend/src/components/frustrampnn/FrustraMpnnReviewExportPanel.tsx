import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
    createFrustraMpnnGovernedExport,
    createFrustraMpnnSavedReview,

    listFrustraMpnnSavedReviewPage,
    persistFrustraMpnnReviewCapture,
    updateFrustraMpnnSavedReview,
    type FrustraMpnnSavedReview,
    type FrustraMpnnSavedReviewWrite,
} from '../../lib/frustraMpnnApi';
import type { ResidueRef } from '../../structureViewer/contracts/structureIdentity';
import type { StructureSceneController } from '../../structureViewer/runtime/StructureSceneController';

interface Props {
    jobId: string;
    invocationId: string;
    selectedResidue: ResidueRef | null;
    filters: Record<string, string>;
    viewerState: Omit<FrustraMpnnSavedReviewWrite['viewer_state'], 'structure_camera' | 'structure_representations' | 'structure_layers'>;
    onRestore: (review: FrustraMpnnSavedReview) => void;
    sceneController: StructureSceneController | null;
}

export default function FrustraMpnnReviewExportPanel({
    jobId,
    invocationId,
    selectedResidue,
    filters,
    viewerState,
    onRestore,
    sceneController,
}: Props) {
    const queryClient = useQueryClient();
    const [editingReviewId, setEditingReviewId] = useState<string | null>(null);
    const [title, setTitle] = useState('');
    const [notes, setNotes] = useState('');
    const [tags, setTags] = useState('');
    const [receiptMessage, setReceiptMessage] = useState('');
    const [reviewOffset, setReviewOffset] = useState(0);
    const reviews = useQuery({
        queryKey: ['frustrampnn', 'reviews', jobId, reviewOffset],
        queryFn: ({ signal }) => listFrustraMpnnSavedReviewPage(jobId, reviewOffset, signal),
    });

    const payload = (): FrustraMpnnSavedReviewWrite => {
        if (!sceneController) throw new Error('structure viewer is unavailable');
        const captured = sceneController.capturePresentation();
        if (captured.status !== 'ok') throw new Error('structure presentation is unavailable');
        return {
        title: title.trim(), notes,
        result_references: [{ parent_job_id: jobId, invocation_id: invocationId }],
        selected_residues: selectedResidue?.authAsymId && selectedResidue.authSeqId != null ? [{
            auth_asym_id: selectedResidue.authAsymId,
            auth_seq_id: String(selectedResidue.authSeqId),
            insertion_code: selectedResidue.insertionCode ?? '',
        }] : [],
        filters,
        viewer_state: {
            ...viewerState,
            structure_camera: captured.value.camera ?? null,
            structure_representations: [...(captured.value.representations ?? [])],
            structure_layers: [...(captured.value.layers ?? [])],
        },
        tags: tags.split(',').map((tag) => tag.trim()).filter(Boolean),
        supersedes_review_id: editingReviewId,
    };
    };

    const save = useMutation({
        mutationFn: () => editingReviewId
            ? updateFrustraMpnnSavedReview(jobId, editingReviewId, payload())
            : createFrustraMpnnSavedReview(jobId, payload()),
        onSuccess: async () => {
            setEditingReviewId(null); setTitle(''); setNotes(''); setTags('');
            await queryClient.invalidateQueries({ queryKey: ['frustrampnn', 'reviews', jobId] });
        },
    });
    const governedExport = useMutation({
        mutationFn: ({ reviewId, format }: { reviewId: string; format: 'json' | 'csv' }) => createFrustraMpnnGovernedExport(jobId, {
            review_id: reviewId,
            invocation_id: invocationId,
            format,
            auth_asym_id: filters.chain || undefined,
            mutation_aa: filters.mutation || undefined,
            status: filters.slot_status || undefined,
        }),
        onSuccess: (receipt) => {
            setReceiptMessage(`${receipt.complete ? 'Complete' : 'Bounded'} ${receipt.format.toUpperCase()} export: ${receipt.row_count} of ${receipt.total_matching_rows} rows · ${receipt.content_sha256}`);
            window.location.assign(receipt.download_url);
        },
    });

    const capture = useMutation({
        mutationFn: async (reviewId: string) => {
            if (!sceneController) throw new Error('structure viewer is unavailable');
            const result = await sceneController.capturePng();
            if (result.status !== 'ok') throw new Error('structure capture is unavailable');
            return persistFrustraMpnnReviewCapture(jobId, reviewId, result.value);
        },
        onSuccess: (receipt) => setReceiptMessage(`Persisted PNG capture · ${receipt.size_bytes} bytes · ${receipt.content_sha256}`),
    });

    return (
        <section aria-label="FrustraMPNN review, capture, and export" className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div><h2 className="font-semibold">Review, capture, and export</h2><p className="mt-1 text-xs text-slate-500">Saved reviews restore the persisted result, filters, selection, and view state without model recomputation. Camera and representation captures use the structure workbench above.</p></div>
                <p className="text-xs text-slate-500">Exports and captures are created from one immutable saved review revision.</p>
            </div>
            {receiptMessage && <div role="status" className="mt-3 break-all text-xs text-emerald-300">{receiptMessage}</div>}
            {governedExport.isError && <div role="alert" className="mt-3 text-xs text-red-300">Governed export could not be created.</div>}
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
                <div className="space-y-2">
                    <input aria-label="Review title" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Review title" maxLength={160} className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
                    <textarea aria-label="Review notes" value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Interpretation notes" rows={4} className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
                    <input aria-label="Review tags" value={tags} onChange={(event) => setTags(event.target.value)} placeholder="tags, comma-separated" className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
                    <button type="button" disabled={!title.trim() || save.isPending} onClick={() => save.mutate()} className="rounded border border-cyan-500/50 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-100 disabled:opacity-40">{editingReviewId ? 'Update review' : 'Save review'}</button>
                    {save.isError && <div role="alert" className="text-xs text-red-300">Saved review could not be persisted.</div>}
                </div>
                <div className="max-h-64 space-y-2 overflow-auto">
                    {reviews.isError && <div role="alert" className="text-xs text-red-300">Saved reviews are unavailable.</div>}

                    {capture.isError && <div role="alert" className="text-xs text-red-300">Structure capture could not be persisted.</div>}
                    {reviews.data?.items.map((review) => <article key={review.review_id} className="rounded border border-slate-800 p-3 text-xs"><div className="font-medium text-slate-200">{review.title}</div><p className="mt-1 whitespace-pre-wrap text-slate-400">{review.notes || 'No notes.'}</p><div className="mt-1 break-all font-mono text-[10px] text-slate-600">{review.review_sha256}</div><div className="mt-2 flex flex-wrap gap-2"><button type="button" onClick={() => onRestore(review)} className="rounded border border-cyan-700 px-2 py-1 text-cyan-200">Restore</button><button type="button" disabled={!sceneController || capture.isPending} onClick={() => capture.mutate(review.review_id)} className="rounded border border-emerald-700 px-2 py-1 text-emerald-200 disabled:opacity-40">Persist PNG capture</button><button type="button" onClick={() => governedExport.mutate({ reviewId: review.review_id, format: 'json' })} className="rounded border border-slate-700 px-2 py-1">Export JSON</button><button type="button" onClick={() => governedExport.mutate({ reviewId: review.review_id, format: 'csv' })} className="rounded border border-slate-700 px-2 py-1">Export CSV</button><button type="button" onClick={() => { setEditingReviewId(review.review_id); setTitle(review.title); setNotes(review.notes); setTags(review.tags.join(', ')); onRestore(review); }} className="rounded border border-slate-700 px-2 py-1">Create revision</button></div></article>)}
                    {!reviews.isLoading && !reviews.isError && !reviews.data?.items.length && <p className="text-xs text-slate-500">No saved reviews.</p>}
                    {reviews.data && (reviewOffset > 0 || reviews.data.next_offset !== null) && <div className="flex justify-end gap-2"><button type="button" disabled={reviewOffset === 0 || reviews.isFetching} onClick={() => setReviewOffset(Math.max(0, reviewOffset - 100))} className="rounded border border-slate-700 px-2 py-1 disabled:opacity-30">Previous reviews</button><button type="button" disabled={reviews.data.next_offset === null || reviews.isFetching} onClick={() => setReviewOffset(reviews.data!.next_offset!)} className="rounded border border-slate-700 px-2 py-1 disabled:opacity-30">Next reviews</button></div>}
                </div>
            </div>
        </section>
    );
}
