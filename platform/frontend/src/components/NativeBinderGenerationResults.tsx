import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { BindCraft2SettingsReadback } from './BindCraft2NativeResults';
import { fetchNativeBinderGeneration, nativeCandidateRoute } from '../lib/nativeBinderResults';

/** Published model-owned evidence; never depends on a worker or Design-page fetch. */
export function NativeBinderGenerationResults({ jobId, status, launchContextId }: { jobId: string; status?: string; launchContextId?: string | null }) {
    const [offset, setOffset] = useState(0);
    const query = useQuery({ queryKey: ['native-binder-generation', jobId, offset],
        queryFn: () => fetchNativeBinderGeneration(jobId, offset), retry: false,
        refetchInterval: status === 'queued' || status === 'running' ? 5000 : false });
    const page = query.data;
    return <section aria-label="Native initial-generation results" className="space-y-3 rounded-lg border border-[var(--border-color)] p-4">
        <h3 className="font-semibold">Native initial-generation results</h3>
        {query.isLoading && <p role="status">Reading published generation records…</p>}
        {query.isError && <p role="status">Native publication is not available: {String(query.error)} <button type="button" onClick={() => void query.refetch()}>Retry native readback</button></p>}
        {page && <>
            <details open><summary>Native receipt and accounting</summary><BindCraft2SettingsReadback value={page.receipt} /></details>
            <p>{page.total} native records. Native observations retain their producer semantics; no binding verdict is implied.</p>
            {page.total === 0 && <p>No native candidate records were emitted. This published zero-yield result remains available for review.</p>}
            {page.records.map((row, index) => <article key={row.candidate_key ?? page.offset + index} className="border-t border-[var(--border-color)] py-3">
                <h4>{row.candidate_key ?? `Native record ${page.offset + index + 1}`}</h4>
                <BindCraft2SettingsReadback value={Object.fromEntries(Object.entries(row).filter(([key]) => !['structures', 'design_id'].includes(key)))} />
                {row.structures?.map(doc => <p key={doc.artifact_id}>{doc.logical_path ?? doc.artifact_id} · {doc.target_state ?? 'Native state'}{doc.primary ? ' · primary' : ''}
                    {doc.download_url && <> <a href={doc.download_url} download>Download exact native document</a></>}
                    {row.design_id && <> <a href={nativeCandidateRoute(jobId, row.design_id, doc, launchContextId)}>Select exact document in candidate workbench</a></>}
                </p>)}
                {row.design_id && <a href={nativeCandidateRoute(jobId, row.design_id, undefined, launchContextId)}>Open candidate workbench</a>}
            </article>)}
            <nav aria-label="Generation result pagination">
                <button type="button" disabled={page.offset === 0} onClick={() => setOffset(Math.max(0, page.offset - page.limit))}>Previous native records</button>
                <button type="button" disabled={page.offset + page.limit >= page.total} onClick={() => setOffset(page.offset + page.limit)}>Next native records</button>
            </nav>
            <details><summary>Published native files</summary><ul>{page.artifacts?.map(file => <li key={file.path}>{file.download_url ? <a href={file.download_url} download>{file.path}</a> : file.path}</li>)}</ul></details>
            <details><summary>Publication identity</summary><BindCraft2SettingsReadback value={page.publication} /></details>
        </>}
    </section>;
}
