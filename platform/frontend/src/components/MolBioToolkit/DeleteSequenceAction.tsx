import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { deleteNucleotideSequence } from '../../lib/api';

export function DeleteSequenceAction({ sequenceId, name, onDeleted, mobile=false }: { sequenceId: string; name: string; onDeleted?: () => void; mobile?: boolean }) {
    const client=useQueryClient(); const [confirm,setConfirm]=useState(false);
    const remove=useMutation({mutationFn:()=>deleteNucleotideSequence(sequenceId),onSuccess:async()=>{
        await Promise.all([client.invalidateQueries({queryKey:['molbio-recent-sequence-shelf']}),client.invalidateQueries({queryKey:['molbio-ngs-sequences']}),client.invalidateQueries({queryKey:['molbio-project-hub']}),client.invalidateQueries({queryKey:['project-manager']})]);
        setConfirm(false);onDeleted?.();
    }});
    const detail = (remove.error as unknown as {response?:{data?:{detail?:unknown}}})?.response?.data?.detail;
    return <div className="text-xs">
        <button type="button" disabled={remove.isPending} onClick={()=>setConfirm(true)} data-molbio-mobile-touch-target={mobile ? 'true' : undefined} className={`rounded border border-[var(--border-primary)] px-2 py-1.5 text-[var(--text-error)] ${mobile ? 'min-h-12 min-w-12' : ''}`} aria-label={`Delete DNA sequence ${name}`}>Delete sequence</button>
        {confirm && <div role="alertdialog" aria-label={`Delete ${name}`} className="mt-2 space-y-2 rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-[var(--text-primary)]">
            <p>Delete the editable sequence “{name}” from the library? Saved historical versions and existing evidence are retained. This removes the editable copy from every Project; linked primers or derived sequences may block deletion.</p>
            <div className="flex gap-2"><button type="button" disabled={remove.isPending} onClick={()=>setConfirm(false)} className="rounded border border-[var(--border-primary)] px-2 py-1">Cancel</button><button type="button" disabled={remove.isPending} onClick={()=>remove.mutate()} className="rounded border border-[var(--text-error)] px-2 py-1 text-[var(--text-error)]">{remove.isPending?'Deleting…':'Confirm delete sequence'}</button></div>
            {remove.isError && <p role="alert" className="text-[var(--text-error)]">{typeof detail === 'string' ? detail : remove.error instanceof Error ? remove.error.message : 'Sequence could not be deleted.'}</p>}
        </div>}
    </div>;
}
