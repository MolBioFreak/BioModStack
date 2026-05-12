import { useEffect, useMemo, useState } from 'react';
import {
    fetchGoldenGateAssemblyOptions,
    saveGibsonAssembly,
    saveGoldenGateAssembly,
    saveLigationAssembly,
    simulateGibsonAssembly,
    simulateGoldenGateAssembly,
    simulateLigationAssembly,
    type AssemblyFragmentEnd,
    type AssemblyFragmentInput,
    type AssemblyOperationResponse,
    type GoldenGateAssemblyOptionsResponse,
} from '../../../lib/api';
import type { SequenceData, SelectionInfo } from '../types';

interface AssemblyPanelProps {
    sequenceData: SequenceData;
    selection: SelectionInfo | null;
    selectedSequenceId: string | null;
    onLoadProduct: (sequenceData: SequenceData, savedSequenceId?: string | null) => void;
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

export function AssemblyPanel({
    sequenceData,
    selection,
    selectedSequenceId,
    onLoadProduct,
}: AssemblyPanelProps) {
    const [mode, setMode] = useState<AssemblyMode>('ligation');
    const [fragments, setFragments] = useState<AssemblyFragmentInput[]>([]);
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
    const [error, setError] = useState<string | null>(null);

    const activeSelection = useMemo(() => selectionSequence(sequenceData, selection), [sequenceData, selection]);

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
    };

    const removeFragment = (fragmentId: string) => {
        setFragments((current) => current.filter((fragment) => fragment.id !== fragmentId));
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

    const execute = async (action: 'simulate' | 'save') => {
        if (fragments.length === 0) {
            setError('Add at least one fragment to the basket.');
            return;
        }
        setLoading(action);
        setError(null);
        try {
            let response;
            if (mode === 'ligation') {
                const payload = {
                    fragments,
                    circular: true,
                    new_name: saveName || undefined,
                    save_description: saveDescription || undefined,
                };
                response = action === 'save'
                    ? await saveLigationAssembly(payload)
                    : await simulateLigationAssembly(payload);
            } else if (mode === 'gibson') {
                const payload = {
                    fragments,
                    circular: true,
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
                    circular: true,
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

            {mode === 'gibson' && (
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
                            <button
                                type="button"
                                onClick={() => removeFragment(fragment.id)}
                                className="rounded px-2 py-1 text-xs text-red-300 transition-colors hover:bg-red-500/10"
                            >
                                Remove
                            </button>
                        </div>

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
                <div className="flex gap-2">
                    <button
                        type="button"
                        onClick={() => void execute('simulate')}
                        disabled={loading !== null}
                        className="flex-1 rounded-lg bg-violet-600 px-3 py-2 font-medium text-white transition-colors hover:bg-violet-500 disabled:opacity-50"
                    >
                        {loading === 'simulate' ? 'Validating…' : 'Simulate'}
                    </button>
                    <button
                        type="button"
                        onClick={() => void execute('save')}
                        disabled={loading !== null}
                        className="flex-1 rounded-lg bg-emerald-600 px-3 py-2 font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-50"
                    >
                        {loading === 'save' ? 'Saving…' : 'Validate + Save'}
                    </button>
                </div>
            </div>

            {error && (
                <div className="rounded border border-red-800 bg-red-900/30 px-3 py-2 text-sm text-red-300">
                    {error}
                </div>
            )}

            {result && (
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
