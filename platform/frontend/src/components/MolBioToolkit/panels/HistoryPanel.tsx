import { useEffect, useState } from 'react';
import {
    fetchMolecularRevisions,
    type MolecularRevision,
} from '../../../lib/api';
import type { HistoryEntry } from '../hooks/useSequenceHistory';
import type { SequenceData } from '../types';

interface WorkspaceSummary {
    id: string;
    title: string;
    sequenceId: string | null;
    dirty: boolean;
    sequenceType: SequenceData['sequenceType'];
    sequenceLength: number;
}

interface HistoryPanelProps {
    sequenceData: SequenceData;
    selectedSequenceId: string | null;
    historyJournal: HistoryEntry[];
    workspaces: WorkspaceSummary[];
    activeWorkspaceId: string;
    onActivateWorkspace: (workspaceId: string) => void;
    revisionHref: (sequenceId: string, revisionId: string) => string;
}

function humanizeOperation(value?: string | null): string | null {
    if (!value) return null;
    return value.replace(/_/g, ' ');
}

function validateRevisionList(sequenceId: string, revisions: MolecularRevision[]): MolecularRevision[] {
    revisions.forEach((revision) => {
        if (
            revision.sequence_id !== sequenceId
            || revision.document_id !== sequenceId
            || revision.reopen_destination.surface !== 'molbio-sequence-revision'
            || revision.reopen_destination.params.sequence_id !== sequenceId
            || revision.reopen_destination.params.revision_id !== revision.revision_id
        ) {
            throw new Error('Server immutable revision history returned a mismatched sequence/revision authority.');
        }
    });
    return revisions;
}

