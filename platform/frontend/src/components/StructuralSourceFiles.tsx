import { useEffect, useRef, useState } from 'react';
import { fetchFiles, fetchProjectStructureSources, type ProjectStructureQuery, type ProjectStructureEntry } from '../lib/api';
import type { SelectedTarget } from './TargetAntigenSelector';

/** Project acquisition is independent of the destination Project launch context. */
export function ProjectStructureSources({ onSelect }: { onSelect: (source: SelectedTarget) => void }) {
    const [query, setQuery] = useState<ProjectStructureQuery>({});
    const [page, setPage] = useState<{ items: ProjectStructureEntry[]; total: number; offset: number; limit: number }>();
    const [history, setHistory] = useState<ProjectStructureQuery[]>([]);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const epoch = useRef(0);
    useEffect(() => {
        const token = ++epoch.current; setBusy(true); setError(''); setPage(undefined);
        void fetchProjectStructureSources(query).then(result => { if (token === epoch.current) setPage(result); })
            .catch(error => { if (token === epoch.current) setError(error instanceof Error ? error.message : String(error)); })
            .finally(() => { if (token === epoch.current) setBusy(false); });
        return () => { epoch.current++; };
    }, [query]);
    const navigate = (next: ProjectStructureQuery) => { setHistory([...history, query]); setQuery(next); };
    return <div className="space-y-2" aria-label="Project structure resources">
        <p className="text-xs">Browse attached resources or exact Dataset revisions. This does not change the destination Project.</p>
        {history.length > 0 && <button type="button" onClick={() => { setQuery(history[history.length - 1]); setHistory(history.slice(0, -1)); }}>Back to source list</button>}
        {query.project_id && !query.receipt_id && !query.dataset_id && <div className="flex gap-3">
            <button type="button" onClick={() => setQuery({ project_id: query.project_id, collection: 'resources' })}>Attached resources</button>
            <button type="button" onClick={() => setQuery({ project_id: query.project_id, collection: 'datasets' })}>Datasets</button>
        </div>}
        {busy && <p role="status">Loading Project sources…</p>}
        {error && <p role="alert">{error}</p>}
        {page?.items.map((entry, index) => <button type="button" key={index} className="block w-full rounded border border-[var(--border-color)] p-2 text-left text-sm" onClick={() => {
            const next: ProjectStructureQuery = { project_id: entry.project_id, dataset_id: entry.dataset_id, revision_id: entry.revision_id, receipt_id: entry.receipt_id, design_id: entry.design_id };
            if (entry.kind === 'document') {
                epoch.current++;
                onSelect({ type: 'project', name: entry.name, path: entry.path, url: entry.document?.download_url, designId: entry.design_id, jobId: entry.job_id, document: entry.document, projectSource: next });
            } else navigate(next);
        }}>{entry.name}{entry.kind === 'dataset' && ` · revision ${entry.revision_id || 'not yet saved'}`}</button>)}
        {page && page.items.length === 0 && <p>No structural sources in this selection.</p>}
        {page && <div className="flex gap-3">
            <button type="button" disabled={page.offset === 0} onClick={() => setQuery({ ...query, offset: Math.max(0, page.offset - page.limit) })}>Previous sources</button>
            <button type="button" disabled={page.offset + page.limit >= page.total} onClick={() => setQuery({ ...query, offset: page.offset + page.limit })}>Next sources</button>
        </div>}
    </div>;
}

/** Shared governed-file browsing only. Accepted formats stay with the consumer. */
export function StructuralSourceFiles({ onSelect, allowSequence = false }: {
    onSelect: (source: { path: string; name: string }) => void;
    allowSequence?: boolean;
}) {
    const [folder, setFolder] = useState('/');
    const [entries, setEntries] = useState<Array<{ path: string; name: string; is_directory: boolean }> | null>(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const epoch = useRef(0);
    useEffect(() => () => { epoch.current++; }, []);
    const browse = async (path: string) => {
        const token = ++epoch.current; setBusy(true); setError('');
        try {
            const response = await fetchFiles(path);
            if (token === epoch.current) { setEntries(response.data.entries); setFolder(path); }
        } catch (error) { if (token === epoch.current) setError(error instanceof Error ? error.message : String(error)); }
        finally { if (token === epoch.current) setBusy(false); }
    };
    const button = 'rounded-lg border border-[var(--border-color)] px-3 py-2 text-sm hover:text-accent disabled:opacity-50';
    return <div className="space-y-2 min-w-0">
        <button className={button} type="button" disabled={busy} onClick={() => void browse(folder)}>Browse managed files</button>
        {entries && <div className="max-h-64 overflow-auto rounded-lg border border-[var(--border-color)] p-2">
            <div className="flex items-center gap-2"><button type="button" className={button} disabled={folder === '/'} onClick={() => void browse('/' + folder.split('/').filter(Boolean).slice(0, -1).join('/'))}>Up one folder</button><span className="break-all text-xs">{folder}</span></div>
            {entries.filter(entry => entry.is_directory || (allowSequence ? /\.(pdb|cif|mmcif|fa|fasta|faa)$/i : /\.(pdb|cif|mmcif)$/i).test(entry.path)).map(entry => <button type="button" className="block w-full rounded p-2 text-left text-sm hover:bg-accent/10" key={entry.path} onClick={() => { if (entry.is_directory) void browse(entry.path); else { epoch.current++; setBusy(false); onSelect({ path: entry.path, name: entry.name }); } }}>{entry.is_directory ? '▸ ' : ''}{entry.name}</button>)}
        </div>}
        {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
    </div>;
}
