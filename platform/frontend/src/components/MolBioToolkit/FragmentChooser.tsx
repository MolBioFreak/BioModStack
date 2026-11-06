import { useCallback, useEffect, useRef, useState } from 'react';
import {
    fetchChooser,
    fetchMolecularRevision,
    type AssemblyFragmentEnd,
    type AssemblyFragmentInput,
    type ChooserEnd,
    type ChooserItem,
    type MolecularRevision,
} from '../../lib/api';

function endFromChooser(end: ChooserEnd | null | undefined): AssemblyFragmentEnd | null | undefined {
    if (!end) return undefined;
    if (end.kind === 'natural' || (end.kind as string) === 'no_cut_circular') return null;
    const stated: 'phosphorylated' | 'unphosphorylated' | undefined =
        end.five_prime_phosphate === 'phosphorylated' || end.five_prime_phosphate === 'unphosphorylated'
            ? end.five_prime_phosphate : end.enzyme_created ? 'phosphorylated' : undefined;
    const phosphate = stated ? { five_prime_phosphate: stated } : {};
    if (end.kind === 'blunt') return { type: 'blunt', overhang: '', ...phosphate };
    return {
        type: end.kind === 'five_prime_overhang' ? 'sticky_5' : 'sticky_3',
        overhang: end.overhang_sequence_5to3 ?? '',
        protruding_strand: end.protruding_strand ?? null,
        ...phosphate,
    };
}

/**
 * Pins (document, revision_number) and the full source; ends come from the item when the
 * API supplies them. The chooser list does not carry bases: pass them in, or use
 * `loadChooserFragment` which reads them from the immutable revision.
 */
export function fragmentFromChooserItem(item: ChooserItem, sequence = ''): AssemblyFragmentInput {
    const left = endFromChooser(item.left_end);
    const right = endFromChooser(item.right_end);
    return {
        id: `chooser-${item.revision_id}-${Math.random().toString(36).slice(2, 7)}`,
        name: item.name,
        sequence,
        orientation: 'forward',
        circular: item.topology === 'circular',
        source_sequence_id: item.document_id,
        source_name: item.name,
        source_revision: item.revision_number,
        ...(left !== undefined ? { left_end: left } : {}),
        ...(right !== undefined ? { right_end: right } : {}),
        // The revision id lets End operations read the saved molecule (features included); ends that came from the
        // saved molecule are marked so a mode switch does not replace them with the mode's defaults.
        metadata: { revision_id: item.revision_id, ...(left || right ? { retained_ends: true } : {}) },
    };
}

/** Retained physical ends: `physical_ends` (end operations/assemblies) or digest-fragment `geometry`. */
function endsFromProvenance(provenance: Record<string, unknown> | null | undefined): Pick<ChooserItem, 'left_end' | 'right_end'> {
    const p = provenance ?? {};
    const source = (p.physical_ends && typeof p.physical_ends === 'object')
        ? p.physical_ends as Record<string, unknown>
        : (p.schema === 'bms.molbio.restriction-digest-fragment-provenance.v1' && p.geometry && typeof p.geometry === 'object')
            ? p.geometry as Record<string, unknown>
            : null;
    if (!source) return {};
    return { left_end: source.left_end as ChooserEnd | undefined, right_end: source.right_end as ChooserEnd | undefined };
}

/** Fragment pinned to an exact saved revision, bases read from that revision's snapshot. */
export function fragmentFromRevision(revision: MolecularRevision, item?: Partial<ChooserItem>): AssemblyFragmentInput {
    const snapshot = revision.snapshot as { sequence?: unknown; is_circular?: unknown };
    return fragmentFromChooserItem({
        document_id: revision.document_id,
        revision_id: revision.revision_id,
        revision_number: revision.revision_number,
        sequence_id: revision.sequence_id,
        name: revision.document_name,
        topology: snapshot.is_circular === true ? 'circular' : 'linear',
        length: revision.content_length,
        is_current: revision.relation === 'current',
        origin: 'retained',
        produced_by: null,
        ...endsFromProvenance(revision.provenance),
        ...item,
    }, typeof snapshot.sequence === 'string' ? snapshot.sequence : '');
}

