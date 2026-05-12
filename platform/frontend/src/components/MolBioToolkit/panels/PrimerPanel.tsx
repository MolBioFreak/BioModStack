/**
 * PrimerPanel - Primer design and management with library integration
 */

import { useState, useMemo, useEffect, useCallback } from 'react';
import type { SequenceData, Primer, HighlightedRegion, SelectionInfo } from '../types';
import {
    fetchPrimers,
    createPrimer,
    deletePrimer as deletePrimerApi,
    togglePrimerFavorite,
    calculatePrimerTm,
    calculatePrimerQc,
    designPrimers,
    type Primer as LibraryPrimer,
    type PrimerCreate,
    type PrimerDesignResponse,
    type PrimerQcResponse,
    type PrimerTmOptionsResponse,
    type PrimerTmResult,
    type PrimerTmSettings,
} from '../../../lib/api';
import { PrimerTmSettingsPanel } from '../PrimerTmSettingsPanel';
import {
    calculateGcPercent,
    cleanNucleotideSequence,
    inferSequenceTypeFromSequence,
    isValidNucleotideSequence,
    resolvePrimerBindings,
    reverseComplementSequence,
    sequenceUnitLabel,
} from '../utils/nucleotides';

interface PrimerPanelProps {
    sequenceData: SequenceData;
    selection: SelectionInfo | null;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onAddPrimer: (primer: Primer) => void;
    onRemovePrimer: (primerId: string) => void;
    tmOptions: PrimerTmOptionsResponse | null;
    tmSettings: PrimerTmSettings;
    onTmSettingsChange: (settings: PrimerTmSettings) => void;
}

function formatTm(result: PrimerTmResult | null | undefined): string {
    if (!result || result.tm === null || Number.isNaN(result.tm)) {
        return 'n/a';
    }
    return `${result.tm.toFixed(1)}°C`;
}

function SliderField({
    label,
    value,
    min,
    max,
    step = 1,
    onChange,
    format = (current: number) => String(current),
}: {
    label: string;
    value: number;
    min: number;
    max: number;
    step?: number;
    onChange: (value: number) => void;
    format?: (value: number) => string;
}) {
    return (
        <label className="space-y-1.5">
            <div className="flex items-center justify-between gap-3 text-[11px] uppercase tracking-[0.12em] text-slate-500">
                <span>{label}</span>
                <span className="text-slate-300">{format(value)}</span>
            </div>
            <input
                type="range"
                min={min}
                max={max}
                step={step}
                value={value}
                onChange={(event) => onChange(Number(event.target.value))}
                className="w-full accent-violet-500"
            />
            <div className="flex items-center justify-between text-[10px] text-slate-600">
                <span>{format(min)}</span>
                <span>{format(max)}</span>
            </div>
        </label>
    );
}

