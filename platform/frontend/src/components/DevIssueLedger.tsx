import { useEffect, useMemo, useState, type ClipboardEvent } from 'react';
import { useLocation } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { buildIdentity } from '../lib/buildIdentity';

type DevIssueStatus = 'open' | 'in_progress' | 'cleared';
type DevIssueLane = 'general' | 'mobile';
type DevIssueView = 'current' | 'mobile' | 'all';

type DevIssue = {
    id: number;
    issue_key: string;
    body: string;
    status: DevIssueStatus;
    lane: DevIssueLane;
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
    screenshot: {
        sha256: string;
        media_type: 'image/png' | 'image/jpeg' | 'image/webp';
        byte_size: number;
        content_url: string;
    } | null;
};

const SCREENSHOT_MAX_BYTES = 10 * 1024 * 1024;
const SCREENSHOT_MEDIA_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);

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

function readIssueLane(): DevIssueLane {
    if (typeof document === 'undefined') return 'general';
    return document.documentElement.classList.contains('bms-cordova-compact') ? 'mobile' : 'general';
}

async function fetchIssues(scopeKey: string, view: DevIssueView, issueLane: DevIssueLane): Promise<DevIssueList> {
    const params = new URLSearchParams({ status: view === 'current' ? 'all' : 'active', limit: '100' });
    if (view === 'current') params.set('scope_key', scopeKey);
    if (view === 'mobile' || (view === 'current' && issueLane === 'mobile')) params.set('lane', 'mobile');
    const response = await fetch(`/api/dev/issues?${params.toString()}`, { cache: 'no-store' });
    if (response.status === 404) return { items: [], active_count: 0, available: false };
    if (!response.ok) throw new Error(`Issues unavailable (${response.status})`);
    const payload = await response.json() as Omit<DevIssueList, 'available'>;
    return { ...payload, available: true };
}

