/**
 * PCRPanel - PCR amplification tool
 */

import { useState, useMemo, useEffect, useCallback } from 'react';
import type { SequenceData, HighlightedRegion } from '../types';
import {
    calculatePrimerTm,
    type PrimerTmOptionsResponse,
    type PrimerTmResult,
    type PrimerTmSettings,
} from '../../../lib/api';
import { PrimerTmSettingsPanel } from '../PrimerTmSettingsPanel';
import {
    isValidNucleotideSequence,
    resolvePrimerBindings,
    sequenceUnitLabel,
} from '../utils/nucleotides';

interface PCRPanelProps {
    sequenceData: SequenceData;
    sequenceId: string | null;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onPCRComplete?: (product: { sequence: string; length: number; start?: number; end?: number; wrapsOrigin?: boolean }) => void;
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

export function PCRPanel(props: PCRPanelProps) {
    const {
        sequenceData,
        sequenceId,
        onHighlight,
        onPCRComplete,
        tmOptions,
        tmSettings,
        onTmSettingsChange,
    } = props;
    const [forwardPrimer, setForwardPrimer] = useState('');
    const [reversePrimer, setReversePrimer] = useState('');
    const [productName, setProductName] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<{ sequence: string; length: number; start?: number; end?: number; wrapsOrigin?: boolean } | null>(null);
    const [tmLoading, setTmLoading] = useState(false);
    const [tmResults, setTmResults] = useState<{ forward: PrimerTmResult | null; reverse: PrimerTmResult | null }>({
        forward: null,
        reverse: null,
    });

    const sequenceType: 'dna' | 'rna' = sequenceData.sequenceType === 'rna' ? 'rna' : 'dna';
    const unitLabel = sequenceUnitLabel(sequenceType);

    const fwdBindings = useMemo(() => resolvePrimerBindings(sequenceData.sequence, forwardPrimer, {
        reverse: false,
        sequenceType,
        circular: sequenceData.circular,
    }), [forwardPrimer, sequenceData.sequence, sequenceData.circular, sequenceType]);

    const revBindings = useMemo(() => resolvePrimerBindings(sequenceData.sequence, reversePrimer, {
        reverse: true,
        sequenceType,
        circular: sequenceData.circular,
    }), [reversePrimer, sequenceData.sequence, sequenceData.circular, sequenceType]);

    const fwdBinding = fwdBindings[0] ?? null;
    const revBinding = revBindings[0] ?? null;

    const forwardAnnealSequence = useMemo(() => {
        if (!forwardPrimer) return '';
        if (!fwdBinding) return forwardPrimer;
        return forwardPrimer.slice(forwardPrimer.length - fwdBinding.annealLength);
    }, [forwardPrimer, fwdBinding]);

    const reverseAnnealSequence = useMemo(() => {
        if (!reversePrimer) return '';
        if (!revBinding) return reversePrimer;
        return reversePrimer.slice(reversePrimer.length - revBinding.annealLength);
    }, [reversePrimer, revBinding]);

    useEffect(() => {
        if (!forwardPrimer && !reversePrimer) {
            setTmResults({ forward: null, reverse: null });
            setTmLoading(false);
            return;
        }
        if ((forwardPrimer && !isValidNucleotideSequence(forwardPrimer)) || (reversePrimer && !isValidNucleotideSequence(reversePrimer))) {
            setTmResults({ forward: null, reverse: null });
            setTmLoading(false);
            return;
        }

        let cancelled = false;
        setTmLoading(true);
        const timer = window.setTimeout(async () => {
            try {
                const response = await calculatePrimerTm({
                    primers: [
                        ...(forwardAnnealSequence ? [{ id: 'forward', sequence: forwardAnnealSequence, sequence_type: sequenceType }] : []),
                        ...(reverseAnnealSequence ? [{ id: 'reverse', sequence: reverseAnnealSequence, sequence_type: sequenceType }] : []),
                    ],
                    settings: tmSettings,
                });
                if (cancelled) {
                    return;
                }
                const forwardResult = response.data.find((entry) => entry.id === 'forward') ?? null;
                const reverseResult = response.data.find((entry) => entry.id === 'reverse') ?? null;
                setTmResults({ forward: forwardResult, reverse: reverseResult });
            } catch (tmError) {
                console.error('Failed to calculate PCR primer Tm:', tmError);
                if (!cancelled) {
                    setTmResults({ forward: null, reverse: null });
                }
            } finally {
                if (!cancelled) {
                    setTmLoading(false);
                }
            }
        }, 250);

        return () => {
            cancelled = true;
            window.clearTimeout(timer);
        };
    }, [forwardAnnealSequence, forwardPrimer, reverseAnnealSequence, reversePrimer, sequenceType, tmSettings]);

    const predictedSize = useMemo(() => {
        if (fwdBinding && revBinding) {
            const templateLength = sequenceData.sequence.length;
            const templateSpan = sequenceData.circular
                ? (() => {
                    let span = (revBinding.end - fwdBinding.start) % templateLength;
                    if (span === 0) {
                        span = templateLength;
                    }
                    return span;
                })()
                : revBinding.end > fwdBinding.start
                    ? revBinding.end - fwdBinding.start
                    : null;

            if (templateSpan !== null) {
                return templateSpan + fwdBinding.overhangLength + revBinding.overhangLength;
            }
        }
        return null;
    }, [fwdBinding, revBinding, sequenceData.circular, sequenceData.sequence.length]);

    const buildBindingHighlights = useCallback(() => {
        const regions: HighlightedRegion[] = [];
        const sequenceLength = sequenceData.sequence.length;

        const addBinding = (
            binding: typeof fwdBinding,
            color: string,
            label: string,
        ) => {
            if (!binding) return;
            if (binding.end > sequenceLength && sequenceLength > 0) {
                regions.push({
                    start: binding.start,
                    end: sequenceLength,
                    color,
                    label,
                });
                regions.push({
                    start: 0,
                    end: binding.end % sequenceLength,
                    color,
                    label,
                });
                return;
            }
            regions.push({
                start: binding.start,
                end: binding.end,
                color,
                label,
            });
        };

        addBinding(fwdBinding, '#22c55e', 'Forward primer');
        addBinding(revBinding, '#ef4444', 'Reverse primer');
        return regions;
    }, [fwdBinding, revBinding, sequenceData.sequence.length]);

    useEffect(() => {
        if (!forwardPrimer && !reversePrimer) {
            onHighlight([]);
            return;
        }
        onHighlight(buildBindingHighlights());
    }, [forwardPrimer, reversePrimer, buildBindingHighlights, onHighlight]);

    const runPCR = async () => {
        if (!forwardPrimer || !reversePrimer) return;
        if (!isValidNucleotideSequence(forwardPrimer) || !isValidNucleotideSequence(reversePrimer)) {
            setError('Primers contain invalid nucleotide characters.');
            return;
        }

        setLoading(true);
        setError(null);

        try {
            const payload: Record<string, unknown> = {
                primer_fwd: forwardPrimer,
                primer_rev: reversePrimer,
                is_circular: sequenceData.circular,
                save: false,
                new_name: productName || `${sequenceData.name}_PCR`,
            };

            if (sequenceId) {
                payload.sequence_id = sequenceId;
            } else {
                payload.sequence = sequenceData.sequence;
                payload.name = sequenceData.name;
                payload.sequence_type = sequenceType;
            }

            const res = await fetch('/api/molbio/pcr', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `PCR failed: ${res.status}`);
            }

            const data = await res.json();
            const responseProduct = data.product;
            const persistedSequence = data.sequence;
            const product = responseProduct
                ? {
                    sequence: responseProduct.sequence,
                    length: responseProduct.length,
                    start: responseProduct.start,
                    end: responseProduct.end,
                    wrapsOrigin: responseProduct.wraps_origin,
                }
                : persistedSequence
                    ? {
                        sequence: persistedSequence.sequence,
                        length: persistedSequence.length,
                    }
                    : null;

            if (product) {
                setResult(product);
                onPCRComplete?.(product);

                const regions: HighlightedRegion[] = [];
                if (typeof product.start === 'number' && typeof product.end === 'number') {
                    const label = `PCR product (${product.length.toLocaleString()} ${unitLabel})`;
                    if (product.wrapsOrigin) {
                        regions.push({
                            start: product.start,
                            end: sequenceData.sequence.length,
                            color: '#38bdf8',
                            label,
                        });
                        if (product.end > 0) {
                            regions.push({
                                start: 0,
                                end: product.end,
                                color: '#38bdf8',
                                label,
                            });
                        }
                    } else {
                        regions.push({
                            start: product.start,
                            end: product.end,
                            color: '#38bdf8',
                            label,
                        });
                    }
                }
                if (regions.length > 0) {
                    onHighlight(regions);
                }
            }
        } catch (runError) {
            setError(runError instanceof Error ? runError.message : 'Unknown error');
        } finally {
            setLoading(false);
        }
    };

