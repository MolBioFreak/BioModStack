import { useCallback, useEffect, useState } from 'react';
import {
    fetchRevisionRelatives,
    type RelativeEdge,
    type RelativeOperation,
    type RevisionRelatives,
} from '../../../lib/api';

export type RelativePanel = 'digest' | 'pcr' | 'edit';

export interface HistoryRelativeActions {
    revisionHref: (documentId: string, revisionId: string) => string;
    operationHref: (operationId: string) => string;
    onUseInAssembly?: (edge: RelativeEdge) => void;
    onOpenForPanel?: (edge: RelativeEdge, panel: RelativePanel) => void;
}

const kindLabel = (kind: string) => kind.replace(/_/g, ' ');

function EdgeRow({ edge, actions, actionable, depth }: {
    edge: RelativeEdge;
    actions: HistoryRelativeActions;
    actionable: boolean;
    depth: number;
}) {
    const [expanded, setExpanded] = useState(false);
    return (
        <li className="rounded border border-slate-800 bg-slate-950/70 px-2 py-1.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <a className="min-w-0 truncate text-cyan-200 hover:underline" href={actions.revisionHref(edge.document_id, edge.revision_id)}>
                    {edge.document_name} r{edge.revision_number}
                </a>
                <span className="text-[11px] text-slate-500">{edge.role} · {edge.length.toLocaleString()} bp · {edge.topology}</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-1.5 text-[11px]">
                {actionable && actions.onUseInAssembly && (
                    <button type="button" className="rounded border border-slate-600 px-1.5 py-0.5 text-slate-200" onClick={() => actions.onUseInAssembly!(edge)}>Use in assembly</button>
                )}
                {actionable && actions.onOpenForPanel && (['digest', 'pcr', 'edit'] as const).map((panel) => (
                    <button key={panel} type="button" className="rounded border border-slate-600 px-1.5 py-0.5 text-slate-200" onClick={() => actions.onOpenForPanel!(edge, panel)}>
                        {panel === 'pcr' ? 'PCR' : panel === 'digest' ? 'Digest' : 'Edit'}
                    </button>
                ))}
                <button type="button" className="rounded border border-slate-700 px-1.5 py-0.5 text-slate-400" aria-expanded={expanded} onClick={() => setExpanded((v) => !v)}>
                    {expanded ? 'Hide related' : 'Show related'}
                </button>
            </div>
            {expanded && <RelatedList revisionId={edge.revision_id} actions={actions} depth={depth + 1} />}
        </li>
    );
}

function OperationBlock({ operation, show, actions, depth, heading }: {
    operation: RelativeOperation;
    show: 'inputs' | 'outputs';
    actions: HistoryRelativeActions;
    depth: number;
    heading: string;
}) {
    const edges = operation[show];
    return (
        <article className="space-y-1 rounded border border-slate-800 p-2">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="font-medium text-slate-200">{heading} {kindLabel(operation.operation_kind)} <span className="font-mono text-slate-500">{operation.operation_id.slice(0, 8)}</span></span>
                <a className="text-cyan-300 hover:underline" href={actions.operationHref(operation.operation_id)}>Open plan</a>
            </div>
            <ul className="space-y-1">
                {edges.map((edge) => (
                    <EdgeRow key={`${edge.revision_id}:${edge.role}:${edge.position}`} edge={edge} actions={actions} actionable={show === 'outputs'} depth={depth} />
                ))}
            </ul>
        </article>
    );
}

export function RelatedList({ revisionId, actions, depth = 0 }: {
    revisionId: string;
    actions: HistoryRelativeActions;
    depth?: number;
}) {
    const [data, setData] = useState<RevisionRelatives | null>(null);
    const [derived, setDerived] = useState<RelativeOperation[]>([]);
    const [cursor, setCursor] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback((after: string | null, signal?: AbortSignal) => {
        setLoading(true);
        setError(null);
        fetchRevisionRelatives(revisionId, { cursor: after }, signal)
            .then((page) => {
                if (signal?.aborted) return;
                if (!after) setData(page);
                setDerived((current) => after ? [...current, ...page.derived.items] : page.derived.items);
                setCursor(page.derived.next_cursor);
            })
            .catch((e) => { if (!signal?.aborted) setError(e instanceof Error ? e.message : String(e)); })
            .finally(() => { if (!signal?.aborted) setLoading(false); });
    }, [revisionId]);

    useEffect(() => {
        const controller = new AbortController();
        setData(null);
        setDerived([]);
        setCursor(null);
        load(null, controller.signal);
        return () => controller.abort();
    }, [load]);

    return (
        <div className="mt-2 space-y-2 border-l border-slate-700 pl-2" data-relatives-depth={depth}>
            {loading && !data && <p className="text-xs text-cyan-200">Loading related molecules…</p>}
            {error && <p role="alert" className="text-xs text-red-300">Unable to load related molecules: {error}</p>}
            {data && data.sources.length === 0 && derived.length === 0 && !loading && (
                <p className="text-xs text-slate-500">No parents or derived molecules recorded.</p>
            )}
            {data?.sources.map((operation) => (
                <OperationBlock key={`s-${operation.operation_id}`} operation={operation} show="inputs" heading="Made by" actions={actions} depth={depth} />
            ))}
            {derived.map((operation) => (
                <OperationBlock key={`d-${operation.operation_id}`} operation={operation} show="outputs" heading="Used in" actions={actions} depth={depth} />
            ))}
            {cursor && (
                <button type="button" disabled={loading} className="text-xs text-cyan-300" onClick={() => load(cursor)}>
                    {loading ? 'Loading…' : 'Load more'}
                </button>
            )}
        </div>
    );
}

export function HistoryRelatives({ revisionId, actions }: { revisionId: string | null; actions: HistoryRelativeActions }) {
    return (
        <section className="space-y-2 rounded-xl border border-slate-700 bg-slate-900/50 p-3" aria-label="Related molecules">
            <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Related molecules</div>
            {revisionId
                ? <RelatedList revisionId={revisionId} actions={actions} />
                : <p className="text-xs text-slate-500">Save this workspace to see parents and derived molecules.</p>}
        </section>
    );
}
