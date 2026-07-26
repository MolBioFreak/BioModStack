import { useEffect, useMemo, useRef, useState } from 'react';
import {
    fetchGoldenGateAssemblyOptions,
    fetchSavedGibsonWorkups,
    planDnaWeaverGibsonAssembly,
    saveDnaWeaverGibsonAssembly,
    saveGibsonAssembly,
    saveGoldenGateAssembly,
    saveLigationAssembly,
    simulateGibsonAssembly,
    simulateGoldenGateAssembly,
    simulateLigationAssembly,
    type AssemblyFragmentEnd,
    type AssemblyFragmentInput,
    type AssemblyOperationResponse,
    type DnaWeaverPlanRequest,
    type DnaWeaverPlanResponse,
    type GoldenGateAssemblyOptionsResponse,
    type SavedGibsonWorkupListItem,
} from '../../../lib/api';
import type { SequenceData, SelectionInfo } from '../types';
import { GibsonDesignWorkspace } from './GibsonDesignWorkspace';

interface AssemblyPanelProps {
    sequenceData: SequenceData;
    selection: SelectionInfo | null;
    selectedSequenceId: string | null;
    onLoadProduct: (sequenceData: SequenceData, savedSequenceId?: string | null) => void;
    onLoadSavedWorkup: (savedSequenceId: string) => Promise<void> | void;
}

type AssemblyMode = 'ligation' | 'gibson' | 'golden_gate';

function nextFragmentId(): string {
    return `asm_${Math.random().toString(36).slice(2, 9)}`;
}

function selectionSequence(sequenceData: SequenceData, selection: SelectionInfo | null): { sequence: string; start: number; end: number; wrapsOrigin: boolean } | null {
    if (!selection || selection.start === selection.end) {
        return null;
    }
    const seq = sequenceData.sequence;
    const rawStart = selection.start;
    const rawEnd = selection.end;
    const start = Math.max(0, Math.min(rawStart, seq.length));
    const end = Math.max(0, Math.min(rawEnd, seq.length));

    if (!sequenceData.circular || start < end) {
        return {
            sequence: seq.slice(start, end),
            start: Math.min(start, end),
            end: Math.max(start, end),
            wrapsOrigin: false,
        };
    }

    return {
        sequence: seq.slice(start) + seq.slice(0, end),
        start,
        end,
        wrapsOrigin: true,
    };
}

function defaultEnds(mode: AssemblyMode): { left_end?: AssemblyFragmentEnd; right_end?: AssemblyFragmentEnd } {
    if (mode === 'gibson') {
        return {};
    }
    if (mode === 'golden_gate') {
        return {
            left_end: { type: 'sticky_5', overhang: '' },
            right_end: { type: 'sticky_5', overhang: '' },
        };
    }
    return {
        left_end: { type: 'blunt', overhang: '' },
        right_end: { type: 'blunt', overhang: '' },
    };
}

function fragmentFromSequence(
    source: {
        id?: string | null;
        sourceName?: string;
        name: string;
        sequence: string;
        role?: string;
        sourceStart?: number;
        sourceEnd?: number;
        wrapsOrigin?: boolean;
    },
    mode: AssemblyMode,
): AssemblyFragmentInput {
    return {
        id: nextFragmentId(),
        name: source.name,
        sequence: source.sequence,
        orientation: 'forward',
        circular: false,
        role: source.role,
        source_sequence_id: source.id || undefined,
        source_name: source.sourceName,
        source_start: source.sourceStart,
        source_end: source.sourceEnd,
        source_wraps_origin: source.wrapsOrigin,
        ...defaultEnds(mode),
    };
}

function EndEditor({
    label,
    value,
    disabled,
    onChange,
}: {
    label: string;
    value?: AssemblyFragmentEnd;
    disabled?: boolean;
    onChange: (next?: AssemblyFragmentEnd) => void;
}) {
    if (disabled) {
        return (
            <div className="space-y-1">
                <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">{label}</div>
                <div className="rounded border border-slate-800 bg-slate-950/70 px-2 py-1.5 text-xs text-slate-500">
                    Overlap-driven
                </div>
            </div>
        );
    }

    const current = value || { type: 'blunt' as const, overhang: '' };
    return (
        <div className="space-y-1">
            <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">{label}</div>
            <div className="grid grid-cols-[132px_minmax(0,1fr)] gap-2">
                <select
                    value={current.type}
                    onChange={(event) => onChange({ ...current, type: event.target.value as AssemblyFragmentEnd['type'] })}
                    className="rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs"
                >
                    <option value="blunt">Blunt</option>
                    <option value="sticky_5">5' sticky</option>
                    <option value="sticky_3">3' sticky</option>
                </select>
                <input
                    value={current.overhang || ''}
                    disabled={current.type === 'blunt'}
                    onChange={(event) => onChange({ ...current, overhang: event.target.value.toUpperCase().replace(/[^A-Z]/g, '') })}
                    placeholder={current.type === 'blunt' ? 'No overhang' : 'Overhang'}
                    className="rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs disabled:opacity-50"
                />
            </div>
        </div>
    );
}

type SavedWorkupRecord = Record<string, unknown>;

function asWorkupRecord(value: unknown): SavedWorkupRecord | null {
    return value !== null && typeof value === 'object' && !Array.isArray(value)
        ? value as SavedWorkupRecord
        : null;
}

function asWorkupRecords(value: unknown): SavedWorkupRecord[] {
    return Array.isArray(value)
        ? value.map(asWorkupRecord).filter((item): item is SavedWorkupRecord => item !== null)
        : [];
}

function workupText(value: unknown, fallback = '—'): string {
    return typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
}

