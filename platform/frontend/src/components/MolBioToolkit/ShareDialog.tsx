/**
 * ShareDialog - create and manage temporary read-only guest links for MolBio items.
 * Tailnet-side only; the link and passcode are shown once on creation.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { listProjects, type ProjectListItem } from '../../lib/projectManager';
import {
    createMolBioShare,
    describeShareScope,
    fetchMolBioShareConfig,
    formatShareRemaining,
    listMolBioShares,
    revokeMolBioShare,
    type MolBioShareConfig,
    type MolBioShareCreated,
    type MolBioShareGrant,
    type MolBioShareScope,
    type MolBioShareSecurity,
} from '../../lib/molbioShares';

export interface ShareableItem {
    id: string;
    name: string;
}

export interface ShareProjectContext {
    projectId: string;
    globalExperimentId: string;
    domainExperimentId: string;
    stateRevisionId?: string | null;
}

export interface ShareDialogProps {
    open: boolean;
    onClose: () => void;
    currentSequence: ShareableItem | null;
    sequences: ShareableItem[];
    operations: ShareableItem[];
    projectContext: ShareProjectContext | null;
}

type ScopeMode = 'sequence' | 'domain' | 'project' | 'items';

const HOUR = 3600;
const PRESETS: Array<{ label: string; seconds: number }> = [
    { label: '1h', seconds: HOUR },
    { label: '4h', seconds: 4 * HOUR },
    { label: '8h', seconds: 8 * HOUR },
    { label: '24h', seconds: 24 * HOUR },
];
const HARD_MAX_SECONDS = 24 * HOUR;

function errorText(error: unknown): string {
    return error instanceof Error && error.message ? error.message : 'Something went wrong.';
}

function CopyField({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
    const [copied, setCopied] = useState(false);
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
        } catch {
            setCopied(false);
        }
    };
    return (
        <div className="space-y-1">
            <div className="text-xs text-slate-400">{label}</div>
            <div className="flex items-center gap-2">
                <input
                    readOnly
                    value={value}
                    aria-label={label}
                    onFocus={(event) => event.currentTarget.select()}
                    className={`min-w-0 flex-1 rounded border border-slate-600 bg-slate-900 px-2 py-1.5 text-sm text-slate-100 ${mono ? 'font-mono' : ''}`}
                />
                <button
                    type="button"
                    onClick={copy}
                    className="min-h-9 rounded bg-slate-700 px-3 text-sm text-slate-100 hover:bg-slate-600"
                >
                    {copied ? 'Copied' : 'Copy'}
                </button>
            </div>
        </div>
    );
}

function ShareRow({ grant, now, onRevoked }: { grant: MolBioShareGrant; now: number; onRevoked: (grant: MolBioShareGrant) => void }) {
    const [confirming, setConfirming] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const remaining = grant.active ? formatShareRemaining(grant.expires_at, now) : null;
    const opens = grant.access_log.filter((entry) => entry.outcome === 'bundle_ok').length;
    const revoke = async () => {
        setBusy(true);
        setError(null);
        try {
            onRevoked(await revokeMolBioShare(grant.grant_id));
            setConfirming(false);
        } catch (err) {
            setError(errorText(err));
        } finally {
            setBusy(false);
        }
    };
    return (
        <li className="space-y-1 rounded border border-slate-700 bg-slate-900/60 px-2 py-2 text-sm" data-share-grant={grant.grant_id}>
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                    <div className="truncate text-slate-100">{grant.label || describeShareScope(grant.scope)}</div>
                    <div className="text-xs text-slate-400">
                        {grant.label ? `${describeShareScope(grant.scope)} · ` : ''}
                        {remaining ?? (grant.revoked_at ? 'Revoked' : 'Expired')}
                        {' · '}{grant.creator}
                        {' · '}{opens} open{opens === 1 ? '' : 's'}
                    </div>
                </div>
                {grant.active && (confirming ? (
                    <div className="flex items-center gap-1">
                        <button type="button" disabled={busy} onClick={revoke} className="min-h-9 rounded bg-red-700 px-2 text-xs text-white hover:bg-red-600 disabled:opacity-50">
                            {busy ? 'Revoking…' : 'Confirm revoke'}
                        </button>
                        <button type="button" disabled={busy} onClick={() => setConfirming(false)} className="min-h-9 rounded px-2 text-xs text-slate-300 hover:bg-slate-700">
                            Cancel
                        </button>
                    </div>
                ) : (
                    <button type="button" onClick={() => setConfirming(true)} className="min-h-9 rounded border border-red-800 px-2 text-xs text-red-300 hover:bg-red-950">
                        Revoke
                    </button>
                ))}
            </div>
            {error && <div className="text-xs text-red-300">{error}</div>}
            {grant.access_log.length > 0 && (
                <details className="text-xs text-slate-400">
                    <summary className="cursor-pointer w-fit hover:text-slate-200">Access log ({grant.access_log.length})</summary>
                    <ul className="mt-1 max-h-32 space-y-0.5 overflow-y-auto font-mono">
                        {grant.access_log.slice().reverse().map((entry, index) => (
                            <li key={index} className="break-all">
                                {String(entry.time ?? '')} {String(entry.ip ?? '')} {String(entry.outcome ?? '')}
                            </li>
                        ))}
                    </ul>
                </details>
            )}
        </li>
    );
}

function ShareSecurity({ security }: { security: MolBioShareSecurity }) {
    const quiet = security.failed_attempts_24h === 0 && security.active_bans.length === 0;
    return (
        <details className="text-sm" data-share-security>
            <summary className={`cursor-pointer w-fit text-xs ${quiet ? 'text-slate-400' : 'text-amber-300'} hover:text-slate-200`}>
                Failed attempts: {security.failed_attempts_24h} in 24h · {security.active_bans.length} banned
            </summary>
            <div className="mt-1 space-y-1 text-xs text-slate-400">
                <p>Three failed attempts from one network ban it for 7 days. Three wrong passcodes lock that share.</p>
                {security.active_bans.length > 0 && (
                    <ul className="space-y-0.5 font-mono">
                        {security.active_bans.map((ban) => (
                            <li key={ban.network} className="break-all">banned {ban.network} until {ban.until}</li>
                        ))}
                    </ul>
                )}
                {security.recent_failures.length > 0 && (
                    <ul className="max-h-32 space-y-0.5 overflow-y-auto font-mono">
                        {security.recent_failures.map((event, index) => (
                            <li key={index} className="break-all">{event.time} {event.ip} {event.outcome}</li>
                        ))}
                    </ul>
                )}
            </div>
        </details>
    );
}

export function ShareDialog({ open, onClose, currentSequence, sequences, operations, projectContext }: ShareDialogProps) {
    const defaultMode: ScopeMode = currentSequence ? 'sequence' : projectContext ? 'domain' : 'project';
    const [mode, setMode] = useState<ScopeMode>(defaultMode);
    const [projects, setProjects] = useState<ProjectListItem[] | null>(null);
    const [projectsError, setProjectsError] = useState<string | null>(null);
    const [projectId, setProjectId] = useState('');
    const [pickedSequences, setPickedSequences] = useState<Set<string>>(new Set());
    const [pickedOperations, setPickedOperations] = useState<Set<string>>(new Set());
    const [expiryPreset, setExpiryPreset] = useState<number | 'custom'>(HOUR);
    const [customHours, setCustomHours] = useState('2');
    const [label, setLabel] = useState('');
    const [config, setConfig] = useState<MolBioShareConfig | null>(null);
    const [creating, setCreating] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [created, setCreated] = useState<MolBioShareCreated | null>(null);
    const [shares, setShares] = useState<MolBioShareGrant[] | null>(null);
    const [security, setSecurity] = useState<MolBioShareSecurity | null>(null);
    const [sharesError, setSharesError] = useState<string | null>(null);
    const [now, setNow] = useState(() => Date.now());

    const refreshShares = useCallback(async () => {
        setSharesError(null);
        try {
            const list = await listMolBioShares();
            setShares(list.items);
            setSecurity(list.security);
        } catch (err) {
            setSharesError(errorText(err));
        }
    }, []);

    useEffect(() => {
        if (!open) return;
        setMode(defaultMode);
        setCreated(null);
        setError(null);
        setPickedSequences(new Set(currentSequence ? [currentSequence.id] : []));
        setPickedOperations(new Set());
        void fetchMolBioShareConfig().then(setConfig).catch((err) => setError(errorText(err)));
        void refreshShares();
        // Reset only when the dialog opens.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);

    useEffect(() => {
        if (!open || mode !== 'project' || projects !== null) return;
        const controller = new AbortController();
        listProjects(controller.signal)
            .then((page) => {
                setProjects(page.items);
                setProjectId((value) => value || projectContext?.projectId || page.items[0]?.project_id || '');
            })
            .catch((err) => {
                if (!controller.signal.aborted) setProjectsError(errorText(err));
            });
        return () => controller.abort();
    }, [mode, open, projectContext?.projectId, projects]);

    useEffect(() => {
        if (!open) return;
        const timer = window.setInterval(() => setNow(Date.now()), 30000);
        return () => window.clearInterval(timer);
    }, [open]);

    useEffect(() => {
        if (!open) return;
        const onKey = (event: KeyboardEvent) => {
            if (event.key === 'Escape') onClose();
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [onClose, open]);

    const maxSeconds = Math.min(config?.max_expiry_seconds ?? HARD_MAX_SECONDS, HARD_MAX_SECONDS);
    const minSeconds = Math.max(config?.min_expiry_seconds ?? 60, 60);
    const expirySeconds = expiryPreset === 'custom' ? Math.round(Number(customHours) * HOUR) : expiryPreset;
    const expiryValid = Number.isFinite(expirySeconds) && expirySeconds >= minSeconds && expirySeconds <= maxSeconds;

    const itemPool = useMemo(() => {
        const seen = new Map<string, ShareableItem>();
        if (currentSequence) seen.set(currentSequence.id, currentSequence);
        for (const item of sequences) if (!seen.has(item.id)) seen.set(item.id, item);
        return [...seen.values()];
    }, [currentSequence, sequences]);

    const scope: MolBioShareScope | null = useMemo(() => {
        if (mode === 'sequence') return currentSequence ? { kind: 'items', sequence_ids: [currentSequence.id], operation_ids: [] } : null;
        if (mode === 'domain') {
            return projectContext ? {
                kind: 'project_domain',
                project_id: projectContext.projectId,
                global_experiment_id: projectContext.globalExperimentId,
                domain_experiment_id: projectContext.domainExperimentId,
                state_revision_id: projectContext.stateRevisionId ?? null,
            } : null;
        }
        if (mode === 'project') return projectId ? { kind: 'project', project_id: projectId } : null;
        if (pickedSequences.size === 0 && pickedOperations.size === 0) return null;
        return { kind: 'items', sequence_ids: [...pickedSequences], operation_ids: [...pickedOperations] };
    }, [currentSequence, mode, pickedOperations, pickedSequences, projectContext, projectId]);

    const submit = async () => {
        if (!scope || !expiryValid) return;
        setCreating(true);
        setError(null);
        try {
            const result = await createMolBioShare({ scope, expires_in_seconds: expirySeconds, label: label.trim() });
            setCreated(result);
            setShares((list) => [result.grant, ...(list ?? []).filter((g) => g.grant_id !== result.grant.grant_id)]);
        } catch (err) {
            setError(errorText(err));
        } finally {
            setCreating(false);
        }
    };

    const toggle = (setter: typeof setPickedSequences, id: string) => setter((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });

    if (!open) return null;

    const activeShares = (shares ?? []).filter((g) => g.active);
    const endedShares = (shares ?? []).filter((g) => !g.active);
    const modes: Array<{ id: ScopeMode; label: string; available: boolean }> = [
        { id: 'sequence', label: 'This sequence', available: Boolean(currentSequence) },
        { id: 'domain', label: 'Current project', available: Boolean(projectContext) },
        { id: 'project', label: 'A project', available: true },
        { id: 'items', label: 'Pick items', available: itemPool.length + operations.length > 0 },
    ];

    return (
        <div
            className="fixed inset-0 z-[120] flex items-end justify-center bg-black/60 sm:items-center"
            onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
            data-molbio-share-dialog="true"
        >
            <div role="dialog" aria-modal="true" aria-label="Share" className="flex max-h-[90dvh] w-full max-w-lg flex-col rounded-t-lg border border-slate-700 bg-slate-800 text-slate-100 shadow-xl sm:rounded-lg">
                <div className="flex items-center justify-between border-b border-slate-700 px-4 py-2">
                    <h2 className="text-base font-semibold">Share read-only link</h2>
                    <button type="button" onClick={onClose} aria-label="Close" className="min-h-9 min-w-9 rounded text-slate-300 hover:bg-slate-700">✕</button>
                </div>
                <div className="space-y-4 overflow-y-auto px-4 py-3">
                    {config && !config.configured && (
                        <div className="rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
                            Sharing is not configured on this server.
                        </div>
                    )}

                    {created ? (
                        <div className="space-y-3">
                            <CopyField label="Link" value={created.link} />
                            <CopyField label="Passcode" value={created.passcode} />
                            <p className="text-xs text-amber-200">
                                The passcode is shown only once. Send it separately from the link.
                            </p>
                            <div className="flex justify-end gap-2">
                                <button type="button" onClick={() => setCreated(null)} className="min-h-9 rounded px-3 text-sm text-slate-300 hover:bg-slate-700">New share</button>
                                <button type="button" onClick={onClose} className="min-h-9 rounded bg-blue-600 px-3 text-sm text-white hover:bg-blue-500">Done</button>
                            </div>
                        </div>
                    ) : (
                        <div className="space-y-3">
                            <fieldset className="space-y-2">
                                <legend className="text-xs text-slate-400">What to share</legend>
                                <div className="flex flex-wrap gap-1">
                                    {modes.filter((m) => m.available).map((m) => (
                                        <button
                                            key={m.id}
                                            type="button"
                                            aria-pressed={mode === m.id}
                                            onClick={() => setMode(m.id)}
                                            className={`min-h-9 rounded px-2 text-sm ${mode === m.id ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}
                                        >
                                            {m.label}
                                        </button>
                                    ))}
                                </div>
                                {mode === 'sequence' && currentSequence && (
                                    <div className="truncate text-sm text-slate-300">{currentSequence.name}</div>
                                )}
                                {mode === 'domain' && (
                                    <div className="text-sm text-slate-300">All sequences and experiments in the open project view.</div>
                                )}
                                {mode === 'project' && (
                                    projectsError ? <div className="text-sm text-red-300">{projectsError}</div>
                                        : projects === null ? <div className="text-sm text-slate-400">Loading projects…</div>
                                            : projects.length === 0 ? <div className="text-sm text-slate-400">No projects.</div>
                                                : (
                                                    <select
                                                        aria-label="Project"
                                                        value={projectId}
                                                        onChange={(event) => setProjectId(event.target.value)}
                                                        className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-1.5 text-sm"
                                                    >
                                                        {projects.map((p) => <option key={p.project_id} value={p.project_id}>{p.name}</option>)}
                                                    </select>
                                                )
                                )}
                                {mode === 'items' && (
                                    <div className="max-h-48 space-y-1 overflow-y-auto rounded border border-slate-700 p-2 text-sm">
                                        {itemPool.map((item) => (
                                            <label key={`s:${item.id}`} className="flex items-center gap-2">
                                                <input type="checkbox" checked={pickedSequences.has(item.id)} onChange={() => toggle(setPickedSequences, item.id)} />
                                                <span className="truncate">{item.name}</span>
                                            </label>
                                        ))}
                                        {operations.map((item) => (
                                            <label key={`o:${item.id}`} className="flex items-center gap-2">
                                                <input type="checkbox" checked={pickedOperations.has(item.id)} onChange={() => toggle(setPickedOperations, item.id)} />
                                                <span className="truncate">{item.name}</span>
                                            </label>
                                        ))}
                                    </div>
                                )}
                            </fieldset>

                            <fieldset className="space-y-2">
                                <legend className="text-xs text-slate-400">Expires after</legend>
                                <div className="flex flex-wrap items-center gap-1">
                                    {PRESETS.filter((p) => p.seconds <= maxSeconds).map((p) => (
                                        <button
                                            key={p.label}
                                            type="button"
                                            aria-pressed={expiryPreset === p.seconds}
                                            onClick={() => setExpiryPreset(p.seconds)}
                                            className={`min-h-9 rounded px-3 text-sm ${expiryPreset === p.seconds ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}
                                        >
                                            {p.label}
                                        </button>
                                    ))}
                                    <button
                                        type="button"
                                        aria-pressed={expiryPreset === 'custom'}
                                        onClick={() => setExpiryPreset('custom')}
                                        className={`min-h-9 rounded px-3 text-sm ${expiryPreset === 'custom' ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}
                                    >
                                        Custom
                                    </button>
                                    {expiryPreset === 'custom' && (
                                        <label className="flex items-center gap-1 text-sm text-slate-300">
                                            <input
                                                type="number"
                                                aria-label="Custom expiry hours"
                                                min={minSeconds / HOUR}
                                                max={maxSeconds / HOUR}
                                                step={0.25}
                                                value={customHours}
                                                onChange={(event) => setCustomHours(event.target.value)}
                                                className="w-20 rounded border border-slate-600 bg-slate-900 px-2 py-1.5 text-sm"
                                            />
                                            hours
                                        </label>
                                    )}
                                </div>
                                {expiryPreset === 'custom' && !expiryValid && (
                                    <div className="text-xs text-red-300">Up to {maxSeconds / HOUR} hours.</div>
                                )}
                            </fieldset>

                            <label className="block space-y-1">
                                <span className="text-xs text-slate-400">Label (optional)</span>
                                <input
                                    value={label}
                                    maxLength={120}
                                    onChange={(event) => setLabel(event.target.value)}
                                    placeholder="e.g. For Dana"
                                    className="w-full rounded border border-slate-600 bg-slate-900 px-2 py-1.5 text-sm"
                                />
                            </label>

                            {error && <div role="alert" className="text-sm text-red-300">{error}</div>}

                            <div className="flex justify-end">
                                <button
                                    type="button"
                                    onClick={submit}
                                    disabled={creating || !scope || !expiryValid || config?.configured === false}
                                    className="min-h-9 rounded bg-blue-600 px-4 text-sm text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-slate-600"
                                >
                                    {creating ? 'Creating…' : 'Create link'}
                                </button>
                            </div>
                        </div>
                    )}

                    <details className="border-t border-slate-700 pt-3" onToggle={(event) => { if ((event.currentTarget as HTMLDetailsElement).open) void refreshShares(); }}>
                        <summary className="cursor-pointer w-fit text-sm text-slate-300 hover:text-white">
                            Active shares{shares ? ` (${activeShares.length})` : ''}
                        </summary>
                        <div className="mt-2 space-y-2">
                            {sharesError && <div className="text-sm text-red-300">{sharesError}</div>}
                            {shares && activeShares.length === 0 && <div className="text-sm text-slate-400">No active shares.</div>}
                            <ul className="space-y-2">
                                {activeShares.map((grant) => (
                                    <ShareRow
                                        key={grant.grant_id}
                                        grant={grant}
                                        now={now}
                                        onRevoked={(updated) => setShares((list) => (list ?? []).map((g) => g.grant_id === updated.grant_id ? { ...updated, access_log: g.access_log } : g))}
                                    />
                                ))}
                            </ul>
                            {security && <ShareSecurity security={security} />}
                            {endedShares.length > 0 && (
                                <details className="text-sm">
                                    <summary className="cursor-pointer w-fit text-xs text-slate-400 hover:text-slate-200">Ended ({endedShares.length})</summary>
                                    <ul className="mt-2 space-y-2">
                                        {endedShares.map((grant) => <ShareRow key={grant.grant_id} grant={grant} now={now} onRevoked={() => undefined} />)}
                                    </ul>
                                </details>
                            )}
                        </div>
                    </details>
                </div>
            </div>
        </div>
    );
}
