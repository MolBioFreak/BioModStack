import { useEffect, useMemo, useRef, useState } from 'react';
import type { HighlightedRegion, SelectionInfo, SequenceData } from '../types';
import type {
    RestrictionAnalysisBatch,
    RestrictionCatalogReceipt,
    RestrictionDigestEnd,
    RestrictionDigestSimulation,
    RestrictionProductReleaseReceipt,
    RestrictionRecord,
} from '../../../lib/restrictionAnalysis';

type CutFilter = 'all' | 'zero' | 'unique' | 'double' | 'three_plus' | 'selection';
type GroupFilter = 'all' | 'digest' | 'nicking' | 'recognition_only' | 'commercial';
export type QuickMapGroup = 'unique' | 'double' | 'three_plus' | 'nicking' | 'type_iis';

interface DigestPanelProps {
    mobile?: boolean;
    compactLandscape?: boolean;
    sequenceData: SequenceData;
    sequenceId: string | null;
    selection?: SelectionInfo | null;
    onHighlight: (regions: HighlightedRegion[]) => void;
    selectedEnzymes?: string[];
    onEnzymesChange?: (enzymes: string[]) => void;
    onMapVisibilityRequest?: () => void;
    catalog: RestrictionCatalogReceipt | null;
    productEvidence?: RestrictionProductReleaseReceipt | null;
    catalogRecords: RestrictionRecord[];
    analysis: RestrictionAnalysisBatch | null;
    authorityLoading: boolean;
    authorityError: string | null;
    digestSimulation: RestrictionDigestSimulation | null;
    digestLoading: boolean;
    digestError: string | null;
    onDigestSelectionChange: (enzymeIds: string[]) => void;
    onSimulateDigest: (enzymeIds: string[]) => void;
}

interface EnzymeCutData {
    record: RestrictionRecord;
    summary: RestrictionAnalysisBatch['analysis']['enzyme_summaries'][number] | null;
    cuts: number[];
    selectionCuts: number;
}

export function getQuickMapEnzymeNames(enzymes: EnzymeCutData[], group: QuickMapGroup): string[] {
    return enzymes.filter(({ record, summary }) => {
        if (!summary) return false;
        const sites = summary.recognition_site_count_definite + summary.recognition_site_count_possible;
        if (sites === 0) return false;
        if (group === 'unique') return summary.double_strand_break_count === 1;
        if (group === 'double') return summary.double_strand_break_count === 2;
        if (group === 'three_plus') return summary.double_strand_break_count >= 3;
        if (group === 'nicking') return summary.nick_count > 0;
        return record.golden_gate_compatible;
    }).map(({ record }) => record.enzyme_id);
}
export function mergeMappedEnzymes(current: string[], additions: string[]): string[] { return Array.from(new Set([...current, ...additions])); }
export interface QuickMapSelectionState { selectedEnzymes: string[]; activeGroups: Set<QuickMapGroup> }
export function toggleQuickMapGroupSelection(state: QuickMapSelectionState, manuallyMappedEnzymes: Set<string>, enzymes: EnzymeCutData[], group: QuickMapGroup): QuickMapSelectionState {
    const activeGroups = new Set(state.activeGroups);
    const groupIds = getQuickMapEnzymeNames(enzymes, group);
    if (!activeGroups.has(group)) return { activeGroups: new Set([...activeGroups, group]), selectedEnzymes: mergeMappedEnzymes(state.selectedEnzymes, groupIds) };
    activeGroups.delete(group);
    const retained = new Set([...activeGroups].flatMap((entry) => getQuickMapEnzymeNames(enzymes, entry)));
    const removed = new Set(groupIds);
    return { activeGroups, selectedEnzymes: state.selectedEnzymes.filter((id) => !removed.has(id) || manuallyMappedEnzymes.has(id) || retained.has(id)) };
}

function endLabel(end: RestrictionDigestEnd): string {
    if (end.kind === 'five_prime_overhang') return `5′ ${end.overhang_sequence_5to3} overhang`;
    if (end.kind === 'three_prime_overhang') return `3′ ${end.overhang_sequence_5to3} overhang`;
    if (end.kind === 'blunt') return 'blunt end';
    if (end.kind === 'natural') return 'natural end';
    return 'uncut circular end';
}
function inSelection(position: number, selection: SelectionInfo | null | undefined, length: number, circular: boolean): boolean {
    if (!selection || selection.start === selection.end) return false;
    const start = Math.max(0, Math.min(length, selection.start));
    const end = Math.max(0, Math.min(length, selection.end));
    return circular && start > end ? position >= start || position < end : position >= Math.min(start, end) && position < Math.max(start, end);
}