export function PrimerPanel({
    sequenceData,
    selection,
    onHighlight,
    onAddPrimer,
    onRemovePrimer,
    tmOptions,
    tmSettings,
    onTmSettingsChange,
}: PrimerPanelProps) {
    const [newPrimerName, setNewPrimerName] = useState('');
    const [newPrimerSeq, setNewPrimerSeq] = useState('');
    const [isReverse, setIsReverse] = useState(false);
    const [hoveredPrimerId, setHoveredPrimerId] = useState<string | null>(null);
    const [activeTab, setActiveTab] = useState<'sequence' | 'design' | 'qc' | 'library'>('sequence');

    const [libraryPrimers, setLibraryPrimers] = useState<LibraryPrimer[]>([]);
    const [libraryLoading, setLibraryLoading] = useState(false);
    const [librarySearch, setLibrarySearch] = useState('');
    const [showFavoritesOnly, setShowFavoritesOnly] = useState(false);
    const [saveToLibrary, setSaveToLibrary] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [draftTmResult, setDraftTmResult] = useState<PrimerTmResult | null>(null);
    const [draftTmLoading, setDraftTmLoading] = useState(false);
    const [designTargetStart, setDesignTargetStart] = useState(1);
    const [designTargetEnd, setDesignTargetEnd] = useState(Math.min(sequenceData.sequence.length, 500));
    const [designPrimerMinLength, setDesignPrimerMinLength] = useState(20);
    const [designPrimerMaxLength, setDesignPrimerMaxLength] = useState(26);
    const [designProductMinLength, setDesignProductMinLength] = useState(120);
    const [designProductMaxLength, setDesignProductMaxLength] = useState(1200);
    const [designGcMin, setDesignGcMin] = useState(35);
    const [designGcMax, setDesignGcMax] = useState(65);
    const [designTargetTm, setDesignTargetTm] = useState(62);
    const [designTmDelta, setDesignTmDelta] = useState(3);
    const [designGcClampMin, setDesignGcClampMin] = useState(1);
    const [designMaxPolyX, setDesignMaxPolyX] = useState(4);
    const [designFlankSearch, setDesignFlankSearch] = useState(80);
    const [designMaxPairs, setDesignMaxPairs] = useState(8);
    const [designOverhangForward, setDesignOverhangForward] = useState('');
    const [designOverhangReverse, setDesignOverhangReverse] = useState('');
    const [designResult, setDesignResult] = useState<PrimerDesignResponse | null>(null);
    const [designLoading, setDesignLoading] = useState(false);
    const [draftQc, setDraftQc] = useState<PrimerQcResponse['primers'][number]['qc'] | null>(null);
    const [sequenceQc, setSequenceQc] = useState<PrimerQcResponse | null>(null);
    const [qcLoading, setQcLoading] = useState(false);

    const sequenceType = sequenceData.sequenceType === 'rna' ? 'rna' : 'dna';
    const unitLabel = sequenceUnitLabel(sequenceType);

    const selectedRegion = useMemo(() => {
        if (!selection || selection.start === selection.end) return null;
        const start = Math.min(selection.start, selection.end);
        const end = Math.max(selection.start, selection.end);
        const seq = sequenceData.sequence.slice(start, end);
        return { start, end, sequence: seq, length: seq.length };
    }, [selection, sequenceData.sequence]);

    const cleanedDraftPrimer = useMemo(() => cleanNucleotideSequence(newPrimerSeq), [newPrimerSeq]);
    const draftBindings = useMemo(() => resolvePrimerBindings(sequenceData.sequence, cleanedDraftPrimer, {
        reverse: isReverse,
        sequenceType,
        circular: sequenceData.circular,
    }), [cleanedDraftPrimer, isReverse, sequenceData.circular, sequenceData.sequence, sequenceType]);
    const draftBinding = draftBindings[0] ?? null;
    const draftTmSequence = useMemo(() => {
        if (!cleanedDraftPrimer) {
            return '';
        }
        if (!draftBinding) {
            return cleanedDraftPrimer;
        }
        return cleanedDraftPrimer.slice(cleanedDraftPrimer.length - draftBinding.annealLength);
    }, [cleanedDraftPrimer, draftBinding]);

    const loadLibrary = useCallback(async () => {
        setLibraryLoading(true);
        try {
            const response = await fetchPrimers({
                search: librarySearch || undefined,
                favorites_only: showFavoritesOnly,
            });
            setLibraryPrimers(response.data);
        } catch (loadError) {
            console.error('Failed to load primer library:', loadError);
        } finally {
            setLibraryLoading(false);
        }
    }, [librarySearch, showFavoritesOnly]);

    useEffect(() => {
        if (activeTab === 'library') {
            loadLibrary();
        }
    }, [activeTab, loadLibrary]);

    useEffect(() => {
        const sequenceLength = sequenceData.sequence.length;
        if (sequenceLength === 0) {
            setDesignTargetStart(1);
            setDesignTargetEnd(1);
            return;
        }
        if (selection && selection.start !== selection.end) {
            const start = Math.min(selection.start, selection.end) + 1;
            const end = Math.max(selection.start, selection.end);
            setDesignTargetStart(start);
            setDesignTargetEnd(end);
            setDesignProductMinLength((current) => Math.max(current, end - start + 40));
        } else {
            setDesignTargetStart((current) => Math.max(1, Math.min(current, sequenceLength)));
            setDesignTargetEnd((current) => Math.max(1, Math.min(current, sequenceLength)));
        }
        setDesignResult(null);
    }, [selection, sequenceData.sequence.length]);

    const calculateTmForSequence = useCallback(async (
        sequence: string,
        explicitSequenceType?: 'dna' | 'rna',
    ): Promise<PrimerTmResult | null> => {
        const cleaned = cleanNucleotideSequence(sequence);
        if (!cleaned) {
            return null;
        }
        try {
            const response = await calculatePrimerTm({
                primers: [{
                    sequence: cleaned,
                    sequence_type: explicitSequenceType || inferSequenceTypeFromSequence(cleaned),
                }],
                settings: tmSettings,
            });
            return response.data[0] ?? null;
        } catch (tmError) {
            console.error('Failed to calculate primer Tm:', tmError);
            return null;
        }
    }, [tmSettings]);

    useEffect(() => {
        if (!cleanedDraftPrimer || !isValidNucleotideSequence(cleanedDraftPrimer)) {
            setDraftTmResult(null);
            setDraftQc(null);
            setDraftTmLoading(false);
            return;
        }

        let cancelled = false;
        setDraftTmLoading(true);
        const timer = window.setTimeout(async () => {
            const [tmResult, qcResult] = await Promise.all([
                calculateTmForSequence(draftTmSequence, inferSequenceTypeFromSequence(draftTmSequence)),
                calculatePrimerQc({
                    primers: [{
                        sequence: cleanedDraftPrimer,
                        sequence_type: inferSequenceTypeFromSequence(cleanedDraftPrimer),
                    }],
                    template_sequence: sequenceData.sequence,
                    template_sequence_type: sequenceType,
                    template_is_circular: sequenceData.circular,
                    include_pairwise: false,
                }).then((response) => response.data).catch(() => null),
            ]);
            if (!cancelled) {
                setDraftTmResult(tmResult);
                setDraftQc(qcResult?.primers[0]?.qc || null);
                setDraftTmLoading(false);
            }
        }, 250);

        return () => {
            cancelled = true;
            window.clearTimeout(timer);
        };
    }, [calculateTmForSequence, cleanedDraftPrimer, draftTmSequence, sequenceData.circular, sequenceData.sequence, sequenceType]);

    useEffect(() => {
        if (activeTab !== 'qc') {
            return;
        }
        const primers = sequenceData.primers || [];
        if (primers.length === 0) {
            setSequenceQc({ primers: [], pairwise: [] });
            return;
        }

        let cancelled = false;
        setQcLoading(true);
        void calculatePrimerQc({
            primers: primers.map((primer) => ({
                id: primer.id,
                name: primer.name,
                sequence: primer.sequence,
                sequence_type: primer.sequenceType || inferSequenceTypeFromSequence(primer.sequence),
            })),
            template_sequence: sequenceData.sequence,
            template_sequence_type: sequenceType,
            template_is_circular: sequenceData.circular,
            include_pairwise: true,
        }).then((response) => {
            if (!cancelled) {
                setSequenceQc(response.data);
            }
        }).catch((qcError) => {
            console.error('Failed to calculate primer QC:', qcError);
        }).finally(() => {
            if (!cancelled) {
                setQcLoading(false);
            }
        });

        return () => {
            cancelled = true;
        };
    }, [activeTab, sequenceData.circular, sequenceData.primers, sequenceData.sequence, sequenceType]);

    const handleSelectionAsPrimer = (reverse: boolean) => {
        if (!selectedRegion) return;
        setError(null);
        const seq = reverse
            ? reverseComplementSequence(selectedRegion.sequence, sequenceType)
            : selectedRegion.sequence;
        setNewPrimerSeq(seq);
        setIsReverse(reverse);
        setNewPrimerName(`Primer_${reverse ? 'Rev' : 'Fwd'}_${selectedRegion.start + 1}`);
    };

    const addPrimer = async () => {
        const cleanedPrimer = cleanNucleotideSequence(newPrimerSeq);
        if (!cleanedPrimer || cleanedPrimer.length < 10) return;
        if (!isValidNucleotideSequence(newPrimerSeq)) {
            setError('Primer contains invalid nucleotide characters.');
            return;
        }
        setError(null);

        const binding = draftBinding;
        if (!binding) {
            setError('No primer annealing site was found on the current construct. Tailed primers are supported, but the 3′ annealing region must match.');
            return;
        }

        const sequenceTypeForPrimer = inferSequenceTypeFromSequence(cleanedPrimer);
        const effectiveTmResult = draftTmResult ?? await calculateTmForSequence(
            cleanedPrimer.slice(cleanedPrimer.length - binding.annealLength),
            inferSequenceTypeFromSequence(cleanedPrimer.slice(cleanedPrimer.length - binding.annealLength)),
        );

        const primer: Primer = {
            id: `primer_${Date.now()}`,
            name: newPrimerName || `Primer_${(sequenceData.primers?.length || 0) + 1}`,
            sequence: cleanedPrimer,
            sequenceType: sequenceTypeForPrimer,
            start: binding.start,
            end: binding.end,
            strand: isReverse ? -1 : 1,
            tm: effectiveTmResult?.tm ?? undefined,
            gc_percent: effectiveTmResult?.gc_percent ?? calculateGcPercent(cleanedPrimer),
            tm_algorithm: effectiveTmResult?.algorithm,
            tm_salt_correction: effectiveTmResult?.salt_correction,
            tm_settings: tmSettings,
        };

        onAddPrimer(primer);

        if (saveToLibrary) {
            try {
                const libraryData: PrimerCreate = {
                    name: primer.name,
                    sequence: primer.sequence,
                    sequence_type: sequenceTypeForPrimer,
                    primer_type: isReverse ? 'reverse' : 'forward',
                    binding_start: primer.start,
                    binding_end: primer.end,
                    binding_strand: primer.strand,
                    tm_settings: tmSettings,
                };
                await createPrimer(libraryData);
            } catch (createError) {
                console.error('Failed to save primer to library:', createError);
            }
        }

        setNewPrimerName('');
        setNewPrimerSeq('');
        setIsReverse(false);
        setDraftTmResult(null);
    };

    const highlightPrimer = (primer: Primer | null) => {
        if (!primer) {
            const regions: HighlightedRegion[] = (sequenceData.primers || []).map((existingPrimer) => ({
                start: existingPrimer.start,
                end: existingPrimer.end,
                color: existingPrimer.strand === 1 ? '#22c55e' : '#ef4444',
                label: existingPrimer.name,
            }));
            onHighlight(regions);
            return;
        }
        onHighlight([{
            start: primer.start,
            end: primer.end,
            color: primer.strand === 1 ? '#22c55e' : '#ef4444',
            label: primer.name,
        }]);
    };

    const addLibraryPrimerToSequence = async (libPrimer: LibraryPrimer) => {
        const bindings = resolvePrimerBindings(sequenceData.sequence, libPrimer.sequence, {
            reverse: libPrimer.binding_strand === -1,
            sequenceType,
            circular: sequenceData.circular,
        });
        const binding = bindings[0];
        if (!binding) {
            setError(`Primer "${libPrimer.name}" does not anneal to the current construct.`);
            return;
        }
        setError(null);

        const annealSequence = libPrimer.sequence.slice(libPrimer.sequence.length - binding.annealLength);
        const liveTmResult = await calculateTmForSequence(
            annealSequence,
            inferSequenceTypeFromSequence(annealSequence),
        );

        const primer: Primer = {
            id: `primer_${Date.now()}`,
            name: libPrimer.name,
            sequence: libPrimer.sequence,
            sequenceType: libPrimer.sequence_type,
            start: binding.start,
            end: binding.end,
            strand: (libPrimer.binding_strand === -1 ? -1 : 1) as 1 | -1,
            tm: liveTmResult?.tm ?? libPrimer.tm ?? undefined,
            gc_percent: liveTmResult?.gc_percent ?? libPrimer.gc_percent ?? undefined,
            tm_algorithm: liveTmResult?.algorithm ?? libPrimer.tm_algorithm ?? undefined,
            tm_salt_correction: liveTmResult?.salt_correction ?? libPrimer.tm_salt_correction ?? undefined,
            tm_settings: tmSettings,
        };

        onAddPrimer(primer);
    };

    const useSelectionAsDesignTarget = () => {
        if (!selectedRegion) return;
        setDesignTargetStart(selectedRegion.start + 1);
        setDesignTargetEnd(selectedRegion.end);
        setDesignProductMinLength(Math.max(selectedRegion.length + 40, 120));
        setDesignProductMaxLength(Math.max(selectedRegion.length + 400, 600));
        setDesignResult(null);
    };

    const runPrimerDesign = async () => {
        if (!sequenceData.sequence) return;
        setDesignLoading(true);
        setError(null);
        try {
            const response = await designPrimers({
                name: sequenceData.name,
                sequence: sequenceData.sequence,
                sequence_type: sequenceType,
                is_circular: sequenceData.circular,
                target_start: Math.max(0, designTargetStart - 1),
                target_end: Math.max(designTargetStart, designTargetEnd),
                primer_min_length: designPrimerMinLength,
                primer_max_length: designPrimerMaxLength,
                product_min_length: designProductMinLength,
                product_max_length: designProductMaxLength,
                flank_search_span: designFlankSearch,
                gc_min_percent: designGcMin,
                gc_max_percent: designGcMax,
                tm_target_c: designTargetTm,
                tm_max_delta_c: designTmDelta,
                gc_clamp_min: designGcClampMin,
                max_poly_x: designMaxPolyX,
                max_pairs: designMaxPairs,
                overhang_forward: designOverhangForward,
                overhang_reverse: designOverhangReverse,
                tm_settings: tmSettings,
            });
            setDesignResult(response.data);
        } catch (designError) {
            setError(designError instanceof Error ? designError.message : 'Primer design failed');
        } finally {
            setDesignLoading(false);
        }
    };

    const addDesignedPair = (pair: NonNullable<PrimerDesignResponse['pairs']>[number]) => {
        const prefix = sequenceData.name.replace(/\s+/g, '_');
        const forwardPrimer: Primer = {
            id: `primer_${Date.now().toString(36)}_f_${pair.rank}`,
            name: `${prefix}_F${pair.rank}`,
            sequence: pair.forward.sequence,
            sequenceType,
            start: pair.forward.start,
            end: pair.forward.end,
            strand: 1,
            tm: pair.forward.tm,
            gc_percent: pair.forward.gc_percent,
            tm_algorithm: tmSettings.algorithm,
            tm_salt_correction: tmSettings.salt_correction,
            tm_settings: tmSettings,
        };
        const reversePrimer: Primer = {
            id: `primer_${Date.now().toString(36)}_r_${pair.rank}`,
            name: `${prefix}_R${pair.rank}`,
            sequence: pair.reverse.sequence,
            sequenceType,
            start: pair.reverse.start,
            end: pair.reverse.end,
            strand: -1,
            tm: pair.reverse.tm,
            gc_percent: pair.reverse.gc_percent,
            tm_algorithm: tmSettings.algorithm,
            tm_salt_correction: tmSettings.salt_correction,
            tm_settings: tmSettings,
        };
        onAddPrimer(forwardPrimer);
        onAddPrimer(reversePrimer);
    };

    const handleToggleFavorite = async (primerId: string) => {
        try {
            await togglePrimerFavorite(primerId);
            loadLibrary();
        } catch (toggleError) {
            console.error('Failed to toggle favorite:', toggleError);
        }
    };

    const handleDeleteFromLibrary = async (primerId: string) => {
        try {
            await deletePrimerApi(primerId);
            loadLibrary();
        } catch (deleteError) {
            console.error('Failed to delete primer:', deleteError);
        }
    };

    const primers = sequenceData.primers || [];
    const draftTmWarnings = draftTmResult?.warnings || [];
    const draftTmLabel = draftBinding && draftBinding.overhangLength > 0 ? 'Annealing Tm' : 'Tm';
    const sequenceLength = Math.max(1, sequenceData.sequence.length);
    const productSliderMax = Math.max(designProductMinLength, sequenceLength);

    return (
        <div className="primer-panel p-3 space-y-3">
            <h4 className="font-semibold text-slate-200">Primers</h4>

            <div className="flex gap-1 text-xs">
                <button
                    onClick={() => setActiveTab('sequence')}
                    className={`px-3 py-1.5 rounded transition-colors ${activeTab === 'sequence'
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-700 hover:bg-slate-600 text-slate-300'
                        }`}
                >
                    Sequence ({primers.length})
                </button>
                <button
                    onClick={() => setActiveTab('design')}
                    className={`px-3 py-1.5 rounded transition-colors ${activeTab === 'design'
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-700 hover:bg-slate-600 text-slate-300'
                        }`}
                >
                    Design
                </button>
                <button
                    onClick={() => setActiveTab('qc')}
                    className={`px-3 py-1.5 rounded transition-colors ${activeTab === 'qc'
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-700 hover:bg-slate-600 text-slate-300'
                        }`}
                >
                    QC
                </button>
                <button
                    onClick={() => setActiveTab('library')}
                    className={`px-3 py-1.5 rounded transition-colors ${activeTab === 'library'
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-700 hover:bg-slate-600 text-slate-300'
                        }`}
                >
                    Library
                </button>
            </div>

            {error && (
                <div className="rounded border border-red-800 bg-red-900/40 px-3 py-2 text-sm text-red-300">
                    {error}
                </div>
            )}

            <PrimerTmSettingsPanel
                sequenceType={sequenceType}
                options={tmOptions}
                settings={tmSettings}
                onChange={onTmSettingsChange}
            />

            {activeTab === 'sequence' && (
                <>
                    {selectedRegion && (
                        <div className="p-3 bg-slate-700/50 rounded space-y-2">
                            <div className="text-sm text-slate-300">
                                Selected: {selectedRegion.start + 1}–{selectedRegion.end} ({selectedRegion.length} {unitLabel})
                            </div>
                            <div className="font-mono text-xs text-slate-400 truncate">
                                {selectedRegion.sequence.slice(0, 50)}{selectedRegion.length > 50 ? '...' : ''}
                            </div>
                            <div className="flex gap-2">
                                <button
                                    onClick={() => handleSelectionAsPrimer(false)}
                                    className="flex-1 px-2 py-1 bg-emerald-700 hover:bg-emerald-600 rounded text-xs transition-colors"
                                >
                                    Use as Forward
                                </button>
                                <button
                                    onClick={() => handleSelectionAsPrimer(true)}
                                    className="flex-1 px-2 py-1 bg-red-700 hover:bg-red-600 rounded text-xs transition-colors"
                                >
                                    Use as Reverse
                                </button>
                            </div>
                        </div>
                    )}

                    <div className="space-y-2 p-3 bg-slate-800 rounded border border-slate-700">
                        <div className="text-sm font-medium text-slate-300">Add Primer</div>

                        <input
                            type="text"
                            value={newPrimerName}
                            onChange={(event) => setNewPrimerName(event.target.value)}
                            placeholder="Primer name"
                            className="w-full px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm focus:border-blue-500 focus:outline-none"
                        />

                        <input
                            type="text"
                            value={newPrimerSeq}
                            onChange={(event) => setNewPrimerSeq(event.target.value.toUpperCase())}
                            placeholder="Sequence (5'→3')"
                            className="w-full px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm font-mono focus:border-blue-500 focus:outline-none"
                        />

                        <div className="flex items-center gap-4 flex-wrap">
                            <label className="flex items-center gap-1 text-sm text-slate-400">
                                <input
                                    type="checkbox"
                                    checked={isReverse}
                                    onChange={(event) => setIsReverse(event.target.checked)}
                                    className="w-3 h-3"
                                />
                                Reverse
                            </label>

                            <label className="flex items-center gap-1 text-sm text-slate-400">
                                <input
                                    type="checkbox"
                                    checked={saveToLibrary}
                                    onChange={(event) => setSaveToLibrary(event.target.checked)}
                                    className="w-3 h-3"
                                />
                                Save to library
                            </label>
                        </div>

                        {newPrimerSeq && (
                            <div className="rounded border border-slate-700 bg-slate-900/40 px-3 py-2 text-xs text-slate-400 space-y-1">
                                <div className="flex flex-wrap items-center gap-3">
                                    <span>{cleanedDraftPrimer.length} {sequenceUnitLabel(inferSequenceTypeFromSequence(cleanedDraftPrimer || 'A'))}</span>
                                    <span className="text-emerald-300">
                                        {draftTmLabel}: {draftTmLoading ? 'Calculating...' : formatTm(draftTmResult)}
                                    </span>
                                    <span>GC: {calculateGcPercent(cleanedDraftPrimer)}%</span>
                                </div>
                                {draftBinding ? (
                                    <div className="text-emerald-400">
                                        Anneals @ {draftBinding.start + 1}
                                        {draftBinding.overhangLength > 0 ? ` with ${draftBinding.overhangLength} ${unitLabel} 5′ overhang` : ''}
                                    </div>
                                ) : (
                                    <div className="text-yellow-300">
                                        No annealing site detected on the current construct.
                                    </div>
                                )}
                                {draftTmWarnings.map((warning) => (
                                    <div key={warning} className="text-yellow-300">
                                        {warning}
                                    </div>
                                ))}
                                {draftQc && (
                                    <div className="border-t border-slate-800 pt-2 text-slate-400">
                                        QC • self {draftQc.max_self_complement} • 3′ self {draftQc.three_prime_self_complement} • hairpin {draftQc.max_hairpin_stem}
                                        {draftQc.binding_site_count != null ? ` • sites ${draftQc.binding_site_count}` : ''}
                                    </div>
                                )}
                                {draftQc?.warnings.map((warning) => (
                                    <div key={warning} className="text-amber-300">
                                        {warning}
                                    </div>
                                ))}
                            </div>
                        )}

                        <button
                            onClick={addPrimer}
                            disabled={!newPrimerSeq || newPrimerSeq.length < 10}
                            className="w-full py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-sm transition-colors"
                        >
                            Add Primer
                        </button>
                    </div>

                    <div className="space-y-1">
                        <div className="flex items-center justify-between text-sm text-slate-400 mb-2">
                            <span>Sequence Primers ({primers.length})</span>
                            <button
                                onClick={() => highlightPrimer(null)}
                                className="text-xs text-blue-400 hover:text-blue-300"
                            >
                                Show all
                            </button>
                        </div>

                        {primers.length === 0 ? (
                            <div className="text-center text-slate-500 text-sm py-4">
                                No primers added yet
                            </div>
                        ) : (
                            <div className="space-y-1 max-h-48 overflow-y-auto">
                                {primers.map((primer) => (
                                    <div
                                        key={primer.id}
                                        className={`flex items-center justify-between p-2 rounded transition-colors ${hoveredPrimerId === primer.id ? 'bg-slate-600' : 'bg-slate-700/50'
                                            }`}
                                        onMouseEnter={() => {
                                            setHoveredPrimerId(primer.id);
                                            highlightPrimer(primer);
                                        }}
                                        onMouseLeave={() => {
                                            setHoveredPrimerId(null);
                                            highlightPrimer(null);
                                        }}
                                    >
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2">
                                                <span className={`w-2 h-2 rounded-full ${primer.strand === 1 ? 'bg-emerald-500' : 'bg-red-500'}`} />
                                                <span className="text-sm text-slate-200 truncate">{primer.name}</span>
                                            </div>
                                            <div className="text-xs text-slate-400 mt-0.5">
                                                {primer.sequence.length} {sequenceUnitLabel(primer.sequenceType || inferSequenceTypeFromSequence(primer.sequence))} • Tm: {primer.tm?.toFixed(1) ?? 'n/a'}°C • GC: {primer.gc_percent ?? 'n/a'}%
                                            </div>
                                        </div>
                                        <button
                                            onClick={() => onRemovePrimer(primer.id)}
                                            className="p-1 hover:bg-slate-500 rounded ml-2"
                                            title="Remove primer"
                                        >
                                            <svg className="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                                            </svg>
                                        </button>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </>
            )}

            {activeTab === 'design' && (
                <div className="space-y-3">
                    {selectedRegion && (
                        <div className="flex items-center justify-between rounded border border-cyan-700/40 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-200">
                            <span>
                                Selection available: {selectedRegion.start + 1}–{selectedRegion.end} ({selectedRegion.length} {unitLabel})
                            </span>
                            <button
                                onClick={useSelectionAsDesignTarget}
                                className="rounded bg-cyan-600 px-2 py-1 font-medium text-white transition-colors hover:bg-cyan-500"
                            >
                                Use Selection
                            </button>
                        </div>
                    )}

                    <div className="space-y-3 rounded border border-slate-700 bg-slate-800 p-3">
                        <div className="text-sm font-medium text-slate-300">Primer Pair Design</div>
                        <div className="grid grid-cols-2 gap-3">
                            <SliderField
                                label="Target start"
                                value={designTargetStart}
                                min={1}
                                max={sequenceLength}
                                onChange={(value) => {
                                    setDesignTargetStart(value);
                                    if (value > designTargetEnd) {
                                        setDesignTargetEnd(value);
                                    }
                                }}
                            />
                            <SliderField
                                label="Target end"
                                value={designTargetEnd}
                                min={1}
                                max={sequenceLength}
                                onChange={(value) => {
                                    setDesignTargetEnd(value);
                                    if (value < designTargetStart) {
                                        setDesignTargetStart(value);
                                    }
                                }}
                            />
                            <SliderField
                                label="Primer min"
                                value={designPrimerMinLength}
                                min={16}
                                max={35}
                                onChange={(value) => {
                                    setDesignPrimerMinLength(value);
                                    if (value > designPrimerMaxLength) {
                                        setDesignPrimerMaxLength(value);
                                    }
                                }}
                                format={(value) => `${value} nt`}
                            />
                            <SliderField
                                label="Primer max"
                                value={designPrimerMaxLength}
                                min={16}
                                max={35}
                                onChange={(value) => {
                                    setDesignPrimerMaxLength(value);
                                    if (value < designPrimerMinLength) {
                                        setDesignPrimerMinLength(value);
                                    }
                                }}
                                format={(value) => `${value} nt`}
                            />
                            <SliderField
                                label="Product min"
                                value={designProductMinLength}
                                min={60}
                                max={productSliderMax}
                                step={10}
                                onChange={(value) => {
                                    setDesignProductMinLength(value);
                                    if (value > designProductMaxLength) {
                                        setDesignProductMaxLength(value);
                                    }
                                }}
                                format={(value) => `${value} ${unitLabel}`}
                            />
                            <SliderField
                                label="Product max"
                                value={designProductMaxLength}
                                min={60}
                                max={productSliderMax}
                                step={10}
                                onChange={(value) => {
                                    setDesignProductMaxLength(value);
                                    if (value < designProductMinLength) {
                                        setDesignProductMinLength(value);
                                    }
                                }}
                                format={(value) => `${value} ${unitLabel}`}
                            />
                            <SliderField
                                label="GC min"
                                value={designGcMin}
                                min={20}
                                max={80}
                                onChange={(value) => {
                                    setDesignGcMin(value);
                                    if (value > designGcMax) {
                                        setDesignGcMax(value);
                                    }
                                }}
                                format={(value) => `${value}%`}
                            />
                            <SliderField
                                label="GC max"
                                value={designGcMax}
                                min={20}
                                max={80}
                                onChange={(value) => {
                                    setDesignGcMax(value);
                                    if (value < designGcMin) {
                                        setDesignGcMin(value);
                                    }
                                }}
                                format={(value) => `${value}%`}
                            />
                            <SliderField
                                label="Target Tm"
                                value={designTargetTm}
                                min={45}
                                max={78}
                                step={0.5}
                                onChange={setDesignTargetTm}
                                format={(value) => `${value.toFixed(1)}°C`}
                            />
                            <SliderField
                                label="Max ΔTm"
                                value={designTmDelta}
                                min={0.5}
                                max={10}
                                step={0.5}
                                onChange={setDesignTmDelta}
                                format={(value) => `${value.toFixed(1)}°C`}
                            />
                            <SliderField
                                label="Flank search"
                                value={designFlankSearch}
                                min={20}
                                max={250}
                                step={5}
                                onChange={setDesignFlankSearch}
                                format={(value) => `${value} nt`}
                            />
                            <SliderField
                                label="Pair count"
                                value={designMaxPairs}
                                min={1}
                                max={20}
                                onChange={setDesignMaxPairs}
                                format={(value) => `${value}`}
                            />
                            <SliderField
                                label="GC clamp"
                                value={designGcClampMin}
                                min={0}
                                max={5}
                                onChange={setDesignGcClampMin}
                                format={(value) => `${value} bp`}
                            />
                            <SliderField
                                label="Poly-X cap"
                                value={designMaxPolyX}
                                min={3}
                                max={8}
                                onChange={setDesignMaxPolyX}
                                format={(value) => `${value}`}
                            />
                        </div>

                        <div className="grid grid-cols-1 gap-2">
                            <input
                                value={designOverhangForward}
                                onChange={(event) => setDesignOverhangForward(event.target.value.toUpperCase())}
                                placeholder="Optional 5′ forward overhang"
                                className="w-full rounded border border-slate-600 bg-slate-700 px-2 py-1.5 font-mono text-sm"
                            />
                            <input
                                value={designOverhangReverse}
                                onChange={(event) => setDesignOverhangReverse(event.target.value.toUpperCase())}
                                placeholder="Optional 5′ reverse overhang"
                                className="w-full rounded border border-slate-600 bg-slate-700 px-2 py-1.5 font-mono text-sm"
                            />
                        </div>

                        <button
                            onClick={() => void runPrimerDesign()}
                            disabled={designLoading || !sequenceData.sequence}
                            className="w-full rounded-lg bg-violet-600 px-3 py-2 font-medium text-white transition-colors hover:bg-violet-500 disabled:opacity-50"
                        >
                            {designLoading ? 'Designing…' : 'Design Primer Pairs'}
                        </button>
                    </div>

                    {designResult && (
                        <div className="space-y-3 rounded border border-slate-700 bg-slate-900/50 p-3">
                            <div className="flex items-center justify-between">
                                <div>
                                    <div className="text-sm font-medium text-slate-200">Ranked Primer Pairs</div>
                                    <div className="mt-1 text-xs text-slate-500">
                                        Target {designResult.target_start + 1}-{designResult.target_end} • {designResult.pair_count} pair{designResult.pair_count === 1 ? '' : 's'}
                                    </div>
                                </div>
                            </div>

                            {designResult.warnings.length > 0 && (
                                <div className="rounded border border-amber-700/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                                    {designResult.warnings.join(' ')}
                                </div>
                            )}

                            <div className="space-y-2 max-h-[28rem] overflow-y-auto">
                                {designResult.pairs.map((pair) => (
                                    <div
                                        key={`${pair.rank}:${pair.forward.start}:${pair.reverse.end}`}
                                        className="rounded-lg border border-slate-700 bg-slate-950/70 p-3"
                                        onMouseEnter={() => onHighlight([
                                            { start: pair.forward.start, end: pair.forward.end, color: '#22c55e', label: `F${pair.rank}` },
                                            { start: pair.reverse.start, end: pair.reverse.end, color: '#ef4444', label: `R${pair.rank}` },
                                            { start: pair.product_start, end: pair.product_end, color: '#8b5cf6', label: `Amplicon ${pair.product_length}` },
                                        ])}
                                        onMouseLeave={() => onHighlight([])}
                                    >
                                        <div className="flex items-center justify-between gap-3">
                                            <div>
                                                <div className="font-medium text-slate-100">Pair {pair.rank}</div>
                                                <div className="mt-1 text-xs text-slate-400">
                                                    Product {pair.product_length} {unitLabel} • ΔTm {pair.tm_delta.toFixed(2)}°C • Penalty {pair.penalty.toFixed(3)}
                                                    {' '}• heterodimer {pair.heterodimer_complement}
                                                    {' '}• 3′ heterodimer {pair.three_prime_heterodimer}
                                                </div>
                                            </div>
                                            <button
                                                onClick={() => addDesignedPair(pair)}
                                                className="rounded bg-emerald-600 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-emerald-500"
                                            >
                                                Add Pair
                                            </button>
                                        </div>

                                        <div className="mt-3 grid grid-cols-1 gap-2">
                                            <div className="rounded border border-slate-800 bg-slate-900 px-3 py-2">
                                                <div className="text-[11px] uppercase tracking-[0.12em] text-emerald-400">Forward</div>
                                                <div className="mt-1 font-mono text-xs text-slate-200 break-all">{pair.forward.sequence}</div>
                                                <div className="mt-1 text-xs text-slate-500">
                                                    {pair.forward.start + 1}-{pair.forward.end} • {pair.forward.tm.toFixed(2)}°C • GC {pair.forward.gc_percent}%
                                                    {pair.forward.overhang_length > 0 ? ` • overhang ${pair.forward.overhang_length}` : ''}
                                                    {pair.forward.off_target_site_count != null ? ` • off-target ${pair.forward.off_target_site_count}` : ''}
                                                    {pair.forward.max_hairpin_stem > 0 ? ` • hairpin ${pair.forward.max_hairpin_stem}` : ''}
                                                </div>
                                            </div>
                                            <div className="rounded border border-slate-800 bg-slate-900 px-3 py-2">
                                                <div className="text-[11px] uppercase tracking-[0.12em] text-red-400">Reverse</div>
                                                <div className="mt-1 font-mono text-xs text-slate-200 break-all">{pair.reverse.sequence}</div>
                                                <div className="mt-1 text-xs text-slate-500">
                                                    {pair.reverse.start + 1}-{pair.reverse.end} • {pair.reverse.tm.toFixed(2)}°C • GC {pair.reverse.gc_percent}%
                                                    {pair.reverse.overhang_length > 0 ? ` • overhang ${pair.reverse.overhang_length}` : ''}
                                                    {pair.reverse.off_target_site_count != null ? ` • off-target ${pair.reverse.off_target_site_count}` : ''}
                                                    {pair.reverse.max_hairpin_stem > 0 ? ` • hairpin ${pair.reverse.max_hairpin_stem}` : ''}
                                                </div>
                                            </div>
                                        </div>

                                        {pair.warnings.length > 0 && (
                                            <div className="mt-2 rounded border border-amber-800/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                                                {pair.warnings.join(' • ')}
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}

            {activeTab === 'qc' && (
                <div className="space-y-3">
                    {qcLoading ? (
                        <div className="rounded border border-slate-700 bg-slate-900/50 px-3 py-4 text-sm text-slate-400">
                            Calculating primer QC…
                        </div>
                    ) : (
                        <>
                            <div className="space-y-2">
                                {(sequenceQc?.primers || []).length === 0 ? (
                                    <div className="rounded border border-dashed border-slate-700 bg-slate-900/40 px-3 py-4 text-xs text-slate-500">
                                        Add primers to the construct to run QC.
                                    </div>
                                ) : (sequenceQc?.primers || []).map((entry) => (
                                    <div key={entry.id || entry.name || entry.qc.sequence} className="rounded-lg border border-slate-700 bg-slate-900/50 p-3">
                                        <div className="flex items-center justify-between gap-3">
                                            <div>
                                                <div className="font-medium text-slate-100">{entry.name || 'Primer'}</div>
                                                <div className="mt-1 text-xs text-slate-500">
                                                    self {entry.qc.max_self_complement} • 3′ self {entry.qc.three_prime_self_complement} • hairpin {entry.qc.max_hairpin_stem}
                                                    {entry.qc.binding_site_count != null ? ` • sites ${entry.qc.binding_site_count}` : ''}
                                                </div>
                                            </div>
                                            <div className="text-xs text-slate-400">
                                                off-target {entry.qc.off_target_site_count ?? 0}
                                            </div>
                                        </div>
                                        <div className="mt-2 font-mono text-xs text-slate-300 break-all">{entry.qc.sequence}</div>
                                        {entry.qc.warnings.length > 0 && (
                                            <div className="mt-2 rounded border border-amber-800/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                                                {entry.qc.warnings.join(' • ')}
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </div>

                            {(sequenceQc?.pairwise || []).length > 0 && (
                                <div className="rounded-lg border border-slate-700 bg-slate-900/50 p-3">
                                    <div className="font-medium text-slate-200">Pairwise Dimer Review</div>
                                    <div className="mt-3 space-y-2">
                                        {sequenceQc?.pairwise.map((pair, index) => (
                                            <div key={`${pair.left_id || pair.left_name}:${pair.right_id || pair.right_name}:${index}`} className="rounded border border-slate-800 bg-slate-950/70 px-3 py-2">
                                                <div className="text-sm text-slate-200">
                                                    {(pair.left_name || 'Primer A')} × {(pair.right_name || 'Primer B')}
                                                </div>
                                                <div className="mt-1 text-xs text-slate-500">
                                                    heterodimer {pair.heterodimer_complement} • 3′ heterodimer {pair.three_prime_heterodimer}
                                                </div>
                                                {pair.warnings.length > 0 && (
                                                    <div className="mt-2 text-xs text-amber-300">{pair.warnings.join(' • ')}</div>
                                                )}
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </>
                    )}
                </div>
            )}

            {activeTab === 'library' && (
                <div className="space-y-3">
                    <div className="flex gap-2">
                        <input
                            type="text"
                            value={librarySearch}
                            onChange={(event) => setLibrarySearch(event.target.value)}
                            placeholder="Search primers..."
                            className="flex-1 px-2 py-1.5 bg-slate-700 border border-slate-600 rounded text-sm focus:border-blue-500 focus:outline-none"
                        />
                        <button
                            onClick={() => setShowFavoritesOnly(!showFavoritesOnly)}
                            className={`px-2 py-1.5 rounded text-sm transition-colors ${showFavoritesOnly
                                ? 'bg-amber-600 text-white'
                                : 'bg-slate-700 hover:bg-slate-600 text-slate-300'
                                }`}
                            title="Show favorites only"
                        >
                            ★
                        </button>
                    </div>

                    {libraryLoading ? (
                        <div className="text-center text-slate-500 py-4">Loading...</div>
                    ) : libraryPrimers.length === 0 ? (
                        <div className="text-center text-slate-500 text-sm py-4">
                            {librarySearch ? 'No matching primers' : 'Library is empty'}
                        </div>
                    ) : (
                        <div className="space-y-1 max-h-64 overflow-y-auto">
                            {libraryPrimers.map((primer) => (
                                <div
                                    key={primer.id}
                                    className="flex items-center justify-between p-2 bg-slate-700/50 hover:bg-slate-700 rounded transition-colors"
                                >
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-2">
                                            <span className={`w-2 h-2 rounded-full ${primer.primer_type === 'reverse' ? 'bg-red-500' : 'bg-emerald-500'
                                                }`} />
                                            <span className="text-sm text-slate-200 truncate">{primer.name}</span>
                                            {primer.is_favorite && <span className="text-amber-400 text-xs">★</span>}
                                        </div>
                                        <div className="text-xs text-slate-400 mt-0.5 font-mono truncate">
                                            {primer.sequence.slice(0, 30)}{primer.length > 30 ? '...' : ''}
                                        </div>
                                        <div className="text-xs text-slate-500 mt-0.5">
                                            {primer.length} {sequenceUnitLabel(primer.sequence_type || inferSequenceTypeFromSequence(primer.sequence))} • Tm: {primer.tm?.toFixed(1) ?? 'n/a'}°C • GC: {primer.gc_percent?.toFixed(0) ?? 'n/a'}%
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-1 ml-2">
                                        <button
                                            onClick={() => void addLibraryPrimerToSequence(primer)}
                                            className="p-1 hover:bg-blue-600 rounded text-blue-400 hover:text-white"
                                            title="Add to sequence"
                                        >
                                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                                            </svg>
                                        </button>
                                        <button
                                            onClick={() => void handleToggleFavorite(primer.id)}
                                            className={`p-1 rounded transition-colors ${primer.is_favorite
                                                ? 'text-amber-400 hover:text-amber-300'
                                                : 'text-slate-500 hover:text-amber-400'
                                                }`}
                                            title="Toggle favorite"
                                        >
                                            ★
                                        </button>
                                        <button
                                            onClick={() => void handleDeleteFromLibrary(primer.id)}
                                            className="p-1 hover:bg-red-600 rounded text-slate-400 hover:text-white"
                                            title="Delete from library"
                                        >
                                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                            </svg>
                                        </button>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    <div className="text-xs text-slate-500 text-center">
                        Click + to add a library primer to the current construct using the active Tm model.
                    </div>
                </div>
            )}
        </div>
    );
}
