import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { Job, StructureSourceSelection } from '../lib/api';
import { fetchNativeBinderGeneration, nativeDocumentLabel } from '../lib/nativeBinderResults';
import { JobBrowser } from './JobBrowser';

/** Lists retained native documents, including alternate and rejected records. */
export function DeNovoNativeDocumentPicker({ onSelect }: { onSelect: (source: StructureSourceSelection) => void }) {
    const [job, setJob] = useState<Job>();
    const [offset, setOffset] = useState(0);
    const limit = 50;
    const page = useQuery({ queryKey: ['native-source-documents', job?.id, offset, limit],
        queryFn: ({ signal }) => fetchNativeBinderGeneration(job!.id, offset, limit, signal), enabled: !!job, retry: false });
    return <div className="space-y-3">
        <JobBrowser status={null} selectedJobId={job?.id} onSelect={next => { setJob(next); setOffset(0); }} />
        {page.isFetching && <p role="status">Reading native documents…</p>}
        {page.error && <p role="status">{page.error.message}. No primary-document substitute was selected.</p>}
        {page.data?.records.map((record, index) => <section key={String(record.candidate_key ?? record.design_id ?? index)}>
            <p>{String(record.name ?? record.candidate_key ?? record.design_id ?? `Record ${offset + index + 1}`)}</p>
            {(record.structures ?? []).map(doc => <button type="button" key={`${doc.artifact_id}:${doc.target_state ?? ''}`}
                aria-label={`Use native document ${doc.artifact_id}`} onClick={() => onSelect({
                    candidate: { kind: 'native_document', job_id: job!.id, artifact_id: doc.artifact_id,
                        ...(doc.target_state != null ? { target_state: doc.target_state } : {}) },
                    ...(doc.sha256 ? { expected_sha256: doc.sha256 } : {}), output_format: 'native',
                })}>{nativeDocumentLabel(doc)} · {doc.logical_path ?? doc.artifact_id}{doc.primary ? ' · primary' : ''}</button>)}
            {!record.structures?.length && record.candidate_ref && ['design', 'native_document'].includes(record.candidate_ref.kind) && <button type="button" onClick={() => onSelect({ candidate: record.candidate_ref!, output_format: 'native' })}>Use exact retained candidate</button>}
        </section>)}
        {page.data && <nav aria-label="Native source pages"><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous documents</button>
            <span>Records {page.data.records.length ? offset + 1 : 0}–{offset + page.data.records.length} of {page.data.total}</span>
            <button type="button" disabled={offset + page.data.records.length >= page.data.total} onClick={() => setOffset(offset + limit)}>Next documents</button></nav>}
    </div>;
}