export function HistoryPanel({
    sequenceData,
    selectedSequenceId,
    historyJournal,
    workspaces,
    activeWorkspaceId,
    onActivateWorkspace,
    revisionHref,
}: HistoryPanelProps) {
    const [serverRevisions, setServerRevisions] = useState<MolecularRevision[]>([]);
    const [serverLoading, setServerLoading] = useState(false);
    const [serverError, setServerError] = useState<string | null>(null);
    const recentEntries = [...historyJournal].reverse().slice(0, 40);
    const lineageBits = [
        sequenceData.version != null ? `v${sequenceData.version}` : null,
        humanizeOperation(sequenceData.operation),
        sequenceData.parentId ? `Parent ${sequenceData.parentId.slice(0, 8)}` : null,
    ].filter(Boolean);

    useEffect(() => {
        if (!selectedSequenceId) {
            setServerRevisions([]);
            setServerLoading(false);
            setServerError(null);
            return;
        }

        let cancelled = false;
        setServerRevisions([]);
        setServerLoading(true);
        setServerError(null);
        void fetchMolecularRevisions(selectedSequenceId)
            .then((revisions) => validateRevisionList(selectedSequenceId, revisions))
            .then((revisions) => {
                if (!cancelled) setServerRevisions(revisions);
            })
            .catch((error) => {
                if (!cancelled) {
                    setServerError(error instanceof Error ? error.message : String(error));
                }
            })
            .finally(() => {
                if (!cancelled) setServerLoading(false);
            });

        return () => {
            cancelled = true;
        };
    }, [selectedSequenceId]);

    return (
        <div className="space-y-4 p-3 text-sm">
            <div>
                <h4 className="font-semibold text-slate-200">History & Lineage</h4>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                    Server immutable revisions and local edit/undo entries are distinct authorities and are never merged.
                </p>
            </div>

            <div className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-full bg-slate-800 px-2.5 py-1 text-xs text-slate-200">
                        {selectedSequenceId ? 'Saved construct' : 'Unsaved workspace'}
                    </span>
                    <span className="rounded-full bg-slate-800 px-2.5 py-1 text-xs uppercase text-slate-300">
                        {sequenceData.sequenceType}
                    </span>
                    <span className="rounded-full bg-slate-800 px-2.5 py-1 text-xs text-slate-300">
                        {sequenceData.circular ? 'circular' : 'linear'}
                    </span>
                    {lineageBits.map((item) => (
                        <span key={item} className="rounded-full bg-cyan-950/40 px-2.5 py-1 text-xs text-cyan-300">
                            {item}
                        </span>
                    ))}
                </div>

                <div className="grid gap-2 text-xs text-slate-400">
                    <div>{sequenceData.sequence.length.toLocaleString()} nt • {sequenceData.features.length} features • {(sequenceData.primers || []).length} primers</div>
                    {sequenceData.accession && <div>Accession: {sequenceData.accession}</div>}
                    {sequenceData.sourceFile && <div>Source file: {sequenceData.sourceFile}</div>}
                    {sequenceData.description && (
                        <div className="rounded border border-slate-800 bg-slate-950/70 px-3 py-2 text-slate-300">
                            {sequenceData.description}
                        </div>
                    )}
                </div>
            </div>

            <section className="space-y-3 rounded-xl border border-cyan-800/70 bg-slate-900/50 p-3" aria-label="Server immutable revision history">
                <div className="flex items-center justify-between gap-3">
                    <div className="text-[11px] uppercase tracking-[0.12em] text-cyan-300">Server immutable revision history</div>
                    <div className="text-[11px] text-slate-500">{serverRevisions.length} revisions</div>
                </div>
                {!selectedSequenceId ? (
                    <div className="rounded border border-dashed border-slate-700 bg-slate-950/40 px-3 py-4 text-xs text-slate-500">
                        Save this workspace before immutable server revision history is available.
                    </div>
                ) : serverLoading ? (
                    <div className="text-xs text-cyan-200">Loading immutable server revisions…</div>
                ) : serverError ? (
                    <div className="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-xs text-red-300">
                        Unable to load server immutable revision history: {serverError}
                    </div>
                ) : serverRevisions.length === 0 ? (
                    <div className="rounded border border-dashed border-slate-700 bg-slate-950/40 px-3 py-4 text-xs text-slate-500">
                        The server returned no immutable revisions for this saved construct.
                    </div>
                ) : (
                    <div className="space-y-2">
                        {serverRevisions.map((revision) => (
                            <article key={revision.revision_id} className="rounded border border-slate-800 bg-slate-950/70 px-3 py-3">
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                    <div>
                                        <div className="font-medium text-slate-200">
                                            Revision #{revision.revision_number} · {revision.relation}
                                        </div>
                                        <div className="mt-1 text-[11px] text-slate-500">
                                            {new Date(revision.created_at).toLocaleString()}
                                        </div>
                                    </div>
                                    <a
                                        href={revisionHref(revision.sequence_id, revision.revision_id)}
                                        className="rounded border border-cyan-700 bg-cyan-950/50 px-2.5 py-1 text-xs font-medium text-cyan-200 hover:bg-cyan-900/50"
                                    >
                                        Reopen exact revision
                                    </a>
                                </div>
                                <dl className="mt-3 grid gap-2 text-xs">
                                    <div><dt className="text-slate-500">Sequence ID</dt><dd className="break-all font-mono text-slate-300">{revision.sequence_id}</dd></div>
                                    <div><dt className="text-slate-500">Revision ID</dt><dd className="break-all font-mono text-slate-300">{revision.revision_id}</dd></div>
                                    <div><dt className="text-slate-500">Content digest</dt><dd className="break-all font-mono text-slate-300">{revision.content_sha256}</dd></div>
                                    <div><dt className="text-slate-500">Relation</dt><dd className="text-slate-300">{revision.relation}</dd></div>
                                </dl>
                            </article>
                        ))}
                    </div>
                )}
            </section>

            <div className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Open Workspaces</div>
                <div className="space-y-2">
                    {workspaces.map((workspace) => (
                        <button
                            key={workspace.id}
                            type="button"
                            onClick={() => onActivateWorkspace(workspace.id)}
                            className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                                workspace.id === activeWorkspaceId
                                    ? 'border-cyan-500/50 bg-slate-800 text-slate-100'
                                    : 'border-slate-700 bg-slate-950/60 text-slate-300 hover:border-slate-500'
                            }`}
                        >
                            <div className="flex items-center justify-between gap-3">
                                <div className="min-w-0">
                                    <div className="truncate font-medium">
                                        {workspace.title}
                                        {workspace.dirty ? ' *' : ''}
                                    </div>
                                    <div className="mt-1 text-[11px] uppercase tracking-[0.08em] text-slate-500">
                                        {workspace.sequenceType} • {workspace.sequenceLength.toLocaleString()} nt
                                    </div>
                                </div>
                                {workspace.sequenceId && (
                                    <span className="text-[10px] text-slate-500">{workspace.sequenceId.slice(0, 8)}</span>
                                )}
                            </div>
                        </button>
                    ))}
                </div>
            </div>

            <section className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/50 p-3" aria-label="Local edit and undo history">
                <div className="flex items-center justify-between gap-3">
                    <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Local edit/undo history</div>
                    <div className="text-[11px] text-slate-500">{historyJournal.length} entries</div>
                </div>
                {recentEntries.length === 0 ? (
                    <div className="rounded border border-dashed border-slate-700 bg-slate-950/40 px-3 py-4 text-xs text-slate-500">
                        No local edit/undo entries recorded in this workspace yet.
                    </div>
                ) : (
                    <div className="space-y-2">
                        {recentEntries.map((entry) => (
                            <div key={entry.id} className="rounded border border-slate-800 bg-slate-950/70 px-3 py-2">
                                <div className="flex items-center justify-between gap-3">
                                    <div className="font-medium text-slate-200">{entry.label}</div>
                                    <div className="text-[11px] text-slate-500">
                                        {new Date(entry.timestamp).toLocaleString()}
                                    </div>
                                </div>
                                <div className="mt-1 text-xs text-slate-400">{entry.summary}</div>
                            </div>
                        ))}
                    </div>
                )}
            </section>
        </div>
    );
}