function SavedGibsonWorkupLibrary({
    records,
    loading,
    error,
    onRefresh,
    onLoad,
}: {
    records: SavedGibsonWorkupListItem[];
    loading: boolean;
    error: string | null;
    onRefresh: () => void;
    onLoad: (savedSequenceId: string) => Promise<void> | void;
}) {
    const [open, setOpen] = useState(false);
    const [loadingId, setLoadingId] = useState<string | null>(null);

    const load = async (id: string) => {
        setLoadingId(id);
        try {
            await onLoad(id);
            setOpen(false);
        } finally {
            setLoadingId(null);
        }
    };

    return (
        <section aria-label="Saved Gibson workups library" className="rounded-xl border border-violet-500/50 bg-violet-950/20 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                    <h5 className="font-semibold text-violet-100">Saved Gibson workups</h5>
                    <p className="mt-1 text-xs text-violet-200/70">Assembly-only records saved on the server. Loading opens the associated construct and its immutable workup evidence.</p>
                </div>
                <div className="flex gap-2">
                    <button type="button" onClick={onRefresh} disabled={loading} className="rounded border border-violet-400/50 px-2 py-1 text-xs text-violet-100 disabled:opacity-50">Refresh</button>
                    <button type="button" onClick={() => setOpen(true)} className="rounded bg-violet-600 px-3 py-1.5 text-xs font-medium text-white">Saved workups ({records.length})</button>
                </div>
            </div>
            {error && <p className="mt-2 text-xs text-red-300">Could not load saved workups: {error}</p>}
            {open && (
                <div role="dialog" aria-modal="true" aria-label="Saved Gibson workups" className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4">
                    <div className="max-h-[80vh] w-full max-w-2xl overflow-auto rounded-xl border border-violet-400/50 bg-slate-900 p-4 shadow-2xl">
                        <div className="flex items-start justify-between gap-3">
                            <div><h4 className="font-semibold text-violet-100">Saved Gibson workups</h4><p className="mt-1 text-xs text-slate-400">Only persisted Gibson assemblies appear here.</p></div>
                            <button type="button" onClick={() => setOpen(false)} className="rounded px-2 py-1 text-sm text-slate-300 hover:bg-slate-800" aria-label="Close saved workups">Close</button>
                        </div>
                        <div className="mt-4 space-y-2">
                            {records.length === 0 && <p className="rounded border border-dashed border-slate-700 p-3 text-sm text-slate-400">No saved Gibson workups yet.</p>}
                            {records.map((record) => <article key={record.id} className="rounded-lg border border-slate-700 bg-slate-950/40 p-3">
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                    <div className="min-w-0"><h5 className="font-medium text-slate-100">{record.name}</h5><p className="mt-1 text-xs text-slate-400">{record.length.toLocaleString()} bp • {record.topology} • {record.engine || 'Gibson'} {record.engine_version || ''}</p><p className="mt-1 text-xs text-slate-500">{record.fragment_count} fragments • {record.primer_count} primers</p></div>
                                    <button type="button" onClick={() => void load(record.id)} disabled={loadingId === record.id} className="rounded bg-violet-600 px-3 py-2 text-xs font-medium text-white disabled:opacity-50">{loadingId === record.id ? 'Loading…' : 'Load workup'}</button>
                                </div>
                            </article>)}
                        </div>
                    </div>
                </div>
            )}
        </section>
    );
}

function workupPreparation(fragment: SavedWorkupRecord): string {
    const metadata = asWorkupRecord(fragment.metadata);
    const preparation = fragment.preparation ?? metadata?.preparation;
    const procurement = fragment.procurement ?? metadata?.procurement;
    if (preparation === 'ready_linear') {
        return procurement === 'vendor_purchase' || metadata?.vendor_purchase === 'full_fragment' ? 'Vendor-ready linear' : 'Ready linear';
    }
    return preparation === 'pcr' ? 'PCR' : 'Unspecified preparation';
}