export async function loadChooserFragment(item: ChooserItem): Promise<AssemblyFragmentInput> {
    const revision = await fetchMolecularRevision(item.document_id, item.revision_id);
    return fragmentFromRevision(revision, item.left_end || item.right_end ? { left_end: item.left_end, right_end: item.right_end } : undefined);
}

export function chooserOriginLabel(item: ChooserItem): string {
    if (!item.produced_by) return 'saved molecule';
    return `${item.produced_by.operation_kind.replace(/_/g, ' ')} ${item.produced_by.operation_id.slice(0, 8)}`;
}

export function FragmentChooser({ onPick, kinds, label = 'Add from saved molecules' }: {
    onPick: (item: ChooserItem) => void;
    kinds?: string[];
    label?: string;
}) {
    const [query, setQuery] = useState('');
    const [open, setOpen] = useState(false);
    const [items, setItems] = useState<ChooserItem[]>([]);
    const [cursor, setCursor] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const generation = useRef(0);
    const kindsKey = (kinds ?? []).join(',');

    const load = useCallback((after: string | null, q: string) => {
        const mine = ++generation.current;
        setLoading(true);
        setError(null);
        fetchChooser({ q, kinds: kindsKey ? kindsKey.split(',') : undefined, cursor: after })
            .then((page) => {
                if (mine !== generation.current) return;
                setItems((current) => after ? [...current, ...page.items] : page.items);
                setCursor(page.next_cursor);
            })
            .catch((e) => { if (mine === generation.current) setError(e instanceof Error ? e.message : String(e)); })
            .finally(() => { if (mine === generation.current) setLoading(false); });
    }, [kindsKey]);

    // Loads only once the list is opened (and again when the search text changes).
    useEffect(() => {
        if (!open) return undefined;
        const timer = setTimeout(() => load(null, query.trim()), query ? 250 : 0);
        return () => clearTimeout(timer);
    }, [open, query, load]);

    useEffect(() => () => { generation.current += 1; }, []);

    if (!open) {
        return <button type="button" className="rounded border border-slate-600 px-2 py-1 text-xs text-slate-200" onClick={() => setOpen(true)}>{label}</button>;
    }
    return (
        <section aria-label="Fragment chooser" className="space-y-2 rounded border border-slate-700 bg-slate-900/60 p-2 text-xs">
            <div className="flex gap-2">
                <input
                    aria-label="Search saved molecules"
                    className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200"
                    placeholder="Search by name"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                />
                <button type="button" className="px-2 text-slate-400" onClick={() => setOpen(false)}>Close</button>
            </div>
            {error && <p role="alert" className="text-red-300">Unable to load molecules: {error}</p>}
            <ul className="max-h-60 space-y-1 overflow-y-auto">
                {items.map((item) => (
                    <li key={`${item.document_id}:${item.revision_id}`}>
                        <button
                            type="button"
                            className="flex w-full items-center justify-between gap-2 rounded border border-slate-800 bg-slate-950/70 px-2 py-1 text-left hover:border-cyan-700"
                            onClick={() => onPick(item)}
                        >
                            <span className="min-w-0 truncate text-slate-200">{item.name} <span className="text-slate-500">r{item.revision_number}</span></span>
                            <span className="shrink-0 text-slate-400">{item.length.toLocaleString()} bp · {item.topology} · {chooserOriginLabel(item)}</span>
                        </button>
                    </li>
                ))}
            </ul>
            {!loading && !error && items.length === 0 && <p className="text-slate-500">No matching molecules.</p>}
            {loading && <p className="text-cyan-200">Loading…</p>}
            {cursor && !loading && <button type="button" className="text-cyan-300" onClick={() => load(cursor, query.trim())}>Load more</button>}
        </section>
    );
}
