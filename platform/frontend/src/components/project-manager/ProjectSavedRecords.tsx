import { useState } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getProjectWorkflowSetup, listProjectResearchRecords, projectManagerErrorMessage, type ProjectHubResponse } from '../../lib/projectManager';
import { nativeDestination } from './ProjectWorkflowSetup';

export function ProjectSavedRecords({ project }: { project: ProjectHubResponse['project'] }) {
    const [open, setOpen] = useState(false);
    const [selected, select] = useState<string>();
    const records = useInfiniteQuery({ queryKey: ['project-records', project.id], enabled: open,
        initialPageParam: undefined as string | undefined,
        queryFn: ({pageParam, signal}) => listProjectResearchRecords(project.id, pageParam, signal),
        getNextPageParam: page => page.next_cursor ?? undefined });
    const setup = useQuery({ queryKey: ['saved-setup', project.id, selected], enabled: !!selected,
        queryFn: () => getProjectWorkflowSetup(project.id, selected!) });
    return <div className="space-y-3">
        <details className="rounded-lg border border-slate-700 p-3" onToggle={e => setOpen(e.currentTarget.open)}>
            <summary className="cursor-pointer font-semibold">Notes and research records</summary>
            {open && <>{records.isLoading && <p>Loading records…</p>}
                {records.error && <p role="alert">{projectManagerErrorMessage(records.error)}</p>}
                {records.data?.pages[0]?.items.length === 0 && <p>No saved records.</p>}
                {records.data?.pages.flatMap(page => page.items).map(r => <article className="border-t border-slate-700 py-3" key={r.id}>
                    <h3 className="font-medium capitalize">{r.record_kind} · {r.subject_name}</h3>
                    <p className="text-xs text-slate-300">{r.author} · {r.created_at.slice(0, 10)}</p>
                    <p className="whitespace-pre-wrap break-words">{r.body}</p>
                </article>)}
                {records.hasNextPage && <button disabled={records.isFetchingNextPage} onClick={() => void records.fetchNextPage()}>Load more records</button>}
            </>}
        </details>
        {!!project.workflow_setups?.length && <details className="rounded-lg border border-slate-700 p-3">
            <summary className="cursor-pointer font-semibold">Saved workflow setups ({project.workflow_setups.length})</summary>
            <ul>{project.workflow_setups.map(s => <li key={s.id}><button className="py-1 text-cyan-300" onClick={() => select(s.id)}>{s.label} · {s.state}</button></li>)}</ul>
            {selected && setup.isLoading && <p>Loading setup…</p>}
            {setup.error && <p role="alert">{projectManagerErrorMessage(setup.error)}</p>}
            {setup.data && <article><h3>{setup.data.workflow_label} · {setup.data.experiment_label}</h3>
                <pre className="overflow-auto whitespace-pre-wrap">{JSON.stringify(setup.data.draft, null, 2)}</pre>
                {setup.data.state === 'open' && <Link className="text-cyan-300" to={nativeDestination(setup.data)}>Resume saved setup</Link>}
            </article>}
        </details>}
    </div>;
}