    const canRun = forwardPrimer.length >= 15 && reversePrimer.length >= 15 && Boolean(fwdBinding) && Boolean(revBinding);
    const tmDiff = (tmResults.forward?.tm !== null && tmResults.forward?.tm !== undefined && tmResults.reverse?.tm !== null && tmResults.reverse?.tm !== undefined)
        ? Math.abs(tmResults.forward.tm - tmResults.reverse.tm)
        : 0;

    return (
        <div className="pcr-panel p-3 space-y-4">
            <h4 className="font-semibold text-slate-200">PCR Amplification</h4>

            <PrimerTmSettingsPanel
                sequenceType={sequenceType}
                options={tmOptions}
                settings={tmSettings}
                onChange={onTmSettingsChange}
            />

            <div className="space-y-1">
                <label className="text-sm text-slate-400">Forward Primer (5'→3')</label>
                <input
                    type="text"
                    value={forwardPrimer}
                    onChange={(event) => {
                        setForwardPrimer(event.target.value.toUpperCase());
                        setResult(null);
                        setError(null);
                    }}
                    placeholder="ATGC..."
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-sm font-mono focus:border-blue-500 focus:outline-none"
                />
                {forwardPrimer && (
                    <div className="space-y-1 text-xs text-slate-400">
                        <div className="flex items-center gap-3 flex-wrap">
                            <span>{forwardPrimer.length} {unitLabel}</span>
                            <span className="text-emerald-300">
                                {fwdBinding && fwdBinding.overhangLength > 0 ? 'Annealing Tm' : 'Tm'}: {tmLoading ? 'Calculating...' : formatTm(tmResults.forward)}
                            </span>
                            <span>GC: {tmResults.forward?.gc_percent ?? 'n/a'}%</span>
                            {fwdBinding ? (
                                <span className="text-emerald-400">
                                    ✓ Anneals @ {fwdBinding.start + 1}
                                    {fwdBinding.overhangLength > 0 ? ` (+${fwdBinding.overhangLength} ${unitLabel} tail)` : ''}
                                </span>
                            ) : (
                                <span className="text-red-400">✗ No annealing site</span>
                            )}
                        </div>
                        {(tmResults.forward?.warnings || []).map((warning) => (
                            <div key={`f-${warning}`} className="text-yellow-300">
                                {warning}
                            </div>
                        ))}
                    </div>
                )}
            </div>

            <div className="space-y-1">
                <label className="text-sm text-slate-400">Reverse Primer (5'→3')</label>
                <input
                    type="text"
                    value={reversePrimer}
                    onChange={(event) => {
                        setReversePrimer(event.target.value.toUpperCase());
                        setResult(null);
                        setError(null);
                    }}
                    placeholder="ATGC..."
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-sm font-mono focus:border-blue-500 focus:outline-none"
                />
                {reversePrimer && (
                    <div className="space-y-1 text-xs text-slate-400">
                        <div className="flex items-center gap-3 flex-wrap">
                            <span>{reversePrimer.length} {unitLabel}</span>
                            <span className="text-emerald-300">
                                {revBinding && revBinding.overhangLength > 0 ? 'Annealing Tm' : 'Tm'}: {tmLoading ? 'Calculating...' : formatTm(tmResults.reverse)}
                            </span>
                            <span>GC: {tmResults.reverse?.gc_percent ?? 'n/a'}%</span>
                            {revBinding ? (
                                <span className="text-emerald-400">
                                    ✓ Anneals @ {revBinding.start + 1}
                                    {revBinding.overhangLength > 0 ? ` (+${revBinding.overhangLength} ${unitLabel} tail)` : ''}
                                </span>
                            ) : (
                                <span className="text-red-400">✗ No annealing site</span>
                            )}
                        </div>
                        {(tmResults.reverse?.warnings || []).map((warning) => (
                            <div key={`r-${warning}`} className="text-yellow-300">
                                {warning}
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {tmDiff > 5 && forwardPrimer && reversePrimer && (
                <div className="p-2 bg-yellow-900/30 border border-yellow-800/50 rounded text-xs text-yellow-300">
                    ⚠️ Tm difference: {tmDiff.toFixed(1)}°C (ideally &lt; 5°C)
                </div>
            )}

            {predictedSize && (
                <div className="p-2 bg-slate-700/50 rounded text-sm text-slate-300">
                    <span className="text-slate-400">Predicted product:</span>{' '}
                    <span className="font-mono text-emerald-400">{predictedSize.toLocaleString()} {unitLabel}</span>
                </div>
            )}

            <div className="space-y-1">
                <label className="text-sm text-slate-400">Product Name</label>
                <input
                    type="text"
                    value={productName}
                    onChange={(event) => setProductName(event.target.value)}
                    placeholder={`${sequenceData.name}_PCR`}
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-sm focus:border-blue-500 focus:outline-none"
                />
            </div>

            <button
                onClick={runPCR}
                disabled={loading || !canRun}
                className="w-full py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-sm font-medium transition-colors"
            >
                {loading ? 'Running PCR...' : 'Run PCR'}
            </button>

            {error && (
                <div className="p-2 bg-red-900/50 border border-red-800 rounded text-sm text-red-300">
                    {error}
                </div>
            )}

            {result && (
                <div className="p-3 bg-emerald-900/30 border border-emerald-800/50 rounded space-y-2">
                    <div className="flex items-center justify-between">
                        <span className="text-sm text-emerald-300">PCR Product</span>
                        <span className="font-mono text-emerald-400">{result.length.toLocaleString()} {unitLabel}</span>
                    </div>
                    {result.wrapsOrigin && (
                        <div className="text-xs text-emerald-200/80">
                            Product spans the plasmid origin.
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
