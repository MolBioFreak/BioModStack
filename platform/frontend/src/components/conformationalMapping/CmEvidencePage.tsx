import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { cmApiError, getCmRecordPage, type CmRecord } from './conformationalMappingApi';

interface Props {
    requestId: string;
    record: CmRecord;
    collection: string;
    label: string;
    getPage?: typeof getCmRecordPage;
}

// Remount on immutable authority changes, including when the request route is reused.
export function CmEvidencePage(props: Props) {
    return <EvidencePage key={JSON.stringify([props.requestId, props.record.type, props.record.key, props.record.sha256, props.collection])} {...props} />;
}

function EvidencePage({ requestId, record, collection, label, getPage = getCmRecordPage }: Props) {
    const [offset, setOffset] = useState(0);
    const limit = 100;
    const query = useQuery({
        queryKey: ['cm-record-evidence', requestId, record.type, record.key, record.sha256, collection, offset],
        queryFn: async () => {
            const page = await getPage(requestId, record.type, record.key, collection, offset, limit);
            if (page.request_id !== requestId || page.record_type !== record.type || page.record_key !== record.key
                || page.sha256 !== record.sha256 || page.collection !== collection || page.offset !== offset || page.limit !== limit
                || (record.artifact && (page.artifact?.artifact_id !== record.artifact.artifact_id
                    || page.artifact?.content_sha256 !== record.artifact.content_sha256))
                || !Array.isArray(page.rows) || page.rows.length > limit) {
                throw new Error('Evidence page does not match the requested record identity or bounds.');
            }
            return page;
        },
        retry: false,
    });
    const page = query.isError ? null : query.data;
    const rows = page?.rows.map((row) => row && typeof row === 'object' && !Array.isArray(row)
        ? row as Record<string, unknown> : { value: row }) || [];
    const columns = [...new Set(rows.flatMap(Object.keys))];
    const total = page?.total_count ?? record.pages?.[collection]?.total_count;
    return <section aria-label={label} className="rounded-xl border border-slate-800 p-3">
        <h3 className="text-sm font-medium text-white">{label}</h3>
        <p className="my-2 text-xs text-slate-400">{rows.length} loaded · {total == null ? 'total unavailable' : `${total} total`}{rows.length ? ` · rows ${offset + 1}–${offset + rows.length}` : ''}</p>
        {query.isError && <p role="alert">{cmApiError(query.error, 'Evidence page unavailable.')}</p>}
        {query.isLoading && <p>Loading bounded evidence page…</p>}
        {page && !rows.length && <p>No records in this collection.</p>}
        {rows.length > 0 && <div className="max-h-96 overflow-auto"><table className="w-full text-left text-xs"><thead><tr>{columns.map((column) => <th key={column} className="p-2">{column}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={offset + index}>{columns.map((column) => <td key={column} className="p-2 align-top">{row[column] == null ? '—' : typeof row[column] === 'object' ? JSON.stringify(row[column]) : String(row[column])}</td>)}</tr>)}</tbody></table></div>}
        <div className="mt-3 flex gap-2">
            <button type="button" disabled={offset === 0 || query.isFetching} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous page</button>
            <button type="button" disabled={!page || page.next_offset == null || query.isFetching} onClick={() => setOffset(page!.next_offset!)}>Next page</button>
        </div>
    </section>;
}