function SavedGibsonWorkup({ operationParams }: { operationParams?: Record<string, unknown> | null }) {
    if (operationParams?.mode !== 'gibson') return null;
    const fragments = asWorkupRecords(operationParams.ordered_fragments ?? operationParams.source_fragments ?? operationParams.fragments);
    const junctions = asWorkupRecords(operationParams.junctions);
    const primers = asWorkupRecords(operationParams.primers);
    const validationNotes = Array.isArray(operationParams.validation_notes)
        ? operationParams.validation_notes.filter((note): note is string => typeof note === 'string')
        : [];
    const warningValues: unknown = operationParams.design_warnings ?? operationParams.warnings;
    const warnings = Array.isArray(warningValues)
        ? warningValues.filter((warning): warning is string => typeof warning === 'string')
        : [];

    return (
        <section aria-label="Saved Gibson workup" className="space-y-3 rounded-xl border border-violet-500/40 bg-violet-950/20 p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                    <h5 className="font-semibold text-violet-100">Saved Gibson workup</h5>
                    <p className="mt-1 text-xs text-violet-200/70">Read-only server-persisted design evidence for this construct.</p>
                </div>
                <span className="rounded-full border border-violet-400/40 px-2 py-1 text-[11px] font-medium text-violet-200">
                    {workupText(operationParams.engine, 'Gibson')} {workupText(operationParams.engine_version, '')}
                </span>
            </div>
            <div className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded bg-slate-950/50 p-2"><div className="text-slate-500">Fragments</div><div className="mt-1 font-medium text-slate-100">{fragments.length}</div></div>
                <div className="rounded bg-slate-950/50 p-2"><div className="text-slate-500">Primers</div><div className="mt-1 font-medium text-slate-100">{primers.length}</div></div>
                <div className="rounded bg-slate-950/50 p-2"><div className="text-slate-500">Junctions</div><div className="mt-1 font-medium text-slate-100">{junctions.length}</div></div>
                <div className="rounded bg-slate-950/50 p-2"><div className="text-slate-500">Overlap / Tm</div><div className="mt-1 font-medium text-slate-100">{workupText(operationParams.overlap)} nt / {workupText(operationParams.target_tm)} °C</div></div>
            </div>
            <div className="rounded bg-slate-950/50 p-2 text-xs">
                <div className="text-slate-500">Server-selected candidate checksum</div>
                <code className="mt-1 block break-all text-[11px] text-emerald-300">{workupText(operationParams.candidate_checksum)}</code>
                {operationParams.plan_checksum != null && <><div className="mt-2 text-slate-500">DNA Weaver plan checksum</div><code className="mt-1 block break-all text-[11px] text-cyan-300">{workupText(operationParams.plan_checksum)}</code></>}
                {operationParams.validator_engine != null && <div className="mt-2 text-slate-400">Validator: {workupText(operationParams.validator_engine)} {workupText(operationParams.validator_version)} • exact candidates {workupText(operationParams.pydna_exact_candidate_count)}</div>}
            </div>
            <details open className="rounded border border-slate-700 bg-slate-950/30 p-2">
                <summary className="cursor-pointer text-xs font-medium text-slate-200">Source fragments ({fragments.length})</summary>
                <div className="mt-2 max-h-64 space-y-1 overflow-auto text-xs">
                    {fragments.map((fragment, index) => <div key={`${workupText(fragment.fragment_id ?? fragment.id)}-${index}`} className="border-b border-slate-800 py-1 text-slate-300">
                        <div className="flex flex-wrap justify-between gap-2">
                            <span>{workupText(fragment.name, workupText(fragment.fragment_id ?? fragment.id))} • {workupPreparation(fragment)}</span>
                            <span className="font-mono text-slate-500">{workupText(fragment.source_start ?? fragment.source_core_start)}–{workupText(fragment.source_end ?? fragment.source_core_end)}{fragment.source_wraps_origin === true ? ' ↻' : ''}</span>
                        </div>
                        {typeof fragment.sequence === 'string' && <code className="mt-1 block max-h-24 overflow-auto break-all text-[10px] text-slate-500">{fragment.sequence}</code>}
                    </div>)}
                </div>
            </details>
            <details className="rounded border border-slate-700 bg-slate-950/30 p-2">
                <summary className="cursor-pointer text-xs font-medium text-slate-200">Validated junctions ({junctions.length})</summary>
                <div className="mt-2 max-h-64 space-y-2 overflow-auto text-xs">
                    {junctions.map((junction, index) => <div key={`${workupText(junction.left_fragment_id)}-${index}`} className="border-b border-slate-800 pb-2 text-slate-300">
                        <div>{workupText(junction.left_fragment_name)} → {workupText(junction.right_fragment_name)} <span className="text-emerald-300">{workupText(junction.validation)}</span></div>
                        <code className="block break-all text-[11px] text-slate-500">{workupText(junction.overlap_sequence)} ({workupText(junction.overlap_length)} nt)</code>
                    </div>)}
                </div>
            </details>
            <details className="rounded border border-slate-700 bg-slate-950/30 p-2">
                <summary className="cursor-pointer text-xs font-medium text-slate-200">Generated primers ({primers.length})</summary>
                <div className="mt-2 max-h-64 space-y-2 overflow-auto text-xs">
                    {primers.map((primer, index) => <div key={`${workupText(primer.id)}-${index}`} className="border-b border-slate-800 pb-2">
                        <div className="text-slate-300">{workupText(primer.fragment_name)} • {workupText(primer.direction)} • {workupText(primer.tm)} °C</div>
                        <code className="block break-all text-[11px] text-slate-500">{workupText(primer.full_sequence)}</code>
                    </div>)}
                </div>
            </details>
            {(validationNotes.length > 0 || warnings.length > 0) && <details className="rounded border border-slate-700 bg-slate-950/30 p-2">
                <summary className="cursor-pointer text-xs font-medium text-slate-200">Validation notes ({validationNotes.length}) / warnings ({warnings.length})</summary>
                <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-slate-400">
                    {[...validationNotes, ...warnings].map((note, index) => <li key={`${note}-${index}`}>{note}</li>)}
                </ul>
            </details>}
        </section>
    );
}

