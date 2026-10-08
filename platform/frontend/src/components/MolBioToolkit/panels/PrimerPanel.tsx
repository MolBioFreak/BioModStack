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
    type Primer as LibraryPrimer,
    type PrimerCreate,
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
    const [activeTab, setActiveTab] = useState<'sequence' | 'library'>('sequence');

    const [libraryPrimers, setLibraryPrimers] = useState<LibraryPrimer[]>([]);
    const [libraryLoading, setLibraryLoading] = useState(false);
    const [librarySearch, setLibrarySearch] = useState('');
    const [showFavoritesOnly, setShowFavoritesOnly] = useState(false);
    const [saveToLibrary, setSaveToLibrary] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [draftTmResult, setDraftTmResult] = useState<PrimerTmResult | null>(null);
    const [draftTmLoading, setDraftTmLoading] = useState(false);

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
            setDraftTmLoading(false);
            return;
        }

        let cancelled = false;
        setDraftTmLoading(true);
        const timer = window.setTimeout(async () => {
            const result = await calculateTmForSequence(draftTmSequence, inferSequenceTypeFromSequence(draftTmSequence));
            if (!cancelled) {
                setDraftTmResult(result);
                setDraftTmLoading(false);
            }
        }, 250);

        return () => {
            cancelled = true;
            window.clearTimeout(timer);
        };
    }, [calculateTmForSequence, cleanedDraftPrimer, draftTmSequence]);

    const useSelectionAsPrimer = (reverse: boolean) => {
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
                                    onClick={() => useSelectionAsPrimer(false)}
                                    className="flex-1 px-2 py-1 bg-emerald-700 hover:bg-emerald-600 rounded text-xs transition-colors"
                                >
                                    Use as Forward
                                </button>
                                <button
                                    onClick={() => useSelectionAsPrimer(true)}
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
