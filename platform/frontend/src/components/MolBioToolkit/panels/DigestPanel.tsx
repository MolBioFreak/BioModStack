import { digestFragmentsForAssembly } from '../../../lib/digestAssemblyTransfer';
import type { AssemblyFragmentInput } from '../../../lib/api';
import { useEffect, useMemo, useRef, useState } from 'react';
import { saveRestrictionDigest, restrictionRecognitionSite } from '../../../lib/restrictionAnalysis';
import { restrictionSpecialCase } from '../../../lib/restrictionSpecialCases';
import type { HighlightedRegion, SelectionInfo, SequenceData } from '../types';
import type {
    RestrictionAnalysisBatch,
    RestrictionCatalogReceipt,
    RestrictionDigestEnd,
    RestrictionDigestSimulation,
    RestrictionProductReleaseReceipt,
    RestrictionRecord,
    RestrictionCatalogSummary,
} from '../../../lib/restrictionAnalysis';

type CutFilter = 'all' | 'zero' | 'unique' | 'double' | 'three_plus' | 'selection';
type GroupFilter = 'all' | 'digest' | 'nicking' | 'recognition_only' | 'special';
export type QuickMapGroup = 'unique' | 'double' | 'three_plus' | 'nicking' | 'type_iis';

interface DigestPanelProps {
    onUseInAssembly?: (fragments: AssemblyFragmentInput[]) => void;
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
    catalogRecords: Array<RestrictionRecord | RestrictionCatalogSummary>;
    analysis: RestrictionAnalysisBatch | null;
    authorityLoading: boolean;
    authorityError: string | null;
    digestSimulation: RestrictionDigestSimulation | null;
    digestLoading: boolean;
    digestError: string | null;
    onDigestSelectionChange: (enzymeIds: string[]) => void;
    onSimulateDigest: (enzymeIds: string[]) => void;
    onAnalyzeAll?: () => Promise<RestrictionAnalysisBatch | undefined> | void;
    onReadEnzymeDetails?: (enzymeIds: string[]) => Promise<RestrictionRecord[]>;
}

interface EnzymeCutData {
    record: RestrictionRecord | RestrictionCatalogSummary;
    summary: RestrictionAnalysisBatch['analysis']['enzyme_summaries'][number] | null;
    cuts: number[];
    selectionCuts: number;
}