const CUT_FILTERS: Array<[CutFilter, string]> = [['all','All'],['unique','1x'],['double','2x'],['three_plus','3x+'],['zero','0x'],['selection','In Selection']];
const GROUP_FILTERS: Array<[GroupFilter, string]> = [['all','All Types'],['digest','Digest-ready'],['nicking','Nicking'],['recognition_only','Recognition only'],['commercial','Commercial reported']];
const QUICK: Array<[QuickMapGroup, string]> = [['unique','1x'],['double','2x'],['three_plus','3x+'],['nicking','Nicking'],['type_iis','Golden Gate compatible']];

export function DigestPanel({ mobile = false, compactLandscape = false, sequenceData, selection, onHighlight, selectedEnzymes = [], onEnzymesChange, onMapVisibilityRequest, catalog, productEvidence = null, catalogRecords, analysis, authorityLoading, authorityError, digestSimulation, digestLoading, digestError, onDigestSelectionChange, onSimulateDigest }: DigestPanelProps) {
    const [digestEnzymes, setDigestEnzymes] = useState<string[]>([]);
    const [searchQuery, setSearchQuery] = useState('');
    const [cutFilter, setCutFilter] = useState<CutFilter>('unique');
    const [groupFilter, setGroupFilter] = useState<GroupFilter>('all');
    const [activeQuickMapGroups, setActiveQuickMapGroups] = useState<Set<QuickMapGroup>>(new Set());
    const manualRef = useRef(new Set(selectedEnzymes));

    useEffect(() => {
        setDigestEnzymes([]);
        onDigestSelectionChange([]);
        setActiveQuickMapGroups(new Set());
        manualRef.current = new Set();
        onHighlight([]);
    }, [analysis?.authority_key, catalog?.catalog_sha256, onDigestSelectionChange, onHighlight]);

    const bySummary = useMemo(() => new Map(analysis?.analysis.enzyme_summaries.map((row) => [row.enzyme_id, row]) ?? []), [analysis]);
    const enzymeData = useMemo<EnzymeCutData[]>(() => catalogRecords.map((record) => {
        const summary = bySummary.get(record.enzyme_id);
        const occurrences = analysis?.analysis.occurrences.filter((row) => row.enzyme_id === record.enzyme_id) ?? [];
        return { record, summary: summary ?? null, cuts: occurrences.map((row) => row.site_start), selectionCuts: occurrences.filter((row) => inSelection(row.site_start, selection, sequenceData.sequence.length, sequenceData.circular)).length };
    }), [analysis, bySummary, catalogRecords, selection, sequenceData.circular, sequenceData.sequence.length]);

    const filtered = useMemo(() => enzymeData.filter(({ record, summary, selectionCuts }) => {
        const query = searchQuery.trim().toLowerCase();
        if (query && ![record.enzyme_id, record.canonical_name, record.recognition.site_iupac, ...record.aliases].some((value) => value.toLowerCase().includes(query))) return false;
        if (groupFilter === 'digest' && record.analysis_capability !== 'digest_simulation') return false;
        if (groupFilter === 'nicking' && (summary?.nick_count ?? 0) === 0) return false;
        if (groupFilter === 'recognition_only' && record.analysis_capability !== 'recognition_only') return false;
        if (groupFilter === 'commercial' && !record.supplier_provenance.reported_commercial) return false;
        if (!summary) return groupFilter === 'recognition_only' || cutFilter === 'all';
        const count = summary.double_strand_break_count;
        if (cutFilter === 'zero') return count === 0;
        if (cutFilter === 'unique') return count === 1;
        if (cutFilter === 'double') return count === 2;
        if (cutFilter === 'three_plus') return count >= 3;
        if (cutFilter === 'selection') return selectionCuts > 0;
        return true;
    }), [cutFilter, enzymeData, groupFilter, searchQuery]);

    const updateDigest = (next: string[]) => { setDigestEnzymes(next); onDigestSelectionChange(next); };
    const toggleDigest = (id: string) => updateDigest(digestEnzymes.includes(id) ? digestEnzymes.filter((entry) => entry !== id) : [...digestEnzymes, id]);
    const toggleMap = (id: string) => {
        const next = selectedEnzymes.includes(id) ? selectedEnzymes.filter((entry) => entry !== id) : [...selectedEnzymes, id];
        manualRef.current = new Set(next);
        onEnzymesChange?.(next);
        if (!selectedEnzymes.includes(id)) onMapVisibilityRequest?.();
    };
    const toggleQuick = (group: QuickMapGroup) => {
        const next = toggleQuickMapGroupSelection({ selectedEnzymes, activeGroups: activeQuickMapGroups }, manualRef.current, enzymeData, group);
        setActiveQuickMapGroups(next.activeGroups);
        onEnzymesChange?.(next.selectedEnzymes);
        if (!activeQuickMapGroups.has(group)) onMapVisibilityRequest?.();
    };

    useEffect(() => {
        const regions = digestSimulation?.fragments.flatMap((fragment, index) => fragment.source_segments.map(([start, end]) => ({ start, end, color: index % 2 ? '#34d399' : '#38bdf8', label: `Fragment ${fragment.fragment_index + 1} (${fragment.reference_span_bp} bp)` }))) ?? [];
        onHighlight(regions);
        return () => onHighlight([]);
    }, [digestSimulation, onHighlight]);

    return <div data-digest-layout={mobile ? 'mobile' : 'desktop'} data-digest-compact-landscape={mobile && compactLandscape ? 'true' : undefined} className={mobile ? 'digest-panel flex h-full min-h-0 flex-col overflow-hidden bg-slate-900 text-sm' : 'digest-panel space-y-4 p-3 text-sm'}>
        {!compactLandscape && <div className={mobile ? 'px-3 pt-3' : ''}><h4 className="font-semibold text-slate-200">Restriction Analysis</h4><p className="text-xs text-slate-500">Backend catalog and exact cleavage authority</p></div>}
        {(authorityError || digestError) && <div role="alert" className="mx-3 rounded border border-red-800 bg-red-900/40 p-2 text-red-200">{authorityError || digestError}</div>}
        {authorityLoading && <div className="px-3 text-xs text-slate-400">Loading exact restriction authority…</div>}
        {productEvidence && <div data-product-evidence-state="unavailable" className="mx-3 rounded border border-amber-800/70 bg-amber-950/30 p-2 text-xs text-amber-200"><strong>Supplier product evidence unavailable</strong><span className="ml-1 text-amber-300/80">Written redistribution permission is unavailable; reaction-aware activity is disabled.</span></div>}
        {analysis && <div data-restriction-counts="true" className="grid grid-cols-3 gap-2 px-3 text-xs text-slate-300">
            <div><strong>{analysis.analysis.counts.recognition_site_count_definite}</strong> recognition sites</div>
            <div><strong>{analysis.analysis.counts.double_strand_break_count}</strong> DSBs</div>
            <div><strong>{analysis.analysis.counts.nick_count}</strong> nicks</div>
        </div>}
        {analysis && <details className="mx-3 text-xs text-slate-400"><summary>{analysis.chunks.length} exact analysis authority chunk{analysis.chunks.length === 1 ? '' : 's'}</summary>{analysis.chunks.map((chunk, index) => <code key={chunk.result_sha256} data-restriction-chunk-result-sha256={chunk.result_sha256} className="block break-all">Chunk {index + 1}: sha256:{chunk.result_sha256}</code>)}</details>}
        <div data-digest-mobile-sticky-search={mobile ? 'true' : undefined} className="space-y-2 px-3">
            <input value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="Search enzyme or recognition site…" data-digest-mobile-touch-target={mobile ? 'true' : undefined} className={`w-full rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 ${mobile ? 'min-h-12 min-w-12' : ''}`} />
            {!compactLandscape && <><div data-digest-mobile-filter-bar={mobile ? 'true' : undefined} className="flex gap-1 overflow-x-auto">{CUT_FILTERS.map(([value,label]) => <button key={value} onClick={() => setCutFilter(value)} className={`${mobile ? 'min-h-12 min-w-12' : ''} rounded border px-2 ${cutFilter === value ? 'bg-cyan-700' : 'bg-slate-800'}`}>{label}</button>)}</div><div data-digest-mobile-filter-bar={mobile ? 'true' : undefined} className="flex gap-1 overflow-x-auto">{GROUP_FILTERS.map(([value,label]) => <button key={value} onClick={() => setGroupFilter(value)} className={`${mobile ? 'min-h-12 min-w-12' : ''} rounded border px-2 ${groupFilter === value ? 'bg-cyan-700' : 'bg-slate-800'}`}>{label}</button>)}</div><div data-digest-quick-map="true" className="flex gap-1 overflow-x-auto">{QUICK.map(([value,label]) => <button key={value} onClick={() => toggleQuick(value)} className={`${mobile ? 'min-h-12 min-w-12' : ''} rounded border px-2`}>{label}</button>)}</div></>}
        </div>
        <div data-digest-scroll-region={mobile ? 'enzymes' : undefined} className={mobile ? 'min-h-0 flex-1 space-y-2 overflow-y-auto p-3' : 'max-h-80 space-y-2 overflow-y-auto'}>
            {filtered.map(({ record, summary }) => { const digestable = record.analysis_capability === 'digest_simulation' && summary !== null; return <div key={record.enzyme_id} data-enzyme-name={record.enzyme_id} className="rounded border border-slate-700 bg-slate-800 p-2"><div className="flex justify-between gap-2"><div><strong>{record.canonical_name}</strong><div className="font-mono text-xs text-slate-400">{record.recognition.site_iupac}</div>{summary ? <div className="text-xs">{summary.recognition_site_count_definite} definite + {summary.recognition_site_count_possible} possible · {summary.double_strand_break_count} DSB · {summary.nick_count} nick</div> : <div className="text-xs text-amber-300">geometry unavailable · not analyzed</div>}{!digestable && summary && <span className="text-xs text-amber-300">{record.analysis_capability === 'nicking_analysis' ? 'map-only nickase' : 'geometry unavailable'}</span>}</div><div className="flex gap-1"><button disabled={!summary} onClick={() => toggleMap(record.enzyme_id)} data-digest-mobile-touch-target={mobile ? 'true' : undefined} className={`${mobile ? 'min-h-12 min-w-20' : ''} rounded border px-2 disabled:opacity-40`}>Map</button><button disabled={!digestable} onClick={() => toggleDigest(record.enzyme_id)} data-digest-mobile-touch-target={mobile ? 'true' : undefined} className={`${mobile ? 'min-h-12 min-w-20' : ''} rounded border px-2 disabled:opacity-40`}>{mobile ? (digestEnzymes.includes(record.enzyme_id) ? 'Remove' : 'Add') : 'Digest'}</button></div></div></div>; })}
        </div>
        <div data-digest-mobile-footer={mobile ? 'true' : undefined} data-digest-compact-landscape={mobile && compactLandscape ? 'true' : undefined} className="border-t border-slate-700 bg-slate-950 p-3">
            <div className="mb-2 flex gap-1 overflow-x-auto">{digestEnzymes.map((id) => <button key={id} onClick={() => toggleDigest(id)} className="rounded border border-amber-500 px-2">{id} ×</button>)}</div>
            <button onClick={() => onSimulateDigest(digestEnzymes)} disabled={digestLoading || digestEnzymes.length === 0} data-digest-mobile-run={mobile ? 'true' : undefined} data-digest-mobile-touch-target={mobile ? 'true' : undefined} className={`w-full rounded bg-cyan-600 py-2 disabled:bg-slate-600 ${mobile ? 'min-h-12 min-w-12' : ''}`}>{digestLoading ? 'Digesting…' : `Run Digest (${digestEnzymes.length} enzyme${digestEnzymes.length === 1 ? '' : 's'})`}</button>
            {digestSimulation && <div data-digest-mobile-result={mobile ? 'true' : undefined} className="mt-2 space-y-1 rounded border border-cyan-700 p-2"><div>{digestSimulation.fragments.length} exact sequence fragments</div>{digestSimulation.fragments.map((fragment) => <div key={fragment.fragment_index} data-fragment-index={fragment.fragment_index} className="rounded bg-slate-800 p-2 text-xs"><strong>#{fragment.fragment_index + 1} · {fragment.reference_span_bp} bp</strong><div>Left: {endLabel(fragment.left_end)}</div><div>Right: {endLabel(fragment.right_end)}</div></div>)}</div>}
        </div>
    </div>;
}