export function AssemblyPanel({
    sequenceData,
    selection,
    selectedSequenceId,
    onLoadProduct,
    onLoadSavedWorkup,
}: AssemblyPanelProps) {
    const [mode, setMode] = useState<AssemblyMode>('ligation');
    const [fragments, setFragments] = useState<AssemblyFragmentInput[]>([]);
    const [gibsonWorkflow, setGibsonWorkflow] = useState<'plan' | 'design' | 'validate'>('plan');
    const [gibsonPreparations, setGibsonPreparations] = useState<Record<string, 'pcr' | 'ready_linear'>>({});
    const [saveName, setSaveName] = useState('');
    const [saveDescription, setSaveDescription] = useState('');
    const [goldenGateOptions, setGoldenGateOptions] = useState<GoldenGateAssemblyOptionsResponse | null>(null);
    const [goldenGateEnzyme, setGoldenGateEnzyme] = useState('BsaI');
    const [gibsonMinOverlap, setGibsonMinOverlap] = useState(20);
    const [gibsonPreferredOverlap, setGibsonPreferredOverlap] = useState(28);
    const [gibsonMaxOverlap, setGibsonMaxOverlap] = useState(80);
    const [pasteName, setPasteName] = useState('');
    const [pasteSequence, setPasteSequence] = useState('');
    const [result, setResult] = useState<AssemblyOperationResponse | null>(null);
    const [loading, setLoading] = useState<'simulate' | 'save' | null>(null);
    const [planning, setPlanning] = useState(false);
    const [dnaWeaverPlan, setDnaWeaverPlan] = useState<DnaWeaverPlanResponse | null>(null);
    const [plannerMinFragmentLength, setPlannerMinFragmentLength] = useState(500);
    const [plannerMaxFragmentLength, setPlannerMaxFragmentLength] = useState(1500);
    const [plannerOverlapLength, setPlannerOverlapLength] = useState(30);
    const [plannerVendorName, setPlannerVendorName] = useState('Configured commercial DNA vendor');
    const [plannerPricePerBp, setPlannerPricePerBp] = useState(0.08);
    const [plannerLeadTimeDays, setPlannerLeadTimeDays] = useState(10);
    const [error, setError] = useState<string | null>(null);
    const [savedWorkups, setSavedWorkups] = useState<SavedGibsonWorkupListItem[]>([]);
    const [savedWorkupsLoading, setSavedWorkupsLoading] = useState(false);
    const [savedWorkupsError, setSavedWorkupsError] = useState<string | null>(null);
    const plannerScopeRef = useRef('');

    useEffect(() => {
        setDnaWeaverPlan(null);
        setResult(null);
    }, [selectedSequenceId, sequenceData.sequence, sequenceData.circular]);

    const refreshSavedWorkups = async () => {
        setSavedWorkupsLoading(true);
        setSavedWorkupsError(null);
        try {
            const response = await fetchSavedGibsonWorkups();
            setSavedWorkups(response.data);
        } catch (loadError) {
            setSavedWorkupsError(loadError instanceof Error ? loadError.message : 'Request failed');
        } finally {
            setSavedWorkupsLoading(false);
        }
    };

    const activeSelection = useMemo(() => selectionSequence(sequenceData, selection), [sequenceData, selection]);

    useEffect(() => {
        void refreshSavedWorkups();
    }, []);

    useEffect(() => {
        let cancelled = false;
        const loadOptions = async () => {
            try {
                const response = await fetchGoldenGateAssemblyOptions();
                if (!cancelled) {
                    setGoldenGateOptions(response.data);
                    if (response.data.enzymes.length > 0) {
                        setGoldenGateEnzyme((current) => current || response.data.enzymes[0].name);
                    }
                }
            } catch (loadError) {
                console.error('Failed to load Golden Gate options:', loadError);
            }
        };
        loadOptions();
        return () => {
            cancelled = true;
        };
    }, []);

    const addWholeConstruct = () => {
        setFragments((current) => [
            ...current,
            fragmentFromSequence({
                id: selectedSequenceId,
                sourceName: sequenceData.name,
                name: current.length === 0 ? `${sequenceData.name} backbone` : `${sequenceData.name} fragment ${current.length + 1}`,
                sequence: sequenceData.sequence,
                role: current.length === 0 ? 'vector' : 'insert',
            }, mode),
        ]);
    };

    const addSelectionFragment = () => {
        if (!activeSelection) return;
        setFragments((current) => [
            ...current,
            fragmentFromSequence({
                id: selectedSequenceId,
                sourceName: sequenceData.name,
                name: `${sequenceData.name} ${activeSelection.start + 1}-${activeSelection.end}${activeSelection.wrapsOrigin ? ' wrap' : ''}`,
                sequence: activeSelection.sequence,
                role: current.length === 0 ? 'vector' : 'insert',
                sourceStart: activeSelection.start,
                sourceEnd: activeSelection.end,
                wrapsOrigin: activeSelection.wrapsOrigin,
            }, mode),
        ]);
    };

    const addPastedFragment = () => {
        const sequence = pasteSequence.toUpperCase().replace(/[^A-Z]/g, '');
        if (!sequence) {
            return;
        }
        setFragments((current) => [
            ...current,
            fragmentFromSequence({
                name: pasteName.trim() || `Fragment ${current.length + 1}`,
                sequence,
                role: current.length === 0 ? 'vector' : 'insert',
            }, mode),
        ]);
        setPasteName('');
        setPasteSequence('');
    };

    const updateFragment = (fragmentId: string, patch: Partial<AssemblyFragmentInput>) => {
        setFragments((current) => current.map((fragment) => (
            fragment.id === fragmentId ? { ...fragment, ...patch } : fragment
        )));
        setDnaWeaverPlan(null);
    };

    const removeFragment = (fragmentId: string) => {
        setFragments((current) => current.filter((fragment) => fragment.id !== fragmentId));
        setDnaWeaverPlan(null);
        setGibsonPreparations((current) => {
            const next = { ...current };
            delete next[fragmentId];
            return next;
        });
    };

    const moveFragment = (index: number, direction: -1 | 1) => {
        setFragments((current) => {
            const target = index + direction;
            if (target < 0 || target >= current.length) return current;
            const next = [...current];
            [next[index], next[target]] = [next[target], next[index]];
            return next;
        });
        setResult(null);
        setDnaWeaverPlan(null);
    };

    const resetForMode = (nextMode: AssemblyMode) => {
        setMode(nextMode);
        setResult(null);
        setError(null);
        setFragments((current) => current.map((fragment) => ({
            ...fragment,
            ...defaultEnds(nextMode),
        })));
    };

    const dnaWeaverRequest = (): DnaWeaverPlanRequest => ({
        target_sequence: sequenceData.sequence.replace(/\s/g, '').toUpperCase(),
        target_sequence_id: selectedSequenceId || undefined,
        circular: sequenceData.circular,
        min_fragment_length: plannerMinFragmentLength,
        max_fragment_length: plannerMaxFragmentLength,
        overlap_length: plannerOverlapLength,
        vendor_name: plannerVendorName.trim() || 'Configured commercial DNA vendor',
        price_per_bp: plannerPricePerBp,
        lead_time_days: plannerLeadTimeDays,
    });
    const plannerScope = JSON.stringify(dnaWeaverRequest());
    plannerScopeRef.current = plannerScope;

    useEffect(() => {
        setDnaWeaverPlan(null);
        setResult(null);
        setPlanning(false);
    }, [plannerScope]);

    const planDnaWeaverVendorFragments = async () => {
        const request = dnaWeaverRequest();
        if (!request.target_sequence) {
            setError('The current construct needs a DNA sequence before DNA Weaver can plan vendor fragments.');
            return;
        }
        setPlanning(true);
        setError(null);
        setDnaWeaverPlan(null);
        const scope = plannerScope;
        try {
            const response = await planDnaWeaverGibsonAssembly(request);
            if (plannerScopeRef.current !== scope) return;
            const ordered = response.data.ordered_fragments;
            setDnaWeaverPlan(response.data);
            setFragments(ordered);
            setGibsonPreparations(Object.fromEntries(ordered.map((fragment) => [fragment.id, 'ready_linear'])));
            setGibsonMinOverlap(plannerOverlapLength);
            setGibsonPreferredOverlap(plannerOverlapLength);
            setGibsonMaxOverlap(plannerOverlapLength);
            setSaveName((current) => current || `${sequenceData.name} DNA Weaver Gibson purchase plan`);
            setResult({
                product: response.data.selected_product,
                message: response.data.message,
            });
        } catch (planError: UntypedApiValue) {
            if (plannerScopeRef.current === scope) {
                setError(planError?.response?.data?.detail || planError?.message || 'DNA Weaver planning failed');
            }
        } finally {
            if (plannerScopeRef.current === scope) setPlanning(false);
        }
    };

    const downloadVendorOrder = (format: 'fasta' | 'csv') => {
        if (!dnaWeaverPlan?.order_ready) return;
        const safeName = (saveName || sequenceData.name || 'dnaweaver-order').replace(/[^A-Za-z0-9._-]+/g, '_');
        const content = format === 'fasta'
            ? `${dnaWeaverPlan.ordered_fragments.map((fragment, index) => `>${fragment.name || `fragment_${index + 1}`}|length=${fragment.sequence.length}|sequence_sha256=${fragment.sequence_sha256}|plan_sha256=${dnaWeaverPlan.plan_checksum}\n${fragment.sequence}`).join('\n')}\n`
            : `order,name,length,source_core_start,source_core_end,terminal_overlap_length,sequence_sha256,sequence,plan_checksum\n${dnaWeaverPlan.ordered_fragments.map((fragment, index) => {
                const metadata = fragment.metadata ?? {};
                return [index + 1, fragment.name, fragment.sequence.length, metadata.source_core_start, metadata.source_core_end, metadata.terminal_overlap_length, fragment.sequence_sha256, fragment.sequence, dnaWeaverPlan.plan_checksum]
                    .map((value) => `"${String(value ?? '').replace(/"/g, '""')}"`).join(',');
            }).join('\n')}\n`;
        const blob = new Blob([content], { type: format === 'fasta' ? 'text/plain' : 'text/csv' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `${safeName}.${format === 'fasta' ? 'fasta' : 'csv'}`;
        anchor.click();
        URL.revokeObjectURL(url);
    };

    const execute = async (action: 'simulate' | 'save') => {
        if (mode === 'gibson' && gibsonWorkflow === 'plan' && action === 'simulate') {
            await planDnaWeaverVendorFragments();
            return;
        }
        if (fragments.length === 0) {
            setError('Add at least one fragment to the basket.');
            return;
        }
        if (mode === 'gibson' && gibsonWorkflow === 'plan' && (!dnaWeaverPlan || !dnaWeaverPlan.order_ready)) {
            setError('Create an order-ready DNA Weaver plan before saving. Resolve every blocker and re-plan.');
            return;
        }
        setLoading(action);
        setError(null);
        try {
            if (mode === 'gibson' && gibsonWorkflow === 'plan' && dnaWeaverPlan) {
                const scope = plannerScope;
                const response = await saveDnaWeaverGibsonAssembly({
                    ...dnaWeaverRequest(),
                    selected_plan_checksum: dnaWeaverPlan.plan_checksum,
                    new_name: saveName || undefined,
                    save_description: saveDescription || undefined,
                });
                if (plannerScopeRef.current !== scope) return;
                setDnaWeaverPlan(response.data);
                setResult({
                    product: response.data.selected_product,
                    saved_sequence: response.data.saved_sequence || undefined,
                    message: response.data.message,
                });
                await refreshSavedWorkups();
                return;
            }
            let response;
            if (mode === 'ligation') {
                const payload = {
                    fragments,
                    circular: sequenceData.circular,
                    new_name: saveName || undefined,
                    save_description: saveDescription || undefined,
                };
                response = action === 'save'
                    ? await saveLigationAssembly(payload)
                    : await simulateLigationAssembly(payload);
            } else if (mode === 'gibson') {
                const payload = {
                    fragments,
                    circular: sequenceData.circular,
                    minimum_overlap: gibsonMinOverlap,
                    preferred_overlap: gibsonPreferredOverlap,
                    maximum_overlap: gibsonMaxOverlap,
                    new_name: saveName || undefined,
                    save_description: saveDescription || undefined,
                };
                response = action === 'save'
                    ? await saveGibsonAssembly(payload)
                    : await simulateGibsonAssembly(payload);
            } else {
                const payload = {
                    fragments,
                    circular: sequenceData.circular,
                    enzyme_name: goldenGateEnzyme,
                    new_name: saveName || undefined,
                    save_description: saveDescription || undefined,
                };
                response = action === 'save'
                    ? await saveGoldenGateAssembly(payload)
                    : await simulateGoldenGateAssembly(payload);
            }
            setResult(response.data);
        } catch (runError: UntypedApiValue) {
            setError(runError?.response?.data?.detail || runError?.message || 'Assembly failed');
        } finally {
            setLoading(null);
        }
    };

    const loadResult = () => {
        if (!result) return;
        const savedSequence = result.saved_sequence;
        const name = result.saved_sequence?.name || saveName.trim() || `${sequenceData.name} ${mode.replace('_', ' ')}`;
        onLoadProduct({
            name,
            description: savedSequence?.description || saveDescription || `Derived ${mode.replace('_', ' ')} product`,
            sequence: result.product.sequence,
            circular: result.product.circular,
            sequenceType: 'dna',
            features: [],
            primers: [],
            translations: [],
            analysisTracks: [],
            parentId: savedSequence?.parent_id ?? null,
            operation: savedSequence?.operation ?? result.product.mode,
            operationParams: savedSequence?.operation_params ?? {
                mode: result.product.mode,
                fragment_count: result.product.fragments.length,
                warnings: result.product.warnings,
                validation_notes: result.product.validation_notes,
            },
            version: savedSequence?.version ?? 1,
        }, savedSequence?.id || null);
    };

    return (
        <div className="space-y-4 p-3 text-sm">
            <div>
                <h4 className="font-semibold text-slate-200">Assembly</h4>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                    Fragment-driven cloning workspace with explicit end or overlap contracts. Nothing is inferred from missing chemistry.
                </p>
            </div>

            <SavedGibsonWorkupLibrary
                records={savedWorkups}
                loading={savedWorkupsLoading}
                error={savedWorkupsError}
                onRefresh={() => void refreshSavedWorkups()}
                onLoad={onLoadSavedWorkup}
            />

            <SavedGibsonWorkup operationParams={sequenceData.operationParams} />

            <div className="flex flex-wrap gap-2">
                {[
                    ['ligation', 'Ligation'],
                    ['gibson', 'Gibson'],
                    ['golden_gate', 'Golden Gate'],
                ].map(([value, label]) => (
                    <button
                        key={value}
                        type="button"
                        onClick={() => resetForMode(value as AssemblyMode)}
                        className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${mode === value ? 'bg-violet-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}
                    >
                        {label}
                    </button>
                ))}
            </div>

            {mode === 'gibson' && (
                <>
                    <div className="grid grid-cols-1 gap-2 rounded-xl border border-slate-700 bg-slate-900/50 p-1.5 sm:grid-cols-3">
                        <button
                            type="button"
                            onClick={() => { setGibsonWorkflow('plan'); setResult(null); setError(null); }}
                            className={`rounded-lg px-3 py-2 text-xs font-medium ${gibsonWorkflow === 'plan' ? 'bg-cyan-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}
                        >
                            Plan vendor fragments (DNA Weaver)
                        </button>
                        <button
                            type="button"
                            onClick={() => { setGibsonWorkflow('validate'); setResult(null); setError(null); }}
                            className={`rounded-lg px-3 py-2 text-xs font-medium ${gibsonWorkflow === 'validate' ? 'bg-slate-700 text-white' : 'text-slate-400 hover:bg-slate-800'}`}
                        >
                            Validate purchased fragments
                        </button>
                        <button
                            type="button"
                            onClick={() => { setGibsonWorkflow('design'); setResult(null); setError(null); }}
                            className={`rounded-lg px-3 py-2 text-xs font-medium ${gibsonWorkflow === 'design' ? 'bg-violet-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}
                        >
                            PCR template route (optional)
                        </button>
                    </div>
                    {gibsonWorkflow === 'plan' ? (
                        <div className="space-y-3 rounded-lg border border-cyan-900/60 bg-cyan-950/20 px-3 py-3 text-xs leading-5 text-cyan-100/80">
                            <p>DNA Weaver will split the current full construct into purchasable, pre-overlapped vendor fragments, then pydna must independently reconstruct the exact requested product.</p>
                            <div className="grid gap-2 sm:grid-cols-3">
                                <label>Minimum core bp<input type="number" min={100} value={plannerMinFragmentLength} onChange={(event) => { setPlannerMinFragmentLength(Number(event.target.value)); setDnaWeaverPlan(null); }} className="mt-1 w-full rounded border border-cyan-900 bg-slate-950 px-2 py-1" /></label>
                                <label>Maximum core bp<input type="number" min={101} value={plannerMaxFragmentLength} onChange={(event) => { setPlannerMaxFragmentLength(Number(event.target.value)); setDnaWeaverPlan(null); }} className="mt-1 w-full rounded border border-cyan-900 bg-slate-950 px-2 py-1" /></label>
                                <label>Terminal overlap nt<input type="number" min={15} max={80} value={plannerOverlapLength} onChange={(event) => { setPlannerOverlapLength(Number(event.target.value)); setDnaWeaverPlan(null); }} className="mt-1 w-full rounded border border-cyan-900 bg-slate-950 px-2 py-1" /></label>
                                <label>Vendor/profile<input value={plannerVendorName} onChange={(event) => { setPlannerVendorName(event.target.value); setDnaWeaverPlan(null); }} className="mt-1 w-full rounded border border-cyan-900 bg-slate-950 px-2 py-1" /></label>
                                <label>Configured price / bp<input type="number" min={0} step="0.01" value={plannerPricePerBp} onChange={(event) => { setPlannerPricePerBp(Number(event.target.value)); setDnaWeaverPlan(null); }} className="mt-1 w-full rounded border border-cyan-900 bg-slate-950 px-2 py-1" /></label>
                                <label>Configured lead time, days<input type="number" min={0} value={plannerLeadTimeDays} onChange={(event) => { setPlannerLeadTimeDays(Number(event.target.value)); setDnaWeaverPlan(null); }} className="mt-1 w-full rounded border border-cyan-900 bg-slate-950 px-2 py-1" /></label>
                            </div>
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <span className="text-cyan-200/70">Target: {sequenceData.name} • {sequenceData.sequence.length.toLocaleString()} bp • {sequenceData.circular ? 'circular' : 'linear'}</span>
                                <button type="button" onClick={() => void planDnaWeaverVendorFragments()} disabled={planning} className="rounded-lg bg-cyan-600 px-3 py-1.5 font-medium text-white hover:bg-cyan-500 disabled:opacity-50">
                                    {planning ? 'Planning vendor fragments…' : 'Plan vendor fragments'}
                                </button>
                            </div>
                            {dnaWeaverPlan && (
                                <div className={`space-y-2 rounded border p-2 ${dnaWeaverPlan.order_ready ? 'border-emerald-800 bg-emerald-950/30' : 'border-red-800 bg-red-950/30'}`}>
                                    <div className="font-medium">{dnaWeaverPlan.order_ready ? 'Order-ready under generic screening' : 'Blocked: do not order or save'} • {dnaWeaverPlan.ordered_fragments.length} fragments • {dnaWeaverPlan.pydna_exact_candidate_count} exact pydna candidate(s)</div>
                                    <div>DNA Weaver {dnaWeaverPlan.planner_version} → pydna {dnaWeaverPlan.validator_version} • price model {dnaWeaverPlan.estimated_price ?? 'unavailable'} • lead {dnaWeaverPlan.estimated_lead_time_days ?? 'unavailable'} days</div>
                                    <code className="block break-all text-[10px] text-cyan-200/60">Plan SHA-256: {dnaWeaverPlan.plan_checksum}</code>
                                    <div className="space-y-1">
                                        {dnaWeaverPlan.quality_checks.filter((check) => check.status !== 'pass').map((check) => <div key={check.check_id} className={check.status === 'blocker' ? 'text-red-300' : 'text-amber-300'}>{check.status.toUpperCase()}: {check.detail}</div>)}
                                    </div>
                                    <div className="flex gap-2">
                                        <button type="button" disabled={!dnaWeaverPlan.order_ready} onClick={() => downloadVendorOrder('fasta')} className="rounded bg-slate-800 px-2 py-1 disabled:opacity-40">Export order FASTA</button>
                                        <button type="button" disabled={!dnaWeaverPlan.order_ready} onClick={() => downloadVendorOrder('csv')} className="rounded bg-slate-800 px-2 py-1 disabled:opacity-40">Export order CSV</button>
                                    </div>
                                </div>
                            )}
                        </div>
                    ) : (
                        <p className="rounded-lg border border-cyan-900/60 bg-cyan-950/20 px-3 py-2 text-xs leading-5 text-cyan-100/80">
                            Standard workup: order complete, pre-overlapped DNA fragments externally and assemble them in-house. Paste exact purchased sequences here to validate every Gibson junction. Use the PCR route only when deliberately amplifying from physical template DNA.
                        </p>
                    )}
                </>
            )}

            {!(mode === 'gibson' && gibsonWorkflow === 'plan') && (
            <div className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                <div className="flex flex-wrap gap-2">
                    <button
                        type="button"
                        onClick={addWholeConstruct}
                        className="rounded-lg bg-slate-800 px-3 py-2 text-xs text-slate-200 transition-colors hover:bg-slate-700"
                    >
                        Add whole construct
                    </button>
                    <button
                        type="button"
                        onClick={addSelectionFragment}
                        disabled={!activeSelection}
                        className="rounded-lg bg-slate-800 px-3 py-2 text-xs text-slate-200 transition-colors hover:bg-slate-700 disabled:opacity-50"
                    >
                        Add selection
                    </button>
                </div>

                <div className="grid gap-2 sm:grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)_auto]">
                    <input
                        value={pasteName}
                        onChange={(event) => setPasteName(event.target.value)}
                        placeholder="Fragment name"
                        className="rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs"
                    />
                    <input
                        value={pasteSequence}
                        onChange={(event) => setPasteSequence(event.target.value)}
                        placeholder="Paste fragment sequence"
                        className="rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs font-mono"
                    />
                    <button
                        type="button"
                        onClick={addPastedFragment}
                        className="rounded-lg bg-cyan-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-cyan-500"
                    >
                        Add pasted
                    </button>
                </div>
            </div>
            )}

            {mode === 'gibson' && gibsonWorkflow === 'validate' && (
                <div className="grid gap-3 rounded-xl border border-slate-700 bg-slate-900/50 p-3 sm:grid-cols-3">
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Minimum overlap</span>
                        <input type="range" min={12} max={40} value={gibsonMinOverlap} onChange={(event) => setGibsonMinOverlap(Number(event.target.value))} className="w-full accent-violet-500" />
                        <div className="text-xs text-slate-400">{gibsonMinOverlap} nt</div>
                    </label>
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Preferred overlap</span>
                        <input type="range" min={16} max={60} value={gibsonPreferredOverlap} onChange={(event) => setGibsonPreferredOverlap(Number(event.target.value))} className="w-full accent-violet-500" />
                        <div className="text-xs text-slate-400">{gibsonPreferredOverlap} nt</div>
                    </label>
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Maximum overlap</span>
                        <input type="range" min={24} max={120} value={gibsonMaxOverlap} onChange={(event) => setGibsonMaxOverlap(Number(event.target.value))} className="w-full accent-violet-500" />
                        <div className="text-xs text-slate-400">{gibsonMaxOverlap} nt</div>
                    </label>
                </div>
            )}

            {mode === 'golden_gate' && (
                <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Type IIS enzyme</span>
                        <select
                            value={goldenGateEnzyme}
                            onChange={(event) => setGoldenGateEnzyme(event.target.value)}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5"
                        >
                            {(goldenGateOptions?.enzymes || []).map((enzyme) => (
                                <option key={enzyme.name} value={enzyme.name}>
                                    {enzyme.name} • {enzyme.site} • {enzyme.overhang_length} nt overhang
                                </option>
                            ))}
                        </select>
                    </label>
                </div>
            )}

            <div className="space-y-3">
                {fragments.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/40 px-3 py-4 text-xs text-slate-500">
                        No fragments in the basket yet.
                    </div>
                ) : fragments.map((fragment, index) => (
                    <div key={fragment.id} className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <div className="min-w-0">
                                <div className="font-medium text-slate-100">{fragment.name}</div>
                                <div className="mt-1 text-xs text-slate-500">
                                    {fragment.sequence.length.toLocaleString()} nt
                                    {fragment.role ? ` • ${fragment.role}` : ''}
                                    {fragment.source_name ? ` • ${fragment.source_name}` : ''}
                                </div>
                            </div>
                            <div className="flex items-center gap-1">
                                <button
                                    type="button"
                                    onClick={() => moveFragment(index, -1)}
                                    disabled={index === 0}
                                    className="rounded px-2 py-1 text-xs text-slate-400 hover:bg-slate-800 disabled:opacity-30"
                                    aria-label={`Move ${fragment.name} up`}
                                >
                                    Move up
                                </button>
                                <button
                                    type="button"
                                    onClick={() => moveFragment(index, 1)}
                                    disabled={index === fragments.length - 1}
                                    className="rounded px-2 py-1 text-xs text-slate-400 hover:bg-slate-800 disabled:opacity-30"
                                    aria-label={`Move ${fragment.name} down`}
                                >
                                    Move down
                                </button>
                                <button
                                    type="button"
                                    onClick={() => removeFragment(fragment.id)}
                                    className="rounded px-2 py-1 text-xs text-red-300 transition-colors hover:bg-red-500/10"
                                >
                                    Remove
                                </button>
                            </div>
                        </div>

                        {mode === 'gibson' && gibsonWorkflow === 'plan' && (
                            <details className="rounded border border-slate-800 bg-slate-950/60 p-2">
                                <summary className="cursor-pointer text-xs text-cyan-200">Exact purchase sequence and source-core interval</summary>
                                <div className="mt-2 text-[11px] text-slate-400">
                                    Core {String(fragment.metadata?.source_core_start ?? '?')}–{String(fragment.metadata?.source_core_end ?? '?')} • terminal overlap {String(fragment.metadata?.terminal_overlap_length ?? '?')} nt
                                </div>
                                <code className="mt-2 block max-h-32 overflow-auto break-all text-[10px] leading-4 text-slate-300">{fragment.sequence}</code>
                            </details>
                        )}

                        <div className="grid gap-2 sm:grid-cols-[minmax(0,0.8fr)_120px_120px]">
                            <input
                                value={fragment.name}
                                onChange={(event) => updateFragment(fragment.id, { name: event.target.value })}
                                className="rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs"
                            />
                            <select
                                value={fragment.role || (index === 0 ? 'vector' : 'insert')}
                                onChange={(event) => updateFragment(fragment.id, { role: event.target.value })}
                                className="rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs"
                            >
                                <option value="vector">Vector</option>
                                <option value="insert">Insert</option>
                                <option value="fragment">Fragment</option>
                            </select>
                            <select
                                value={fragment.orientation || 'forward'}
                                onChange={(event) => updateFragment(fragment.id, { orientation: event.target.value as 'forward' | 'reverse' })}
                                className="rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs"
                            >
                                <option value="forward">Forward</option>
                                <option value="reverse">Reverse</option>
                            </select>
                        </div>

                        {mode === 'gibson' && gibsonWorkflow === 'design' && (
                            <label className="block space-y-1">
                                <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Preparation</span>
                                <select
                                    value={gibsonPreparations[fragment.id] || 'pcr'}
                                    onChange={(event) => setGibsonPreparations((current) => ({
                                        ...current,
                                        [fragment.id]: event.target.value as 'pcr' | 'ready_linear',
                                    }))}
                                    className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs"
                                >
                                    <option value="pcr">PCR — design primers and overlap tails</option>
                                    <option value="ready_linear">Ready linear DNA — no primers</option>
                                </select>
                            </label>
                        )}

                        <div className="grid gap-3 sm:grid-cols-2">
                            <EndEditor
                                label="Left end"
                                value={fragment.left_end || undefined}
                                disabled={mode === 'gibson'}
                                onChange={(value) => updateFragment(fragment.id, { left_end: value })}
                            />
                            <EndEditor
                                label="Right end"
                                value={fragment.right_end || undefined}
                                disabled={mode === 'gibson'}
                                onChange={(value) => updateFragment(fragment.id, { right_end: value })}
                            />
                        </div>
                    </div>
                ))}
            </div>

            <div className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                <input
                    value={saveName}
                    onChange={(event) => setSaveName(event.target.value)}
                    placeholder="Product name"
                    className="w-full rounded border border-slate-600 bg-slate-800 px-3 py-2"
                />
                <textarea
                    value={saveDescription}
                    onChange={(event) => setSaveDescription(event.target.value)}
                    rows={2}
                    placeholder="Optional description or provenance notes"
                    className="w-full rounded border border-slate-600 bg-slate-800 px-3 py-2 text-xs"
                />
                {!(mode === 'gibson' && gibsonWorkflow === 'design') && (
                    <div className="flex gap-2">
                    <button
                        type="button"
                        onClick={() => void execute('simulate')}
                        disabled={loading !== null || planning}
                        className="flex-1 rounded-lg bg-violet-600 px-3 py-2 font-medium text-white transition-colors hover:bg-violet-500 disabled:opacity-50"
                    >
                        {mode === 'gibson' && gibsonWorkflow === 'plan'
                            ? (planning ? 'Planning…' : 'Re-plan and validate')
                            : (loading === 'simulate' ? 'Validating…' : 'Simulate')}
                    </button>
                    <button
                        type="button"
                        onClick={() => void execute('save')}
                        disabled={loading !== null || (mode === 'gibson' && gibsonWorkflow === 'plan' && !dnaWeaverPlan?.order_ready)}
                        className="flex-1 rounded-lg bg-emerald-600 px-3 py-2 font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-50"
                    >
                        {loading === 'save' ? 'Saving…' : mode === 'gibson' && gibsonWorkflow === 'plan' ? 'Regenerate + Verify + Save' : 'Validate + Save'}
                    </button>
                    </div>
                )}
            </div>

            {mode === 'gibson' && gibsonWorkflow === 'design' && (
                <GibsonDesignWorkspace
                    fragments={fragments}
                    preparations={gibsonPreparations}
                    saveName={saveName}
                    saveDescription={saveDescription}
                    sequenceName={sequenceData.name}
                    initialCircular={sequenceData.circular}
                    onLoadProduct={onLoadProduct}
                />
            )}

            {!(mode === 'gibson' && gibsonWorkflow === 'design') && error && (
                <div className="rounded border border-red-800 bg-red-900/30 px-3 py-2 text-sm text-red-300">
                    {error}
                </div>
            )}

            {!(mode === 'gibson' && gibsonWorkflow === 'design') && result && (
                <div className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                            <div className="font-medium text-slate-100">{result.message}</div>
                            <div className="mt-1 text-xs text-slate-500">
                                {result.product.length.toLocaleString()} nt • {result.product.circular ? 'circular' : 'linear'}
                            </div>
                        </div>
                        <button
                            type="button"
                            onClick={loadResult}
                            className="rounded-lg bg-cyan-600 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-cyan-500"
                        >
                            Load product
                        </button>
                    </div>

                    {result.product.validation_notes.length > 0 && (
                        <div className="rounded border border-emerald-900/50 bg-emerald-950/40 px-3 py-2 text-xs text-emerald-300">
                            {result.product.validation_notes.join(' • ')}
                        </div>
                    )}

                    {result.product.warnings.length > 0 && (
                        <div className="space-y-1 rounded border border-amber-900/50 bg-amber-950/40 px-3 py-2 text-xs text-amber-300">
                            {result.product.warnings.map((warning) => (
                                <div key={warning}>{warning}</div>
                            ))}
                        </div>
                    )}

                    <div className="space-y-2">
                        <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Junctions</div>
                        <div className="space-y-2">
                            {result.product.junctions.map((junction, index) => (
                                <div key={`${junction.left_fragment_id}:${junction.right_fragment_id}:${index}`} className="rounded border border-slate-800 bg-slate-950/70 px-3 py-2">
                                    <div className="font-medium text-slate-200">
                                        {junction.left_fragment_name} → {junction.right_fragment_name}
                                    </div>
                                    <div className="mt-1 text-xs text-slate-400">
                                        {junction.overlap_sequence
                                            ? `${junction.overlap_length} nt overlap`
                                            : junction.overhang_sequence
                                                ? `${junction.left_end_type}/${junction.right_end_type} • ${junction.overhang_sequence}`
                                                : 'Validated junction'}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