export function getQuickMapEnzymeNames(enzymes: Pick<EnzymeCutData, 'record' | 'summary'>[], group: QuickMapGroup): string[] {
    return enzymes.filter(({ record, summary }) => {
        if (!summary || restrictionSpecialCase(record.enzyme_id)) return false;
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

function indexRestrictionAnalysis(analysis: RestrictionAnalysisBatch | null) {
    const summaries = new Map(analysis?.analysis.enzyme_summaries.map(row => [row.enzyme_id, row]) ?? []);
    const occurrences = new Map<string, RestrictionAnalysisBatch['analysis']['occurrences']>();
    for (const occurrence of analysis?.analysis.occurrences ?? []) {
        const rows = occurrences.get(occurrence.enzyme_id) ?? [];
        rows.push(occurrence);
        occurrences.set(occurrence.enzyme_id, rows);
    }
    return { summaries, occurrences };
}

function buildEnzymeRows(records: DigestPanelProps['catalogRecords'], index: ReturnType<typeof indexRestrictionAnalysis>, selection: SelectionInfo | null | undefined, sequence: SequenceData): EnzymeCutData[] {
    return records.map(record => {
        const occurrences = index.occurrences.get(record.enzyme_id) ?? [];
        return { record, summary: index.summaries.get(record.enzyme_id) ?? null, cuts: occurrences.map(row => row.site_start), selectionCuts: occurrences.filter(row => inSelection(row.site_start, selection, sequence.sequence.length, sequence.circular)).length };
    });
}

function filterEnzymeRows(rows: EnzymeCutData[], searchQuery: string, groupFilter: GroupFilter, cutFilter: CutFilter) {
    const query = searchQuery.trim().toLowerCase();
    return rows.filter(({ record, summary, selectionCuts }) => {
        if (Boolean(restrictionSpecialCase(record.enzyme_id)) !== (groupFilter === 'special')) return false;
        if (query && ![record.enzyme_id, record.canonical_name, restrictionRecognitionSite(record), ...record.aliases].some(value => value.toLowerCase().includes(query))) return false;
        if (groupFilter === 'special') return true; // Motif counts are not ordinary cut-count filters.
        if (groupFilter === 'digest' && record.analysis_capability !== 'digest_simulation') return false;
        if (groupFilter === 'nicking' && (summary?.nick_count ?? 0) === 0) return false;
        if (groupFilter === 'recognition_only' && record.analysis_capability !== 'recognition_only') return false;
        if (!summary) return groupFilter === 'recognition_only' || cutFilter === 'all';
        const count = summary.double_strand_break_count;
        if (cutFilter === 'zero') return count === 0;
        if (cutFilter === 'unique') return count === 1;
        if (cutFilter === 'double') return count === 2;
        if (cutFilter === 'three_plus') return count >= 3;
        if (cutFilter === 'selection') return selectionCuts > 0;
        return true;
    });
}

const CUT_FILTERS: Array<[CutFilter, string]> = [['all','All'],['unique','1x'],['double','2x'],['three_plus','3x+'],['zero','0x'],['selection','In Selection']];
const GROUP_FILTERS: Array<[GroupFilter, string]> = [['all','All Types'],['digest','Digest-ready'],['nicking','Nicking'],['recognition_only','Recognition only']];
const QUICK: Array<[QuickMapGroup, string]> = [['unique','Map all 1x'],['double','Map all 2x'],['three_plus','Map all 3x+'],['nicking','Map nicking'],['type_iis','Map Golden Gate']];

export function DigestPanel({ mobile = false, compactLandscape = false, sequenceData, selection, onHighlight, selectedEnzymes = [], onEnzymesChange, onMapVisibilityRequest, catalog, catalogRecords, analysis, authorityLoading, authorityError, digestSimulation, digestLoading, digestError, onDigestSelectionChange, onSimulateDigest, onAnalyzeAll, onReadEnzymeDetails, productEvidence, sequenceId, onUseInAssembly }: DigestPanelProps) {
    const [assemblySelection, setAssemblySelection] = useState<{simulation: RestrictionDigestSimulation; indices: number[]} | null>(null);
    const assemblyIndices = assemblySelection?.simulation === digestSimulation ? assemblySelection.indices : [];
    const [digestEnzymes, setDigestEnzymes] = useState<string[]>([]);
    const detailKey = JSON.stringify([catalog?.catalog_id, catalog?.catalog_sha256, [...new Set([...selectedEnzymes, ...digestEnzymes])].sort()]);
    const [detailState, setDetailState] = useState<{ key: string; records?: RestrictionRecord[]; error?: string } | null>(null);
    const detailOwner = useRef(detailKey);
    detailOwner.current = detailKey;
    const readDetails = async () => {
        if (!onReadEnzymeDetails) return;
        const key = detailKey;
        setDetailState({ key });
        try {
            const records = await onReadEnzymeDetails([...new Set([...selectedEnzymes, ...digestEnzymes])]);
            if (detailOwner.current === key) setDetailState({ key, records });
        } catch (error) {
            if (detailOwner.current === key) setDetailState({ key, error: error instanceof Error ? error.message : 'Enzyme details unavailable.' });
        }
    };
    const visibleDetails = detailState?.key === detailKey ? detailState : null;
    const [saveState, setSaveState] = useState<{ simulation: RestrictionDigestSimulation; pending?: boolean; operationId?: string; error?: string } | null>(null);
    const saveRequest = useRef<{ simulation: RestrictionDigestSimulation; key: string } | null>(null);
    const saveDigest = async () => {
        if (!digestSimulation) return;
        if (saveRequest.current?.simulation !== digestSimulation) saveRequest.current = { simulation: digestSimulation, key: crypto.randomUUID() };
        setSaveState({ simulation: digestSimulation, pending: true });
        try {
            const operationId = await saveRestrictionDigest(digestSimulation, saveRequest.current.key);
            setSaveState(current => current?.simulation === digestSimulation ? { simulation: digestSimulation, operationId } : current);
        } catch (error) {
            setSaveState(current => current?.simulation === digestSimulation ? { simulation: digestSimulation, error: error instanceof Error ? error.message : 'Digest save failed.' } : current);
        }
    };
    const currentSave = saveState?.simulation === digestSimulation ? saveState : null;
    const [searchQuery, setSearchQuery] = useState('');
    const [displayPage, setDisplayPage] = useState(0);
    const [cutFilter, setCutFilter] = useState<CutFilter>('all');
    const [groupFilter, setGroupFilter] = useState<GroupFilter>('all');
    const [activeQuickMapGroups, setActiveQuickMapGroups] = useState<Set<QuickMapGroup>>(new Set());
    const selectedEnzymesRef = useRef(selectedEnzymes);
    const manualRef = useRef(new Set(selectedEnzymes));
    const recordsRef = useRef(catalogRecords);
    recordsRef.current = catalogRecords;
    const mappingContextRef = useRef({ sequenceId, dna: sequenceData.sequence, circular: sequenceData.circular, catalog: catalog?.catalog_sha256 });

    selectedEnzymesRef.current = selectedEnzymes;
    const activeGroupsRef = useRef(activeQuickMapGroups);
    activeGroupsRef.current = activeQuickMapGroups;
    const mappingGeneration = useRef(0);
    const pendingGroups = useRef(new Map<QuickMapGroup | 'filtered', symbol>());
    const [mappingError, setMappingError] = useState<string | null>(null);
    const [pendingMappingCount, setPendingMappingCount] = useState(0);
    const updateMapped = (ids: string[]) => {
        selectedEnzymesRef.current = ids;
        onEnzymesChange?.(ids);
    };
    const updateGroups = (groups: Set<QuickMapGroup>) => {
        activeGroupsRef.current = groups;
        setActiveQuickMapGroups(groups);
    };
    const cancelPendingMapping = () => {
        mappingGeneration.current++;
        pendingGroups.current.clear();
        setPendingMappingCount(0);
        setMappingError(null);
    };
    useEffect(() => () => { mappingGeneration.current++; }, []);

    useEffect(() => {
        setDigestEnzymes([]);
        onDigestSelectionChange([]);
        const previous = mappingContextRef.current;
        if (previous.sequenceId !== sequenceId || previous.dna !== sequenceData.sequence || previous.circular !== sequenceData.circular
            || (previous.catalog !== undefined && previous.catalog !== catalog?.catalog_sha256)) cancelPendingMapping();
        mappingContextRef.current = { sequenceId, dna: sequenceData.sequence, circular: sequenceData.circular, catalog: catalog?.catalog_sha256 };
        updateGroups(new Set());
        manualRef.current = new Set(selectedEnzymesRef.current);
        onHighlight([]);
    }, [sequenceId, sequenceData.sequence, sequenceData.circular, catalog?.catalog_sha256, onDigestSelectionChange, onHighlight]);

    const indexedAnalysis = useMemo(() => indexRestrictionAnalysis(analysis), [analysis]);
    const enzymeData = useMemo(() => buildEnzymeRows(catalogRecords, indexedAnalysis, selection, sequenceData), [catalogRecords, indexedAnalysis, selection, sequenceData.sequence, sequenceData.circular]);
    const hasFullAnalysis = catalogRecords.length > 0 && catalogRecords.every(record => indexedAnalysis.summaries.has(record.enzyme_id));
    const filtered = useMemo(() => filterEnzymeRows(enzymeData, searchQuery, groupFilter, cutFilter), [cutFilter, enzymeData, groupFilter, searchQuery]);
    const specialCasesVisible = groupFilter === 'special';
    const displayCounts = useMemo(() => {
        const rows = (analysis?.analysis.enzyme_summaries ?? []).filter(row => Boolean(restrictionSpecialCase(row.enzyme_id)) === specialCasesVisible);
        return rows.reduce((counts, row) => ({
            definite: counts.definite + row.recognition_site_count_definite,
            possible: counts.possible + row.recognition_site_count_possible,
            dsb: counts.dsb + row.double_strand_break_count,
            nicks: counts.nicks + row.nick_count,
            enzymes: counts.enzymes + 1,
        }), { definite: 0, possible: 0, dsb: 0, nicks: 0, enzymes: 0 });
    }, [analysis, specialCasesVisible]);
    const filteredMapIds = useMemo(() => filtered.filter(({ summary }) => summary !== null && summary.recognition_site_count_definite + summary.recognition_site_count_possible > 0).map(({ record }) => record.enzyme_id), [filtered]);

    // Paging only bounds mounted rows; special cases remain available by explicit choice.
    const pageCount = Math.max(1, Math.ceil(filtered.length / 50));
    const currentPage = Math.min(displayPage, pageCount - 1);
    const displayed = filtered.slice(currentPage * 50, (currentPage + 1) * 50);
    useEffect(() => { setDisplayPage(0); }, [searchQuery, cutFilter, groupFilter, sequenceData.sequence, sequenceData.circular, catalog?.catalog_sha256, groupFilter]);

    const updateDigest = (next: string[]) => { setDigestEnzymes(next); onDigestSelectionChange(next); };
    const toggleDigest = (id: string) => updateDigest(digestEnzymes.includes(id) ? digestEnzymes.filter((entry) => entry !== id) : [...digestEnzymes, id]);
    const toggleMap = (id: string) => {
        const isSelected = selectedEnzymes.includes(id);
        const next = isSelected ? selectedEnzymes.filter((entry) => entry !== id) : [...selectedEnzymes, id];
        const nextManual = new Set(manualRef.current);
        if (isSelected) nextManual.delete(id);
        else nextManual.add(id);
        manualRef.current = nextManual;
        updateMapped(next);
        if (!selectedEnzymes.includes(id)) onMapVisibilityRequest?.();
    };
    const fullAnalysisRows = async (): Promise<EnzymeCutData[] | undefined> => {
        if (hasFullAnalysis || !onAnalyzeAll) return enzymeData;
        const batch = await onAnalyzeAll();
        return batch ? buildEnzymeRows(recordsRef.current, indexRestrictionAnalysis(batch), selection, sequenceData) : undefined;
    };
    const requestCountAnalysis = () => {
        if (!hasFullAnalysis) void Promise.resolve(onAnalyzeAll?.()).catch(() => undefined);
    };
    const toggleQuick = async (group: QuickMapGroup) => {
        if (pendingGroups.current.delete(group)) {
            setPendingMappingCount(pendingGroups.current.size);
            return;
        }
        const generation = mappingGeneration.current;
        const request = Symbol(group);
        const activate = !activeGroupsRef.current.has(group);
        if (activate) {
            pendingGroups.current.set(group, request);
            setPendingMappingCount(pendingGroups.current.size);
            setMappingError(null);
            onMapVisibilityRequest?.();
        }
        try {
            const rows = activate ? await fullAnalysisRows() : enzymeData;
            if (generation !== mappingGeneration.current || (activate && pendingGroups.current.get(group) !== request)) return;
            if (!rows) {
                setMappingError('Enzyme mapping did not finish. Retry the mapping button.');
                return;
            }
            const next = toggleQuickMapGroupSelection({ selectedEnzymes: selectedEnzymesRef.current, activeGroups: activeGroupsRef.current }, manualRef.current, rows, group);
            updateGroups(next.activeGroups);
            updateMapped(next.selectedEnzymes);
        } catch (error) {
            if (generation === mappingGeneration.current) setMappingError(error instanceof Error ? error.message : 'Enzyme mapping is unavailable.');
        } finally {
            if (generation === mappingGeneration.current && pendingGroups.current.get(group) === request) {
                pendingGroups.current.delete(group);
                setPendingMappingCount(pendingGroups.current.size);
            }
        }
    };
    const mapFiltered = async () => {
        const generation = mappingGeneration.current;
        const request = Symbol('filtered');
        pendingGroups.current.set('filtered', request);
        setPendingMappingCount(pendingGroups.current.size);
        setMappingError(null);
        try {
            const rows = await fullAnalysisRows();
            if (generation !== mappingGeneration.current || pendingGroups.current.get('filtered') !== request) return;
            if (!rows) {
                setMappingError('Enzyme mapping did not finish. Retry the mapping button.');
                return;
            }
            const ids = filterEnzymeRows(rows, searchQuery, groupFilter, cutFilter)
                .filter(({ summary }) => summary && summary.recognition_site_count_definite + summary.recognition_site_count_possible > 0)
                .map(({ record }) => record.enzyme_id);
            manualRef.current = new Set([...manualRef.current, ...ids]);
            updateMapped(mergeMappedEnzymes(selectedEnzymesRef.current, ids));
            if (ids.length > 0) onMapVisibilityRequest?.();
        } catch (error) {
            if (generation === mappingGeneration.current) setMappingError(error instanceof Error ? error.message : 'Enzyme mapping is unavailable.');
        } finally {
            if (generation === mappingGeneration.current && pendingGroups.current.get('filtered') === request) {
                pendingGroups.current.delete('filtered');
                setPendingMappingCount(pendingGroups.current.size);
            }
        }
    };
    const clearMap = () => {
        cancelPendingMapping();
        manualRef.current = new Set();
        updateGroups(new Set());
        updateMapped([]);
    };

    useEffect(() => {
        const regions = digestSimulation?.fragments.flatMap((fragment, index) => fragment.source_segments.map(([start, end]) => ({ start, end, color: index % 2 ? '#34d399' : '#38bdf8', label: `Fragment ${fragment.fragment_index + 1} (${fragment.reference_span_bp} bp)` }))) ?? [];
        onHighlight(regions);
        return () => onHighlight([]);
    }, [digestSimulation, onHighlight]);

    return <div data-digest-layout={mobile ? 'mobile' : 'desktop'} data-digest-compact-landscape={mobile && compactLandscape ? 'true' : undefined} className={mobile ? 'digest-panel flex h-full min-h-0 flex-col overflow-hidden bg-slate-900 text-sm' : 'digest-panel space-y-4 p-3 text-sm'}>
        <div data-digest-scroll-region={mobile ? 'controls' : undefined} className={mobile ? 'min-h-0 flex-1 overflow-y-auto overscroll-contain' : 'contents'}>
        {!compactLandscape && <div className={mobile ? 'px-3 pt-3' : ''}><h4 className="font-semibold text-slate-200">Restriction Analysis</h4><p className="text-xs text-slate-500">Backend catalog and exact cleavage authority</p></div>}
        {(authorityError || digestError) && <div role="alert" className="mx-3 rounded border border-red-800 bg-red-900/40 p-2 text-red-200">{authorityError || digestError}</div>}
        {pendingMappingCount > 0 && <p role="status" className="px-3 text-xs text-cyan-200">Mapping selected enzyme groups…</p>}
        {mappingError && <p role="alert" className="px-3 text-xs text-red-200">{mappingError}</p>}
        {authorityLoading && <div className="px-3 text-xs text-slate-400">Analyzing requested enzymes; available results remain usable…</div>}
        <div className="flex flex-wrap items-center gap-2 px-3 text-xs">
            <button type="button" onClick={onAnalyzeAll} disabled={!onAnalyzeAll || !catalog || catalogRecords.length !== catalog.counts.total || authorityLoading} className="rounded border border-slate-600 px-2 py-1 disabled:opacity-40">Analyze full catalog</button>
            <span className="text-slate-400">Select enzymes for immediate analysis or run the full catalog explicitly.</span>
            <span className="text-slate-500">Supplier information: {productEvidence ? 'loaded separately' : 'unavailable'}</span>
            {analysis?.complete === false && <span className="text-amber-300">Partial scan: {analysis.analysis.enzyme_summaries.length}/{analysis.requested_enzyme_count} requested enzymes analyzed.</span>}
        </div>
        {analysis && <div data-restriction-counts="true" className="grid grid-cols-3 gap-2 px-3 text-xs text-slate-300">
            <div><strong>{displayCounts.definite}</strong> definite {specialCasesVisible ? 'sequence-motif' : 'enzyme-site'} match{displayCounts.definite === 1 ? '' : 'es'} <span className="text-slate-500">across {displayCounts.enzymes} analyzed enzymes</span><div className="text-slate-500"><strong>{displayCounts.possible}</strong> possible match{displayCounts.possible === 1 ? '' : 'es'}</div></div>
            {specialCasesVisible ? <div className="col-span-2 text-amber-200">Sequence motifs only; no cuts on unmodified DNA.</div> : <><div><strong>{displayCounts.dsb}</strong> predicted DSB{displayCounts.dsb === 1 ? '' : 's'}</div><div><strong>{displayCounts.nicks}</strong> nicks</div></>}
        </div>}
        {analysis && <p className="px-3 text-xs text-slate-500">Each enzyme is counted separately. Shared motifs and context-dependent enzymes may overlap at one sequence position.</p>}

        <div data-digest-mobile-sticky-search={mobile ? 'true' : undefined} className="space-y-2 px-3">
            <button type="button" aria-pressed={specialCasesVisible} onClick={() => { setGroupFilter(specialCasesVisible ? 'all' : 'special'); setCutFilter('all'); setDisplayPage(0); }} className={`${mobile ? 'min-h-12 min-w-12' : ''} rounded border px-3 py-1 ${specialCasesVisible ? 'border-amber-400 bg-amber-900/40' : 'border-slate-600 bg-slate-800'}`}>Special cases</button>
            {specialCasesVisible && <p className="text-xs text-amber-200">These enzymes require modified cytosines. Sequence matches alone do not establish cleavage; substrate requirements differ by enzyme. Map them here to inspect sequence motifs.</p>}
            <input value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="Search enzyme or recognition site…" data-digest-mobile-touch-target={mobile ? 'true' : undefined} className={`w-full rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 ${mobile ? 'min-h-12 min-w-12' : ''}`} />
            {!compactLandscape && <>{!specialCasesVisible && <div data-digest-mobile-filter-bar={mobile ? 'true' : undefined} className="flex gap-1 overflow-x-auto">{CUT_FILTERS.map(([value,label]) => <button key={value} onClick={() => { setCutFilter(value); if (value !== 'all') requestCountAnalysis(); }} className={`${mobile ? 'min-h-12 min-w-12' : ''} rounded border px-2 ${cutFilter === value ? 'bg-cyan-700' : 'bg-slate-800'}`}>{label}</button>)}</div>}{!specialCasesVisible && <div data-digest-mobile-filter-bar={mobile ? 'true' : undefined} className="flex gap-1 overflow-x-auto">{GROUP_FILTERS.map(([value,label]) => <button key={value} onClick={() => { setGroupFilter(value); if (value === 'nicking') requestCountAnalysis(); }} className={`${mobile ? 'min-h-12 min-w-12' : ''} rounded border px-2 ${groupFilter === value ? 'bg-cyan-700' : 'bg-slate-800'}`}>{label}</button>)}</div>}<div data-digest-bulk-map="true" className="flex gap-1 overflow-x-auto"><button disabled={hasFullAnalysis && filteredMapIds.length === 0} onClick={() => void mapFiltered()} className={`${mobile ? 'min-h-12 min-w-12' : ''} rounded border border-cyan-600 bg-cyan-950/50 px-2 disabled:opacity-40`}>Map filtered ({filteredMapIds.length})</button><button disabled={selectedEnzymes.length === 0 && pendingMappingCount === 0} onClick={clearMap} className={`${mobile ? 'min-h-12 min-w-12' : ''} rounded border px-2 disabled:opacity-40`}>Clear map ({selectedEnzymes.length})</button></div>{!specialCasesVisible && <div data-digest-quick-map="true" className="flex gap-1 overflow-x-auto">{QUICK.map(([value,label]) => <button key={value} aria-pressed={activeQuickMapGroups.has(value)} aria-busy={pendingGroups.current.has(value)} onClick={() => void toggleQuick(value)} className={`${mobile ? 'min-h-12 min-w-12' : ''} ${activeQuickMapGroups.has(value) ? 'border-cyan-400 bg-cyan-700' : 'border-slate-600 bg-slate-800'} rounded border px-2`}>{label}</button>)}</div>}</>}
        </div>
        <nav aria-label="Enzyme display pages" className="flex items-center justify-between gap-2 px-3">
            <button type="button" disabled={currentPage === 0} onClick={() => setDisplayPage(currentPage - 1)} className={`${mobile ? 'min-h-12 min-w-12' : ''} rounded border px-2 disabled:opacity-40`}>Previous enzymes</button>
            <span role="status" className="text-xs text-slate-400">{filtered.length === 0 ? '0 enzymes' : `${currentPage * 50 + 1}–${Math.min((currentPage + 1) * 50, filtered.length)} of ${filtered.length} enzymes`} · Page {currentPage + 1}/{pageCount}</span>
            <button type="button" disabled={currentPage + 1 === pageCount} onClick={() => setDisplayPage(currentPage + 1)} className={`${mobile ? 'min-h-12 min-w-12' : ''} rounded border px-2 disabled:opacity-40`}>Next enzymes</button>
        </nav>
        <div data-digest-scroll-region={mobile ? 'enzymes' : undefined} className={mobile ? 'space-y-2 p-3' : 'max-h-80 space-y-2 overflow-y-auto'}>
            {displayed.map(({ record, summary }) => { const special = restrictionSpecialCase(record.enzyme_id); const digestable = record.analysis_capability === 'digest_simulation'; return <div key={record.enzyme_id} data-enzyme-name={record.enzyme_id} className="rounded border border-slate-700 bg-slate-800 p-2"><div className="flex justify-between gap-2"><div><strong>{record.canonical_name}</strong><div className="font-mono text-xs text-slate-400">{restrictionRecognitionSite(record)}</div>{special && <p className="max-w-lg text-xs text-amber-200">{special}</p>}{summary ? <div className="text-xs">{summary.recognition_site_count_definite} definite + {summary.recognition_site_count_possible} possible {special ? 'sequence motifs (not cuts)' : `· ${summary.double_strand_break_count} DSB · ${summary.nick_count} nick`}</div> : <div className="text-xs text-amber-300">{record.analysis_capability === 'recognition_only' ? 'geometry unavailable' : 'not yet analyzed'}</div>}{!digestable && summary && <span className="text-xs text-amber-300">{record.analysis_capability === 'nicking_analysis' ? 'map-only nickase' : 'geometry unavailable'}</span>}</div><div className="flex gap-1"><button onClick={() => toggleMap(record.enzyme_id)} data-digest-mobile-touch-target={mobile ? 'true' : undefined} className={`${mobile ? 'min-h-12 min-w-20' : ''} rounded border px-2 disabled:opacity-40`}>{selectedEnzymes.includes(record.enzyme_id) ? 'Unmap' : 'Map'}</button><button disabled={!digestable} onClick={() => toggleDigest(record.enzyme_id)} data-digest-mobile-touch-target={mobile ? 'true' : undefined} className={`${mobile ? 'min-h-12 min-w-20' : ''} rounded border px-2 disabled:opacity-40`}>{mobile ? (digestEnzymes.includes(record.enzyme_id) ? 'Remove' : 'Add') : 'Digest'}</button></div></div></div>; })}
        </div>
        {onReadEnzymeDetails && <div className="px-3">
            <button type="button" onClick={readDetails} disabled={selectedEnzymes.length + digestEnzymes.length === 0}>Read selected enzyme details</button>
            {visibleDetails?.error && <p role="alert">{visibleDetails.error}</p>}
            {visibleDetails?.records?.map(record => <details key={record.enzyme_id} data-enzyme-detail={record.enzyme_id}>
                <summary>{record.canonical_name} · {record.recognition.site_iupac} · {record.cleavage.status}</summary>
                <pre className="overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify({ cleavage: record.cleavage, relationships: record.relationships, source: record.source, supplier_provenance: record.supplier_provenance }, null, 2)}</pre>
            </details>)}
        </div>}
        </div>
        <div data-digest-mobile-footer={mobile ? 'true' : undefined} data-digest-compact-landscape={mobile && compactLandscape ? 'true' : undefined} className={`${mobile ? 'max-h-[40%] shrink-0 overflow-y-auto overscroll-contain' : ''} border-t border-slate-700 bg-slate-950 p-3`}>
            <div className="mb-2 flex gap-1 overflow-x-auto">{digestEnzymes.map((id) => <button key={id} onClick={() => toggleDigest(id)} className="rounded border border-amber-500 px-2">{id} ×</button>)}</div>
            {digestEnzymes.some(id => restrictionSpecialCase(id)) && <p className="mb-2 text-xs text-amber-200">Special-case simulation shows motif-based geometry, not cleavage of unmodified DNA.</p>}
            <button onClick={() => onSimulateDigest(digestEnzymes)} disabled={digestLoading || digestEnzymes.length === 0} data-digest-mobile-run={mobile ? 'true' : undefined} data-digest-mobile-touch-target={mobile ? 'true' : undefined} className={`w-full rounded bg-cyan-600 py-2 disabled:bg-slate-600 ${mobile ? 'min-h-12 min-w-12' : ''}`}>{digestLoading ? 'Digesting…' : `Run Digest (${digestEnzymes.length} enzyme${digestEnzymes.length === 1 ? '' : 's'})`}</button>
            {digestSimulation?.source?.kind === 'molecular_revision' && <div>
                <button type="button" onClick={saveDigest} disabled={currentSave?.pending || !!currentSave?.operationId} className="rounded border px-3 py-2 disabled:opacity-40">{currentSave?.pending ? 'Saving…' : 'Save digest & fragments'}</button>
                {onUseInAssembly && digestSimulation && <div><p>Select physical fragments for Assembly (order can be edited there):</p>{digestSimulation.fragments.map(f => <label key={f.fragment_index}><input type="checkbox" aria-label={`Use digest fragment ${f.fragment_index}`} checked={assemblyIndices.includes(f.fragment_index)} onChange={e => setAssemblySelection({simulation:digestSimulation,indices:e.target.checked ? [...assemblyIndices,f.fragment_index] : assemblyIndices.filter(i => i !== f.fragment_index)})}/>{f.fragment_index}: {f.top_strand_sequence.length} bp</label>)}<button disabled={!assemblyIndices.length} onClick={() => onUseInAssembly(digestFragmentsForAssembly(digestSimulation,assemblyIndices,sequenceId,sequenceData.name))}>Use selected fragments in assembly</button></div>}
                {currentSave?.operationId && <a className="ml-2 underline" href={`/api/molbio/restriction/digests/${encodeURIComponent(currentSave.operationId)}`} target="_blank" rel="noreferrer">Open saved digest</a>}
                {currentSave?.error && <p role="alert">{currentSave.error}</p>}
            </div>}
            {digestSimulation && <div data-digest-mobile-result={mobile ? 'true' : undefined} className="mt-2 space-y-1 rounded border border-cyan-700 p-2"><div>{digestSimulation.fragments.length} exact sequence fragments</div>{digestSimulation.fragments.map((fragment) => <div key={fragment.fragment_index} data-fragment-index={fragment.fragment_index} className="rounded bg-slate-800 p-2 text-xs"><strong>#{fragment.fragment_index + 1} · {fragment.reference_span_bp} bp</strong><div>Left: {endLabel(fragment.left_end)}</div><div>Right: {endLabel(fragment.right_end)}</div></div>)}</div>}
        </div>
    </div>;
}
