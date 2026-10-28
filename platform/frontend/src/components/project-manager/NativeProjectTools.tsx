import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { ensureNativeProjectWorkspace, nativeProjectToolHref, type NativeWorkspace } from '../../lib/nativeProjectWorkspace';
import { projectManagerErrorMessage } from '../../lib/projectManager';
import { NgsDataRecordDialog, NgsDataRecords } from './NgsDataRecords';

export function NativeProjectTools({ projectId, projectName, context, disabled = false }: { projectId: string; projectName: string; context: NativeWorkspace | null; disabled?: boolean }) {
    const navigate = useNavigate();
    const client = useQueryClient();
    const [recordOpen, setRecordOpen] = useState(false);
    const open = useMutation({
        mutationFn: async (tool: 'molbio' | 'ngs' | 'add-sequence') => nativeProjectToolHref(projectId, await ensureNativeProjectWorkspace(projectId, context), tool),
        onSuccess: (href) => { void client.invalidateQueries({ queryKey: ['project-manager'] }); navigate(href); },
    });
    return <>
        <section aria-label="Project tools" className="m-4 rounded-xl border border-border-primary bg-surface-secondary p-5">
            <h2 className="font-semibold text-content">Sequences and NGS</h2>
            <div className="mt-3 flex flex-wrap gap-2">{([['molbio', 'Open Molecular Tools'], ['ngs', 'Open NGS'], ['add-sequence', 'Add DNA sequence']] as const).map(([tool, label]) => <button key={tool} disabled={disabled || open.isPending} onClick={() => open.mutate(tool)} className="rounded-lg border border-accent px-3 py-2 text-sm font-semibold text-accent disabled:opacity-50">{label}</button>)}<button disabled={disabled || open.isPending} onClick={() => setRecordOpen(true)} className="rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">Add NGS data record</button></div>
            {open.isPending && <p role="status" className="mt-3 text-sm text-content-secondary">Preparing this Project’s native workspace…</p>}
            {open.isError && <p role="alert" className="mt-3 text-sm text-error">{projectManagerErrorMessage(open.error)}</p>}
        </section>
        <NgsDataRecords projectId={projectId} onAdd={() => setRecordOpen(true)} />
        {recordOpen && <NgsDataRecordDialog projectId={projectId} projectName={projectName} onClose={() => setRecordOpen(false)} />}
    </>;
}
