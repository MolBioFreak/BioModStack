import { useRef, useState } from 'react';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { uploadFile } from '../../lib/api';
import { projectManagerErrorMessage } from '../../lib/projectManager';
import { createNgsDataRecord, listNgsDataRecords, NGS_DATA_FORMATS, NGS_RECORD_STAGES, NGS_USE_CASES, type NgsDataFile, type NgsDataRecordDraft } from '../../lib/ngsDataRecords';

const fieldClass = 'mt-1 block w-full rounded-lg border border-border-primary bg-surface px-3 py-2 text-sm text-content';
const blank = (): NgsDataRecordDraft => ({ schema: 'bns.ngs-data-record.v1', record_key: crypto.randomUUID(), title: '', sample_name: '', data_format: 'fastq', use_case: 'sequence_identity', workflow_stage: 'acquired', data_location: '', notes: '', files: [] });

export function NgsDataRecordDialog({ projectId, projectName, onClose }: { projectId: string; projectName: string; onClose: () => void }) {
    const client = useQueryClient();
    const [draft, setDraft] = useState(blank);
    const [files, setFiles] = useState<File[]>([]);
    const uploaded = useRef(new Map<File, NgsDataFile>());
    const save = useMutation({
        mutationFn: async () => {
            if (files.length > 50) throw new Error('Select at most 50 files for one data record.');
            const references: NgsDataFile[] = [];
            for (const file of files) {
                let reference = uploaded.current.get(file);
                if (!reference) {
                    const stored = new File([file], `${crypto.randomUUID()}-${file.name.replace(/[^a-zA-Z0-9._-]/g, '_').slice(-160)}`, { type: file.type });
                    const { data } = await uploadFile(`inputs/bns-projects/${projectId}`, stored);
                    reference = { name: file.name, path: data.path, size: data.size };
                    uploaded.current.set(file, reference);
                }
                references.push(reference);
            }
            return createNgsDataRecord(projectId, { ...draft, title: draft.title.trim(), files: references });
        },
        onSuccess: async () => { await client.invalidateQueries({ queryKey: ['ngs-data-records', projectId] }); onClose(); },
    });
    const set = <K extends keyof NgsDataRecordDraft>(key: K, value: NgsDataRecordDraft[K]) => setDraft((current) => ({ ...current, [key]: value }));
    return <div className="fixed inset-0 z-[100] grid place-items-center bg-black/70 p-3">
        <section role="dialog" aria-modal="true" aria-labelledby="ngs-record-title" className="max-h-[94vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-border-primary bg-surface-secondary p-5 shadow-xl">
            <h2 id="ngs-record-title" className="text-lg font-semibold text-content">Add NGS data record</h2>
            <p className="mt-1 text-sm text-content-secondary">{projectName} · Record data at any stage of the workflow.</p>
            <form onSubmit={(event) => { event.preventDefault(); if (!save.isPending) save.mutate(); }}>
                <fieldset disabled={save.isPending} className="mt-4 grid gap-4 sm:grid-cols-2">
                    <label className="text-xs text-content-secondary sm:col-span-2">Record name<input aria-label="NGS record name" required maxLength={255} value={draft.title} onChange={(e) => set('title', e.target.value)} className={fieldClass} /></label>
                    <label className="text-xs text-content-secondary">Plasmid / sample name<input aria-label="Plasmid or sample name" maxLength={255} value={draft.sample_name} onChange={(e) => set('sample_name', e.target.value)} className={fieldClass} /></label>
                    <label className="text-xs text-content-secondary">Data format<select aria-label="NGS data format" value={draft.data_format} onChange={(e) => set('data_format', e.target.value as NgsDataRecordDraft['data_format'])} className={fieldClass}>{Object.entries(NGS_DATA_FORMATS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                    <label className="text-xs text-content-secondary">Use case<select aria-label="NGS use case" value={draft.use_case} onChange={(e) => set('use_case', e.target.value as NgsDataRecordDraft['use_case'])} className={fieldClass}>{Object.entries(NGS_USE_CASES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                    <label className="text-xs text-content-secondary">Recorded workflow stage<select aria-label="NGS workflow stage" value={draft.workflow_stage} onChange={(e) => set('workflow_stage', e.target.value as NgsDataRecordDraft['workflow_stage'])} className={fieldClass}>{Object.entries(NGS_RECORD_STAGES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                    <label className="text-xs text-content-secondary sm:col-span-2">Data location (optional reference)<input aria-label="NGS data location" maxLength={2048} placeholder="Existing dataset path, run folder, or accession" value={draft.data_location} onChange={(e) => set('data_location', e.target.value)} className={fieldClass} /></label>
                    <label className="text-xs text-content-secondary sm:col-span-2">Upload data files (optional)<input aria-label="Upload NGS data files" type="file" multiple onChange={(e) => setFiles(Array.from(e.target.files ?? []))} className={fieldClass} /><span className="mt-1 block">Files are stored in this Project’s inputs. For large instrument datasets, record the existing location.</span></label>
                    <label className="text-xs text-content-secondary sm:col-span-2">Notes<textarea aria-label="NGS data notes" maxLength={8192} value={draft.notes} onChange={(e) => set('notes', e.target.value)} className={fieldClass} /></label>
                </fieldset>
                <p className="mt-3 text-xs text-content-muted">This saves an intake record. Run analysis from NGS to generate verified results.</p>
                {save.isError && <p role="alert" className="mt-3 text-sm text-error">{projectManagerErrorMessage(save.error)}</p>}
                <div className="mt-4 flex justify-end gap-2"><button type="button" disabled={save.isPending} onClick={onClose} className="rounded-lg border border-border-primary px-3 py-2 text-sm text-content-secondary">Cancel</button><button type="submit" disabled={save.isPending || !draft.title.trim()} className="rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">{save.isPending ? 'Saving data record…' : 'Save data record'}</button></div>
            </form>
        </section>
    </div>;
}

export function NgsDataRecords({ projectId, onAdd }: { projectId: string; onAdd: () => void }) {
    const query = useInfiniteQuery({ queryKey: ['ngs-data-records', projectId], initialPageParam: undefined as string | undefined,
        queryFn: ({ pageParam, signal }) => listNgsDataRecords(projectId, pageParam, signal), getNextPageParam: (page) => page.next_cursor ?? undefined });
    const records = query.data?.pages.flatMap((page) => page.items) ?? [];
    return <section aria-label="Project NGS data records" className="m-4 rounded-xl border border-border-primary bg-surface-secondary p-5">
        <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold text-content">NGS data records</h2><button type="button" onClick={onAdd} className="rounded-lg border border-accent px-3 py-2 text-xs font-semibold text-accent">Add NGS data record</button></div>
        {query.isPending ? <p role="status" className="mt-3 text-sm text-content-secondary">Loading NGS data records…</p> : query.isError ? <p role="alert" className="mt-3 text-sm text-error">{projectManagerErrorMessage(query.error)} <button onClick={() => void query.refetch()} className="underline">Retry</button></p> : !records.length ? <p className="mt-3 text-sm text-content-secondary">No NGS data records yet. Add raw reads, a run location, or an analysis record to this Project.</p> : <div className="mt-4 grid gap-3 lg:grid-cols-2">{records.map(({ id, created_at, payload }) => <article key={id} className="rounded-lg border border-border-primary bg-surface p-4"><h3 className="font-semibold text-content">{payload.title}</h3><p className="mt-1 text-xs text-content-secondary">{payload.sample_name || 'No sample name'} · {NGS_DATA_FORMATS[payload.data_format]} · {NGS_USE_CASES[payload.use_case]}</p><p className="mt-1 text-xs text-content-secondary">{NGS_RECORD_STAGES[payload.workflow_stage]} · {new Date(created_at).toLocaleString()}</p>{payload.data_location && <p className="mt-2 break-all text-xs text-content-secondary">Data location: {payload.data_location}</p>}{payload.notes && <p className="mt-2 whitespace-pre-wrap text-sm text-content-secondary">{payload.notes}</p>}<ul className="mt-2 space-y-1">{payload.files.map((file) => <li key={file.path} className="text-xs"><a href={`/api/files/download/${file.path.split('/').map(encodeURIComponent).join('/')}`} className="text-accent underline">{file.name}</a> · {file.size.toLocaleString()} bytes</li>)}</ul></article>)}</div>}
        {query.hasNextPage && <button disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()} className="mt-3 text-sm text-accent">Load more data records</button>}
    </section>;
}
