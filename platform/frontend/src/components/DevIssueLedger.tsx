import { useMemo, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { buildIdentity } from '../lib/buildIdentity';

type DevIssueStatus = 'open' | 'in_progress' | 'cleared';

type DevIssue = {
    id: number;
    issue_key: string;
    body: string;
    status: DevIssueStatus;
    scope_kind: string;
    scope_key: string;
    page_label: string;
    route: string;
    component_hint: string | null;
    author_kind: 'operator' | 'ai';
    frontend_revision: string | null;
    api_revision: string;
    created_at: string;
    cleared_at: string | null;
    resolution_note: string | null;
};

type DevIssueList = {
    items: DevIssue[];
    active_count: number;
    available: boolean;
};

type IssueScope = {
    kind: string;
    key: string;
    label: string;
};

const PAGE_SCOPES: Array<[RegExp, string, string]> = [
    [/^\/$/, 'page:dashboard', 'Dashboard'],
    [/^\/projects(?:\/|$)/, 'page:project-manager', 'Project Manager'],
    [/^\/submit$/, 'page:job-launcher', 'Job Launcher'],
    [/^\/designs$/, 'page:data-viewer', 'Data Viewer'],
    [/^\/results$/, 'page:data-viewer', 'Data Viewer'],
    [/^\/designer$/, 'module:molbio-toolkit', 'Mol Bio Toolkit'],
    [/^\/ngs$/, 'module:ngs-toolkit', 'NGS Toolkit'],
    [/^\/stats$/, 'module:stats-toolkit', 'Stats Toolkit'],
    [/^\/infra$/, 'page:system-analytics', 'System Analytics'],
    [/^\/bioxp$/, 'module:bioxp', 'BioXP Handler'],
];

function resolveIssueScope(pathname: string, search: string): IssueScope {
    const resultMatch = pathname.match(/^\/designs\/([^/]+)$/);
    if (resultMatch) {
        return { kind: 'result', key: `result:${decodeURIComponent(resultMatch[1])}`, label: `Result ${decodeURIComponent(resultMatch[1])}` };
    }

    const jobMatch = pathname.match(/^\/jobs\/([^/]+)$/);
    if (jobMatch) {
        return { kind: 'job', key: `job:${decodeURIComponent(jobMatch[1])}`, label: `Job ${decodeURIComponent(jobMatch[1])}` };
    }

    if (pathname === '/submit') {
        const template = new URLSearchParams(search).get('template')?.trim();
        if (template) {
            return { kind: 'workflow', key: `workflow:${template}`, label: `Workflow: ${template}` };
        }
    }

    for (const [pattern, key, label] of PAGE_SCOPES) {
        if (pattern.test(pathname)) {
            return { kind: key.split(':', 1)[0], key, label };
        }
    }

    return { kind: 'route', key: `route:${pathname}`, label: pathname };
}

async function fetchIssues(scopeKey: string, allOpen: boolean): Promise<DevIssueList> {
    const params = new URLSearchParams({ status: allOpen ? 'active' : 'all', limit: '100' });
    if (!allOpen) params.set('scope_key', scopeKey);
    const response = await fetch(`/api/dev/issues?${params.toString()}`, { cache: 'no-store' });
    if (response.status === 404) return { items: [], active_count: 0, available: false };
    if (!response.ok) throw new Error(`Issues unavailable (${response.status})`);
    const payload = await response.json() as Omit<DevIssueList, 'available'>;
    return { ...payload, available: true };
}

async function createIssue(payload: {
    body: string;
    scope_kind: string;
    scope_key: string;
    page_label: string;
    route: string;
    component_hint: string | null;
}): Promise<DevIssue> {
    const response = await fetch('/api/dev/issues', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            ...payload,
            author_kind: 'operator',
            frontend_revision: buildIdentity.revision,
        }),
    });
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    if (!response.ok) throw new Error(String(body?.detail || `Issue save failed (${response.status})`));
    return body as DevIssue;
}