async function createIssue(payload: {
    body: string;
    lane: DevIssueLane;
    scope_kind: string;
    scope_key: string;
    page_label: string;
    route: string;
    component_hint: string | null;
}, screenshot: File | null): Promise<DevIssue> {
    const authoredPayload = {
        ...payload,
        author_kind: 'operator',
        frontend_revision: buildIdentity.revision,
    };
    let response: Response;
    if (screenshot) {
        const form = new FormData();
        for (const [key, value] of Object.entries(authoredPayload)) {
            if (value !== null) form.append(key, value);
        }
        form.append('screenshot', screenshot);
        response = await fetch('/api/dev/issues/with-screenshot', { method: 'POST', body: form });
    } else {
        response = await fetch('/api/dev/issues', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(authoredPayload),
        });
    }
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
    const [view, setView] = useState<DevIssueView>('current');
    const [issueLane, setIssueLane] = useState<DevIssueLane>(() => readIssueLane());
    const [cordovaSettingsAvailable, setCordovaSettingsAvailable] = useState(() => (
        typeof document !== 'undefined' && Boolean(document.getElementById('bms-cordova-preflight-toggle'))
    ));
    const [body, setBody] = useState('');
    const [componentHint, setComponentHint] = useState('');
    const [screenshot, setScreenshot] = useState<File | null>(null);
    const [screenshotPreviewUrl, setScreenshotPreviewUrl] = useState<string | null>(null);
    const [screenshotError, setScreenshotError] = useState<string | null>(null);
    const scope = useMemo(
        () => resolveIssueScope(location.pathname, location.search),
        [location.pathname, location.search],
    );
    const route = `${location.pathname}${location.search}`;

    useEffect(() => {
        if (!screenshot) {
            setScreenshotPreviewUrl(null);
            return;
        }
        const previewUrl = URL.createObjectURL(screenshot);
        setScreenshotPreviewUrl(previewUrl);
        return () => URL.revokeObjectURL(previewUrl);
    }, [screenshot]);

    useEffect(() => {
        if (typeof document === 'undefined' || typeof MutationObserver === 'undefined') return undefined;
        const update = () => {
            setIssueLane(readIssueLane());
            setCordovaSettingsAvailable(Boolean(document.getElementById('bms-cordova-preflight-toggle')));
        };
        const classObserver = new MutationObserver(update);
        const bodyObserver = new MutationObserver(update);
        classObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
        bodyObserver.observe(document.body, { childList: true, subtree: true });
        update();
        return () => {
            classObserver.disconnect();
            bodyObserver.disconnect();
        };
    }, []);

    const issuesQuery = useQuery({
        queryKey: ['dev-issues', scope.key, view, issueLane],
        queryFn: () => fetchIssues(scope.key, view, issueLane),
        retry: false,
        refetchOnWindowFocus: true,
    });

    const refreshIssues = async () => {
        await queryClient.invalidateQueries({ queryKey: ['dev-issues'] });
    };

    const createMutation = useMutation({
        mutationFn: () => createIssue({
            body: body.trim(),
            lane: issueLane,
            scope_kind: scope.kind,
            scope_key: scope.key,
            page_label: scope.label,
            route,
            component_hint: componentHint.trim() || null,
        }, screenshot),
        onSuccess: async () => {
            setBody('');
            setComponentHint('');
            setScreenshot(null);
            setScreenshotError(null);
            await refreshIssues();
        },
    });

    const statusMutation = useMutation({
        mutationFn: ({ issueId, status }: { issueId: number; status: DevIssueStatus }) => setIssueStatus(issueId, status),
        onSuccess: refreshIssues,
    });

    const issuesAvailable = issuesQuery.data?.available !== false;
    if (!issuesAvailable && issueLane !== 'mobile') return null;
    if (!issuesAvailable && !cordovaSettingsAvailable) return null;

    const items = issuesQuery.data?.items ?? [];
    const currentOpenCount = view !== 'current'
        ? items.filter((issue) => issue.scope_key === scope.key && issue.lane === issueLane && issue.status !== 'cleared').length
        : issuesQuery.data?.active_count ?? 0;

    const openIssues = () => {
        setView('current');
        setIsOpen(true);
    };

    const openSettings = () => {
        document.getElementById('bms-cordova-preflight-toggle')?.click();
    };

    const save = () => {
        if (!body.trim() || createMutation.isPending) return;
        createMutation.mutate();
    };

    const handlePaste = (event: ClipboardEvent<HTMLElement>) => {
        const pastedFile = Array.from(event.clipboardData.items)
            .find((item) => item.kind === 'file' && item.type.startsWith('image/'))
            ?.getAsFile();
        if (!pastedFile) return;
        event.preventDefault();
        if (!SCREENSHOT_MEDIA_TYPES.has(pastedFile.type)) {
            setScreenshotError('Screenshot must be PNG, JPEG, or WebP.');
            return;
        }
        if (pastedFile.size > SCREENSHOT_MAX_BYTES) {
            setScreenshotError('Screenshot must be 10 MiB or smaller.');
            return;
        }
        setScreenshot(pastedFile);
        setScreenshotError(null);
    };

    return (
        <>
            {issueLane === 'mobile' ? (
                <div
                    className="bms-mobile-operations-dock fixed z-[90] flex items-center gap-2 rounded-2xl border border-slate-700 bg-slate-950/95 p-2 shadow-2xl backdrop-blur"
                    data-bms-mobile-operations-dock="true"
                    role="group"
                    aria-label="Mobile operations"
                >
                    {issuesAvailable && (
                        <button
                            type="button"
                            onClick={openIssues}
                            className="flex min-h-12 flex-1 items-center justify-center gap-2 rounded-xl border border-amber-400/50 px-4 text-sm font-semibold text-amber-200"
                            data-bms-dev-issues-trigger="true"
                            aria-label="Open mobile issues"
                        >
                            Mobile Issues
                            {currentOpenCount > 0 && <span className="rounded-full bg-amber-400 px-2 py-0.5 text-xs text-slate-950">{currentOpenCount}</span>}
                        </button>
                    )}
                    <button
                        type="button"
                        onClick={openSettings}
                        disabled={!cordovaSettingsAvailable}
                        className="min-h-12 rounded-xl border border-cyan-400/45 px-4 text-sm font-semibold text-cyan-100 disabled:opacity-40"
                    >
                        Settings
                    </button>
                </div>
            ) : (
                <button
                    type="button"
                    onClick={openIssues}
                    className="fixed bottom-4 right-4 z-[70] flex items-center gap-2 rounded-full border border-amber-400/50 bg-slate-950/95 px-4 py-2 text-sm font-semibold text-amber-200 shadow-xl backdrop-blur hover:border-amber-300 hover:text-amber-100"
                    data-bms-dev-issues-trigger="true"
                    aria-label="Open development issues"
                >
                    Issues
                    {currentOpenCount > 0 && <span className="rounded-full bg-amber-400 px-2 py-0.5 text-xs text-slate-950">{currentOpenCount}</span>}
                </button>
            )}

            {issuesAvailable && isOpen && (
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
                        onPaste={handlePaste}
                    >
                        <header className="border-b border-slate-800 p-4">
                            <div className="flex items-start justify-between gap-3">
                                <div>
                                    <p className="text-xs font-semibold uppercase tracking-[0.18em] text-amber-300">{issueLane === 'mobile' ? 'Mobile issues' : 'Development issues'}</p>
                                    <p className="mt-1 break-all text-sm text-slate-300">{scope.label}</p>
                                </div>
                                <button type="button" onClick={() => setIsOpen(false)} className="rounded px-2 py-1 text-sm text-slate-400 hover:bg-slate-800 hover:text-white">Close</button>
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2">
                                <button type="button" onClick={() => setView('current')} className={`rounded px-3 py-1.5 text-xs font-medium ${view === 'current' ? 'bg-amber-400 text-slate-950' : 'bg-slate-800 text-slate-300'}`}>Current page</button>
                                <button type="button" onClick={() => setView('mobile')} className={`rounded px-3 py-1.5 text-xs font-medium ${view === 'mobile' ? 'bg-amber-400 text-slate-950' : 'bg-slate-800 text-slate-300'}`}>Mobile</button>
                                <button type="button" onClick={() => setView('all')} className={`rounded px-3 py-1.5 text-xs font-medium ${view === 'all' ? 'bg-amber-400 text-slate-950' : 'bg-slate-800 text-slate-300'}`}>All active</button>
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
                            <div className="mt-3 rounded-lg border border-dashed border-slate-700 p-3">
                                <p className="text-xs text-slate-400">Paste one PNG, JPEG, or WebP screenshot anywhere in this drawer (10 MiB max).</p>
                                {screenshot && screenshotPreviewUrl && (
                                    <div className="mt-2">
                                        <img src={screenshotPreviewUrl} alt="Screenshot preview" className="max-h-48 w-full rounded border border-slate-700 object-contain" />
                                        <div className="mt-2 flex items-center justify-between gap-3">
                                            <span className="min-w-0 truncate text-xs text-slate-400">{screenshot.name}</span>
                                            <button type="button" onClick={() => setScreenshot(null)} className="shrink-0 text-xs text-red-300 hover:text-red-200">Remove screenshot</button>
                                        </div>
                                    </div>
                                )}
                                {screenshotError && <p role="alert" className="mt-2 text-xs text-red-300">{screenshotError}</p>}
                            </div>
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
                                        {issue.screenshot && (
                                            <img
                                                src={issue.screenshot.content_url}
                                                alt={`${issue.issue_key} screenshot`}
                                                loading="lazy"
                                                className="mt-3 max-h-64 w-full rounded border border-slate-700 object-contain"
                                            />
                                        )}
                                        {issue.component_hint && <p className="mt-2 text-xs text-slate-400"><span className="text-slate-500">Component:</span> {issue.component_hint}</p>}
                                        {view !== 'current' && <p className="mt-2 text-xs text-slate-500">{issue.page_label}</p>}
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
