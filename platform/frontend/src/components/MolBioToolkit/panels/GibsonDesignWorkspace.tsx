import { useEffect, useMemo, useRef, useState } from 'react';
import {
    designGibsonAssembly,
    saveDesignedGibsonAssembly,
    type AssemblyFragmentInput,
    type GibsonDesignRequest,
    type GibsonDesignResponse,
} from '../../../lib/api';
import type { SequenceData } from '../types';

interface GibsonDesignWorkspaceProps {
    fragments: AssemblyFragmentInput[];
    preparations: Record<string, 'pcr' | 'ready_linear'>;
    saveName: string;
    saveDescription: string;
    sequenceName: string;
    initialCircular: boolean;
    onLoadProduct: (sequenceData: SequenceData, savedSequenceId?: string | null) => void;
}

function errorMessage(error: unknown): string {
    const value = error as {
        response?: { data?: { detail?: unknown } };
        message?: string;
    };
    const detail = value?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        return detail.map((item) => {
            if (item && typeof item === 'object' && 'msg' in item) return String(item.msg);
            return JSON.stringify(item);
        }).join('; ');
    }
    return value?.message || 'Gibson design failed';
}

export function GibsonDesignWorkspace({
    fragments,
    preparations,
    saveName,
    saveDescription,
    sequenceName,
    initialCircular,
    onLoadProduct,
}: GibsonDesignWorkspaceProps) {
    const [circular, setCircular] = useState(initialCircular);
    const [overlap, setOverlap] = useState(30);
    const [targetTm, setTargetTm] = useState(60);
    const [minAnneal, setMinAnneal] = useState(13);
    const [result, setResult] = useState<GibsonDesignResponse | null>(null);
    const [loading, setLoading] = useState<'design' | 'save' | null>(null);
    const [error, setError] = useState<string | null>(null);
    const requestScopeRef = useRef('');

    const payload = useMemo<GibsonDesignRequest>(() => ({
        fragments: fragments.map((fragment) => ({
            ...fragment,
            preparation: preparations[fragment.id] || 'pcr',
        })),
        circular,
        overlap,
        target_tm: targetTm,
        min_anneal: minAnneal,
    }), [circular, fragments, minAnneal, overlap, preparations, targetTm]);
    const requestScope = JSON.stringify(payload);
    requestScopeRef.current = requestScope;

    useEffect(() => {
        setCircular(initialCircular);
    }, [initialCircular]);

    useEffect(() => {
        setResult(null);
        setError(null);
    }, [payload]);

    const runDesign = async () => {
        if (fragments.length < 2) {
            setError('Add at least two ordered fragments before designing.');
            return;
        }
        setLoading('design');
        setError(null);
        const scope = requestScope;
        try {
            const response = await designGibsonAssembly(payload);
            if (requestScopeRef.current === scope) setResult(response.data);
        } catch (runError: unknown) {
            if (requestScopeRef.current === scope) setError(errorMessage(runError));
        } finally {
            setLoading(null);
        }
    };

    const saveDesign = async () => {
        if (!result) return;
        setLoading('save');
        setError(null);
        const scope = requestScope;
        try {
            const response = await saveDesignedGibsonAssembly({
                ...payload,
                selected_candidate_checksum: result.selected_candidate_checksum,
                new_name: saveName.trim() || `${sequenceName} Gibson product`,
                save_description: saveDescription.trim() || undefined,
            });
            if (requestScopeRef.current === scope) setResult(response.data);
        } catch (saveError: unknown) {
            if (requestScopeRef.current === scope) setError(errorMessage(saveError));
        } finally {
            setLoading(null);
        }
    };

    const loadPreview = () => {
        if (!result) return;
        const saved = result.saved_sequence;
        const product = result.selected_product;
        onLoadProduct({
            name: saved?.name || saveName.trim() || `${sequenceName} Gibson preview`,
            description: saved?.description || saveDescription || 'pydna-designed Gibson assembly preview',
            sequence: product.sequence,
            circular: product.circular,
            sequenceType: 'dna',
            features: [],
            primers: result.primers.map((primer) => ({
                id: primer.id,
                name: `${primer.fragment_name} ${primer.direction}`,
                sequence: primer.full_sequence,
                sequenceType: 'dna' as const,
                start: 0,
                end: 0,
                strand: primer.direction === 'forward' ? 1 as const : -1 as const,
                tm: primer.tm,
                notes: {
                    fragment_id: primer.fragment_id,
                    annealing_sequence: primer.annealing_sequence,
                    tail_sequence: primer.tail_sequence,
                    warnings: primer.warnings,
                },
            })),
            translations: [],
            analysisTracks: [],
            parentId: saved?.parent_id ?? null,
            operation: saved?.operation ?? 'gibson',
            operationParams: saved?.operation_params ?? {
                engine: result.engine,
                engine_version: result.engine_version,
                candidate_checksum: result.selected_candidate_checksum,
                overlap: result.overlap,
                target_tm: result.target_tm,
                min_anneal: result.min_anneal,
                primers: result.primers,
            },
            version: saved?.version ?? 1,
        }, saved?.id || null);
    };

    return (
        <div className="space-y-3">
            <div className="grid gap-3 rounded-xl border border-violet-900/60 bg-violet-950/20 p-3 sm:grid-cols-4">
                <label className="space-y-1">
                    <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Product topology</span>
                    <select
                        value={circular ? 'circular' : 'linear'}
                        onChange={(event) => setCircular(event.target.value === 'circular')}
                        className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs"
                    >
                        <option value="circular">Circular plasmid</option>
                        <option value="linear">Linear product</option>
                    </select>
                </label>
                <label className="space-y-1">
                    <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Overlap</span>
                    <input
                        type="number"
                        min={15}
                        max={80}
                        value={overlap}
                        onChange={(event) => setOverlap(Number(event.target.value))}
                        className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs"
                    />
                    <div className="text-[11px] text-slate-500">15–80 nt</div>
                </label>
                <label className="space-y-1">
                    <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Target annealing Tm</span>
                    <input
                        type="number"
                        min={45}
                        max={72}
                        step={0.5}
                        value={targetTm}
                        onChange={(event) => setTargetTm(Number(event.target.value))}
                        className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs"
                    />
                    <div className="text-[11px] text-slate-500">°C</div>
                </label>
                <label className="space-y-1">
                    <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Minimum anneal</span>
                    <input
                        type="number"
                        min={10}
                        max={30}
                        value={minAnneal}
                        onChange={(event) => setMinAnneal(Number(event.target.value))}
                        className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs"
                    />
                    <div className="text-[11px] text-slate-500">nt</div>
                </label>
            </div>

            <button
                type="button"
                onClick={() => void runDesign()}
                disabled={loading !== null || fragments.length < 2}
                className="w-full rounded-lg bg-violet-600 px-3 py-2 font-medium text-white transition-colors hover:bg-violet-500 disabled:opacity-50"
            >
                {loading === 'design' ? 'Designing…' : 'Design & Simulate'}
            </button>

            {error && (
                <div className="rounded border border-red-800 bg-red-900/30 px-3 py-2 text-sm text-red-300">
                    {error}
                </div>
            )}

            {result && (
                <div className="space-y-4 rounded-xl border border-slate-700 bg-slate-900/60 p-3">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                            <div className="font-medium text-slate-100">Exact intended product selected</div>
                            <div className="mt-1 text-xs text-slate-400">
                                {result.selected_product.length.toLocaleString()} nt • {result.circular ? 'circular' : 'linear'} • {result.engine} {result.engine_version}
                            </div>
                            <div className="mt-1 text-[11px] text-slate-500">
                                {result.candidates.length} unique candidate{result.candidates.length === 1 ? '' : 's'} • checksum {result.selected_candidate_checksum.slice(0, 12)}…
                            </div>
                        </div>
                        <button
                            type="button"
                            onClick={loadPreview}
                            className="rounded-lg bg-cyan-600 px-3 py-2 text-xs font-medium text-white hover:bg-cyan-500"
                        >
                            Load preview
                        </button>
                    </div>

                    {result.warnings.length > 0 && (
                        <div className="space-y-1 rounded border border-amber-900/50 bg-amber-950/40 px-3 py-2 text-xs text-amber-300">
                            {result.warnings.map((warning) => <div key={warning}>{warning}</div>)}
                        </div>
                    )}

                    <div className="space-y-2">
                        <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Generated primers</div>
                        {result.primers.length === 0 ? (
                            <div className="text-xs text-slate-500">No PCR primers were generated for ready-linear inputs.</div>
                        ) : (
                            <div className="grid gap-2 lg:grid-cols-2">
                                {result.primers.map((primer) => (
                                    <div key={primer.id} className="rounded border border-slate-800 bg-slate-950/70 p-3">
                                        <div className="flex items-center justify-between gap-2">
                                            <div className="font-medium text-slate-200">{primer.fragment_name} {primer.direction === 'forward' ? 'F' : 'R'}</div>
                                            <div className="text-xs text-slate-500">Tm {primer.tm.toFixed(1)} °C</div>
                                        </div>
                                        <div className="mt-2 break-all font-mono text-xs text-cyan-300">{primer.full_sequence}</div>
                                        <div className="mt-2 grid gap-1 text-[11px] sm:grid-cols-2">
                                            <div><span className="text-slate-500">Tail:</span> <span className="break-all font-mono text-violet-300">{primer.tail_sequence || '—'}</span></div>
                                            <div><span className="text-slate-500">Anneal:</span> <span className="break-all font-mono text-emerald-300">{primer.annealing_sequence}</span></div>
                                        </div>
                                        {primer.warnings.length > 0 && (
                                            <div className="mt-2 text-[11px] text-amber-300">{primer.warnings.join(' • ')}</div>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    <div className="space-y-2">
                        <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Validated junctions</div>
                        {result.selected_product.junctions.map((junction, index) => (
                            <div key={`${junction.left_fragment_id}:${junction.right_fragment_id}:${index}`} className="rounded border border-slate-800 bg-slate-950/70 px-3 py-2">
                                <div className="text-xs font-medium text-slate-200">{junction.left_fragment_name} → {junction.right_fragment_name}</div>
                                <div className="mt-1 text-xs text-slate-400">{junction.overlap_length} nt overlap</div>
                                <div className="mt-1 break-all font-mono text-[11px] text-slate-500">{junction.overlap_sequence}</div>
                            </div>
                        ))}
                    </div>

                    <button
                        type="button"
                        onClick={() => void saveDesign()}
                        disabled={loading !== null || Boolean(result.saved_sequence)}
                        className="w-full rounded-lg bg-emerald-600 px-3 py-2 font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
                    >
                        {result.saved_sequence
                            ? `Saved as ${result.saved_sequence.name}`
                            : loading === 'save'
                                ? 'Saving…'
                                : 'Save as new construct'}
                    </button>
                </div>
            )}
        </div>
    );
}