async function setIssueStatus(issueId: number, status: DevIssueStatus): Promise<DevIssue> {
    const response = await fetch(`/api/dev/issues/${issueId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
    });
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    if (!response.ok) throw new Error(String(body?.detail || `Issue update failed (${response.status})`));
    return body as DevIssue;
}

function formatDate(value: string): string {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export function DevIssueLedger() {
    const location = useLocation();
    const queryClient = useQueryClient();
    const [isOpen, setIsOpen] = useState(false);
    const [allOpen, setAllOpen] = useState(false);
    const [body, setBody] = useState('');
    const [componentHint, setComponentHint] = useState('');
    const scope = useMemo(
        () => resolveIssueScope(location.pathname, location.search),
        [location.pathname, location.search],
    );
    const route = `${location.pathname}${location.search}`;

    const issuesQuery = useQuery({
        queryKey: ['dev-issues', scope.key, allOpen],
        queryFn: () => fetchIssues(scope.key, allOpen),
        retry: false,
        refetchOnWindowFocus: true,
    });

    const refreshIssues = async () => {
        await queryClient.invalidateQueries({ queryKey: ['dev-issues'] });
    };

    const createMutation = useMutation({
        mutationFn: () => createIssue({
            body: body.trim(),
            scope_kind: scope.kind,
            scope_key: scope.key,
            page_label: scope.label,
            route,
            component_hint: componentHint.trim() || null,
        }),
        onSuccess: async () => {
            setBody('');
            setComponentHint('');
            await refreshIssues();
        },
    });

    const statusMutation = useMutation({
        mutationFn: ({ issueId, status }: { issueId: number; status: DevIssueStatus }) => setIssueStatus(issueId, status),
        onSuccess: refreshIssues,
    });

    if (issuesQuery.data?.available === false) return null;

    const items = issuesQuery.data?.items ?? [];
    const currentOpenCount = allOpen
        ? items.filter((issue) => issue.scope_key === scope.key && issue.status !== 'cleared').length
        : issuesQuery.data?.active_count ?? 0;

    const save = () => {
        if (!body.trim() || createMutation.isPending) return;
        createMutation.mutate();
    };

    return (
        <>
            <button
                type="button"
                onClick={() => {
                    setAllOpen(false);
                    setIsOpen(true);
                }}
                className="fixed bottom-4 right-4 z-[70] flex items-center gap-2 rounded-full border border-amber-400/50 bg-slate-950/95 px-4 py-2 text-sm font-semibold text-amber-200 shadow-xl backdrop-blur hover:border-amber-300 hover:text-amber-100"
                data-bms-dev-issues-trigger="true"
                aria-label="Open development issues"
            >
                Issues
                {currentOpenCount > 0 && <span className="rounded-full bg-amber-400 px-2 py-0.5 text-xs text-slate-950">{currentOpenCount}</span>}
            </button>

            {isOpen && (
                <>
                    <button
                        type="button"
                        aria-label="Close development issues"
                        className="fixed inset-0 z-[80] cursor-default bg-black/35"
                        onClick={() => setIsOpen(false)}
                    />
                    <aside
                        className="fixed inset-y-0 right-0 z-[90] flex w-[min(94vw,26rem)] flex-col border-l border-slate-700 bg-slate-950 shadow-2xl"
                        data-bms-dev-issues-drawer="true"
                    >
                        <header className="border-b border-slate-800 p-4">
                            <div className="flex items-start justify-between gap-3">
                                <div>
                                    <p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-300">Development issues</p>
                                    <p className="mt-1 break-all text-sm text-slate-300">{scope.label}</p>
                                </div>
                                <button type="button" onClick={() => setIsOpen(false)} className="rounded px-2 py-1 text-sm text-slate-400 hover:bg-slate-800 hover:text-white">Close</button>
                            </div>
                            <div className="mt-3 flex gap-2">
                                <button type="button" onClick={() => setAllOpen(false)} className={`rounded px-3 py-1.5 text-xs font-medium ${!allOpen ? 'bg-amber-400 text-slate-950' : 'bg-slate-800 text-slate-300'}`}>Current scope</button>
                                <button type="button" onClick={() => setAllOpen(true)} className={`rounded px-3 py-1.5 text-xs font-medium ${allOpen ? 'bg-amber-400 text-slate-950' : 'bg-slate-800 text-slate-300'}`}>All active</button>
                            </div>
                        </header>

                        <section className="border-b border-slate-800 p-4">
                            <label className="block text-xs font-medium text-slate-300">
                                Issue
                                <textarea
                                    value={body}
                                    maxLength={4000}
                                    rows={4}
                                    autoFocus
                                    onChange={(event) => setBody(event.target.value)}
                                    placeholder="What is missing or broken?"
                                    className="mt-1.5 w-full resize-y rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:border-amber-400"
                                />
                            </label>
                            <label className="mt-3 block text-xs font-medium text-slate-300">
                                Module/component <span className="font-normal text-slate-500">optional</span>
                                <input
                                    value={componentHint}
                                    maxLength={240}
                                    onChange={(event) => setComponentHint(event.target.value)}
                                    placeholder="For example: Retry button or sequence chart"
                                    className="mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:border-amber-400"
                                />
                            </label>
                            <div className="mt-3 flex justify-end">
                                <button type="button" disabled={!body.trim() || createMutation.isPending} onClick={save} className="rounded-lg bg-amber-400 px-4 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40">
                                    {createMutation.isPending ? 'Saving…' : 'Save'}
                                </button>
                            </div>
                            {createMutation.error && <p role="alert" className="mt-2 text-xs text-red-300">{createMutation.error.message}</p>}
                        </section>

                        <section className="min-h-0 flex-1 overflow-y-auto p-4">
                            {issuesQuery.isLoading && <p className="text-sm text-slate-400">Loading issues…</p>}
                            {issuesQuery.error && <p role="alert" className="text-sm text-red-300">{issuesQuery.error.message}</p>}
                            {!issuesQuery.isLoading && !issuesQuery.error && items.length === 0 && <p className="rounded-lg border border-slate-800 p-4 text-sm text-slate-500">No issues in this view.</p>}
                            <div className="space-y-3">
                                {items.map((issue) => (
                                    <article key={issue.id} className="rounded-lg border border-slate-800 bg-slate-900/70 p-3">
                                        <div className="flex items-start justify-between gap-3">
                                            <div>
                                                <span className="font-mono text-xs text-amber-300">{issue.issue_key}</span>
                                                <span className="ml-2 text-[11px] uppercase tracking-wide text-slate-500">{issue.author_kind}</span>
                                            </div>
                                            <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${issue.status === 'open' ? 'bg-amber-400/15 text-amber-200' : issue.status === 'in_progress' ? 'bg-sky-400/15 text-sky-200' : 'bg-emerald-400/15 text-emerald-200'}`}>{issue.status.replace('_', ' ')}</span>
                                        </div>
                                        <p className="mt-2 whitespace-pre-wrap text-sm leading-5 text-slate-200">{issue.body}</p>
                                        {issue.component_hint && <p className="mt-2 text-xs text-slate-400"><span className="text-slate-500">Component:</span> {issue.component_hint}</p>}
                                        {allOpen && <p className="mt-2 text-xs text-slate-500">{issue.page_label}</p>}
                                        <p className="mt-2 text-[11px] text-slate-600">{formatDate(issue.created_at)}</p>
                                        <div className="mt-3 flex justify-end gap-2">
                                            {issue.status === 'open' && (
                                                <button
                                                    type="button"
                                                    disabled={statusMutation.isPending}
                                                    onClick={() => statusMutation.mutate({ issueId: issue.id, status: 'in_progress' })}
                                                    className="rounded border border-sky-500/60 px-2.5 py-1 text-xs text-sky-200 hover:border-sky-300 hover:text-white disabled:opacity-40"
                                                >
                                                    Mark in progress
                                                </button>
                                            )}
                                            {issue.status !== 'cleared' ? (
                                                <button
                                                    type="button"
                                                    disabled={statusMutation.isPending}
                                                    onClick={() => statusMutation.mutate({ issueId: issue.id, status: 'cleared' })}
                                                    className="rounded border border-emerald-500/60 px-2.5 py-1 text-xs text-emerald-200 hover:border-emerald-300 hover:text-white disabled:opacity-40"
                                                >
                                                    Clear
                                                </button>
                                            ) : (
                                                <button
                                                    type="button"
                                                    disabled={statusMutation.isPending}
                                                    onClick={() => statusMutation.mutate({ issueId: issue.id, status: 'open' })}
                                                    className="rounded border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:border-slate-500 hover:text-white disabled:opacity-40"
                                                >
                                                    Reopen
                                                </button>
                                            )}
                                        </div>
                                    </article>
                                ))}
                            </div>
                        </section>
                    </aside>
                </>
            )}
        </>
    );
}
