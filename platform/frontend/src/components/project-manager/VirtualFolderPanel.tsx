import { useState } from 'react';
import type { JsonObject, ProjectManagerReadModel } from '../../lib/projectManager';
import { displayLabel, valueText } from './projectManagerState';

export type FolderKind = 'plans' | 'runs' | 'results' | 'datasets' | 'notes' | 'decisions' | 'activity' | 'lineage';

interface VirtualFolderPanelProps {
    folder: FolderKind | null;
    summary: ProjectManagerReadModel;
    onLoadMore: (folder: FolderKind) => void;
    onSelectRecord: (folder: FolderKind, item: JsonObject) => void;
    loading?: boolean;
}

function text(item: JsonObject, key: string): string | null {
    const value = item[key];
    return typeof value === 'string' && value ? value : null;
}

function RecordList({ items, empty, onSelect }: { items: JsonObject[]; empty: string; onSelect: (item: JsonObject) => void }) {
    if (!items.length) return <p className="text-xs text-content-muted">{empty}</p>;
    return (
        <ul className="space-y-2">
            {items.map((item, index) => {
                const id = text(item, 'id') ?? text(item, 'resource_id') ?? text(item, 'receipt_id') ?? `${index}`;
                const title = text(item, 'record_kind') ?? text(item, 'event_type') ?? text(item, 'edge_mode') ?? text(item, 'entity_kind') ?? text(item, 'label') ?? id;
                const detail = text(item, 'body') ?? text(item, 'entity_id') ?? text(item, 'edge_key') ?? text(item, 'unavailable_reason');
                return (
                    <li key={id} className="rounded-lg border border-border-primary bg-surface">
                        <button type="button" onClick={() => onSelect(item)} className="w-full p-3 text-left outline-none focus:ring-2 focus:ring-accent">
                            <span className="block text-xs font-semibold text-content">{displayLabel(title)}</span>
                            {detail ? <span className="mt-1 block text-[11px] text-content-secondary">{detail}</span> : null}
                            <span className="mt-1 block text-[9px] text-content-muted">{valueText(id)}</span>
                        </button>
                    </li>
                );
            })}
        </ul>
    );
}

const activityLabels: Record<string, [string, string]> = {
    'molbio_ngs.domain_state.revision_saved': ['Domain state saved', 'A new domain state revision was saved.'],
    source_attached: ['Source attached', 'A source record was attached to this project context.'],
    run_completed: ['Run completed', 'A workflow run reported completion.'],
    domain_connector_event_applied: ['Domain connection updated', 'An event from the domain connector was applied.'],
};

function ActivityEvent({ item }: { item: JsonObject }) {
    const [open, setOpen] = useState(false);
    const payload = item.payload && typeof item.payload === 'object' && !Array.isArray(item.payload) ? item.payload as JsonObject : {};
    const eventType = text(payload, 'event_type') ?? text(item, 'event_type') ?? 'unknown_event';
    const [title, description] = activityLabels[eventType] ?? [displayLabel(eventType.replaceAll('.', ' ')), 'A project activity event was recorded. Expand Technical details for the exact event data.'];
    const timestamp = text(item, 'created_at');
    const generation = payload.stream_generation ?? payload.generation ?? item.generation;
    const generationLabel = payload.stream_generation !== undefined ? 'Stream generation' : 'Generation';
    const stream = payload.stream_key ?? payload.event_stream ?? payload.stream ?? item.event_stream ?? item.stream;
    return <li className="border-b border-border-primary py-2 last:border-0">
        <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)} className="w-full rounded-lg p-2 text-left focus:ring-2 focus:ring-accent">
            <span className="flex justify-between gap-3 text-sm font-semibold text-content">{title}<span aria-hidden="true">{open ? '−' : '+'}</span></span>
            <time dateTime={timestamp ?? undefined} className="mt-1 block text-xs text-content-muted">{timestamp ? (Number.isNaN(Date.parse(timestamp)) ? timestamp : new Date(timestamp).toLocaleString()) : 'Timestamp unavailable'}</time>
        </button>
        {open && <div className="space-y-3 px-2 pb-3 text-sm text-content-secondary">
            <p>{text(payload, 'description') ?? text(item, 'description') ?? description}</p>
            {(generation !== undefined || stream !== undefined) && <p>{stream !== undefined && `Event stream: ${valueText(stream)}`}{generation !== undefined && ` · ${generationLabel} ${valueText(generation)}`}</p>}
            <details><summary className="cursor-pointer text-xs font-semibold">Technical details</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(item, null, 2)}</pre></details>
        </div>}
    </li>;
}

export function VirtualFolderPanel({ folder, summary, onLoadMore, onSelectRecord, loading = false }: VirtualFolderPanelProps) {
    if (!folder) return null;

    let items: JsonObject[] = [];
    let nextCursor: string | null = null;
    if (folder === 'activity') {
        items = summary.pagination.activity.items as unknown as JsonObject[];
        nextCursor = summary.pagination.activity.next_cursor;
    } else if (folder === 'notes') {
        items = summary.pagination.notes.items;
        nextCursor = summary.pagination.notes.next_cursor;
    } else if (folder === 'decisions') {
        items = summary.pagination.decisions.items;
        nextCursor = summary.pagination.decisions.next_cursor;
    } else if (folder === 'results') {
        items = summary.pagination.results.items;
        nextCursor = summary.pagination.results.next_cursor;
    } else if (folder === 'lineage') {
        items = summary.pagination.lineage.items;
        nextCursor = summary.pagination.lineage.next_cursor;
    } else if (folder === 'plans') {
        items = summary.map.nodes
            .filter((node) => node.node_type === 'workflow')
            .map((node) => ({ id: node.node_key, label: node.label, normalized_state: node.normalized_state }));
        nextCursor = summary.map.next_cursor;
    } else if (folder === 'datasets') {
        items = summary.pagination.datasets.items;
        nextCursor = summary.pagination.datasets.next_cursor;
    } else if (folder === 'runs') {
        items = summary.runs.items.map((run) => ({ id: run.run_id, label: run.target_label, canonical_state: run.canonical_state }));
        nextCursor = summary.runs.next_cursor;
    }

    return (
        <section aria-label={`${displayLabel(folder)} bounded records`} className="rounded-xl border border-border-primary bg-surface-secondary p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                    <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-accent">Bounded server records</p>
                    <h2 className="mt-1 text-sm font-semibold text-content">{displayLabel(folder)}</h2>
                </div>
                <span className="text-[10px] text-content-muted">{items.length} loaded</span>
            </div>
            {folder === 'activity' && items.length ? <ul>{items.map((item) => <ActivityEvent key={text(item, 'id') ?? JSON.stringify(item)} item={item} />)}</ul> : <RecordList items={items} empty={`No ${folder} records are available in this bounded context.`} onSelect={(item) => onSelectRecord(folder, item)} />}
            {nextCursor ? (
                <button type="button" onClick={() => onLoadMore(folder)} disabled={loading} className="mt-3 rounded-lg border border-accent px-3 py-2 text-xs font-semibold text-accent disabled:opacity-50">
                    {loading ? 'Loading…' : `Load more ${folder}`}
                </button>
            ) : null}
        </section>
    );
}
