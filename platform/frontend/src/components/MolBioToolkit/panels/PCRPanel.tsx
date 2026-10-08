/**
 * PCRPanel - PCR amplification tool
 */

import { useState, useMemo, useEffect, useCallback } from 'react';
import type { SequenceData, HighlightedRegion } from '../types';
import {
    calculateGcPercent,
    isValidNucleotideSequence,
    resolvePrimerBindings,
    sequenceUnitLabel,
} from '../utils/nucleotides';

interface PCRPanelProps {
    sequenceData: SequenceData;
    sequenceId: string | null;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onPCRComplete?: (product: { sequence: string; length: number; start?: number; end?: number; wrapsOrigin?: boolean }) => void;
}

// Calculate Tm using basic rule (simplified Nearest Neighbor)
function calculateTm(primer: string): number {
    if (!primer || primer.length === 0) return 0;
    const upper = primer.toUpperCase();
    const a = (upper.match(/A/g) || []).length;
    const t = (upper.match(/[TU]/g) || []).length;
    const g = (upper.match(/G/g) || []).length;
    const c = (upper.match(/C/g) || []).length;

    // Wallace rule for short primers, adjusted for longer
    if (primer.length < 14) {
        return 2 * (a + t) + 4 * (g + c);
    }
    // Basic Tm formula for longer primers
    return 64.9 + 41 * (g + c - 16.4) / primer.length;
}

export function PCRPanel({
    sequenceData,
    sequenceId,
    onHighlight,
    onPCRComplete
}: PCRPanelProps) {
    const [forwardPrimer, setForwardPrimer] = useState('');
    const [reversePrimer, setReversePrimer] = useState('');
    const [productName, setProductName] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<{ sequence: string; length: number; start?: number; end?: number; wrapsOrigin?: boolean } | null>(null);
    const sequenceType = sequenceData.sequenceType === 'rna' ? 'rna' : 'dna';
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

    // Calculate primer properties
    const fwdProps = useMemo(() => ({
        tm: calculateTm(forwardPrimer),
        gc: calculateGcPercent(forwardPrimer),
        length: forwardPrimer.length,
        binding: fwdBinding,
    }), [forwardPrimer, fwdBinding]);

    const revProps = useMemo(() => ({
        tm: calculateTm(reversePrimer),
        gc: calculateGcPercent(reversePrimer),
        length: reversePrimer.length,
        binding: revBinding,
    }), [reversePrimer, revBinding]);

    // Predicted product size
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

    // Run PCR
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
                new_name: productName || `${sequenceData.name}_PCR`
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
                body: JSON.stringify(payload)
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

        } catch (e) {
            setError(e instanceof Error ? e.message : 'Unknown error');
        } finally {
            setLoading(false);
        }
    };

    const canRun = forwardPrimer.length >= 15 && reversePrimer.length >= 15 && Boolean(fwdBinding) && Boolean(revBinding);
    const tmDiff = Math.abs(fwdProps.tm - revProps.tm);

    return (
        <div className="pcr-panel p-3 space-y-4">
            <h4 className="font-semibold text-slate-200">PCR Amplification</h4>

            {/* Forward primer */}
            <div className="space-y-1">
                <label className="text-sm text-slate-400">Forward Primer (5'→3')</label>
                <input
                    type="text"
                    value={forwardPrimer}
                    onChange={(e) => {
                        setForwardPrimer(e.target.value.toUpperCase());
                        setResult(null);
                        setError(null);
                    }}
                    placeholder="ATGC..."
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-sm font-mono focus:border-blue-500 focus:outline-none"
                />
                {forwardPrimer && (
                    <div className="flex items-center gap-3 text-xs text-slate-400">
                        <span>{fwdProps.length} {unitLabel}</span>
                        <span className={fwdProps.tm >= 55 && fwdProps.tm <= 65 ? 'text-emerald-400' : 'text-yellow-400'}>
                            Tm: {fwdProps.tm.toFixed(1)}°C
                        </span>
                        <span className={fwdProps.gc >= 40 && fwdProps.gc <= 60 ? 'text-emerald-400' : 'text-yellow-400'}>
                            GC: {fwdProps.gc}%
                        </span>
                        {fwdProps.binding ? (
                            <span className="text-emerald-400">
                                ✓ Anneals @ {fwdProps.binding.start + 1}
                                {fwdProps.binding.overhangLength > 0 ? ` (+${fwdProps.binding.overhangLength} ${unitLabel} tail)` : ''}
                            </span>
                        ) : (
                            <span className="text-red-400">✗ No annealing site</span>
                        )}
                    </div>
                )}
            </div>

            {/* Reverse primer */}
            <div className="space-y-1">
                <label className="text-sm text-slate-400">Reverse Primer (5'→3')</label>
                <input
                    type="text"
                    value={reversePrimer}
                    onChange={(e) => {
                        setReversePrimer(e.target.value.toUpperCase());
                        setResult(null);
                        setError(null);
                    }}
                    placeholder="ATGC..."
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-sm font-mono focus:border-blue-500 focus:outline-none"
                />
                {reversePrimer && (
                    <div className="flex items-center gap-3 text-xs text-slate-400">
                        <span>{revProps.length} {unitLabel}</span>
                        <span className={revProps.tm >= 55 && revProps.tm <= 65 ? 'text-emerald-400' : 'text-yellow-400'}>
                            Tm: {revProps.tm.toFixed(1)}°C
                        </span>
                        <span className={revProps.gc >= 40 && revProps.gc <= 60 ? 'text-emerald-400' : 'text-yellow-400'}>
                            GC: {revProps.gc}%
                        </span>
                        {revProps.binding ? (
                            <span className="text-emerald-400">
                                ✓ Anneals @ {revProps.binding.start + 1}
                                {revProps.binding.overhangLength > 0 ? ` (+${revProps.binding.overhangLength} ${unitLabel} tail)` : ''}
                            </span>
                        ) : (
                            <span className="text-red-400">✗ No annealing site</span>
                        )}
                    </div>
                )}
            </div>

            {/* Tm difference warning */}
            {tmDiff > 5 && forwardPrimer && reversePrimer && (
                <div className="p-2 bg-yellow-900/30 border border-yellow-800/50 rounded text-xs text-yellow-300">
                    ⚠️ Tm difference: {tmDiff.toFixed(1)}°C (ideally &lt; 5°C)
                </div>
            )}

            {/* Predicted size */}
            {predictedSize && (
                <div className="p-2 bg-slate-700/50 rounded text-sm text-slate-300">
                    <span className="text-slate-400">Predicted product:</span>{' '}
                    <span className="font-mono text-emerald-400">{predictedSize.toLocaleString()} {unitLabel}</span>
                </div>
            )}

            {/* Product name */}
            <div className="space-y-1">
                <label className="text-sm text-slate-400">Product Name</label>
                <input
                    type="text"
                    value={productName}
                    onChange={(e) => setProductName(e.target.value)}
                    placeholder={`${sequenceData.name}_PCR`}
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-sm focus:border-blue-500 focus:outline-none"
                />
            </div>

            {/* Run button */}
            <button
                onClick={runPCR}
                disabled={loading || !canRun}
                className="w-full py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-sm font-medium transition-colors"
            >
                {loading ? 'Running PCR...' : 'Run PCR'}
            </button>

            {/* Error */}
            {error && (
                <div className="p-2 bg-red-900/50 border border-red-800 rounded text-sm text-red-300">
                    {error}
                </div>
            )}

            {/* Result */}
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
