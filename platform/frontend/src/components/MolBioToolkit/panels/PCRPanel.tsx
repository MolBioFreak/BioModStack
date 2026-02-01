/**
 * PCRPanel - PCR amplification tool
 */

import { useState, useMemo } from 'react';
import type { SequenceData, HighlightedRegion } from '../types';

interface PCRPanelProps {
    sequenceData: SequenceData;
    sequenceId: string | null;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onPCRComplete?: (product: { sequence: string; length: number }) => void;
}

// Calculate Tm using basic rule (simplified Nearest Neighbor)
function calculateTm(primer: string): number {
    if (!primer || primer.length === 0) return 0;
    const upper = primer.toUpperCase();
    const a = (upper.match(/A/g) || []).length;
    const t = (upper.match(/T/g) || []).length;
    const g = (upper.match(/G/g) || []).length;
    const c = (upper.match(/C/g) || []).length;

    // Wallace rule for short primers, adjusted for longer
    if (primer.length < 14) {
        return 2 * (a + t) + 4 * (g + c);
    }
    // Basic Tm formula for longer primers
    return 64.9 + 41 * (g + c - 16.4) / primer.length;
}

// Calculate GC content
function calculateGC(primer: string): number {
    if (!primer || primer.length === 0) return 0;
    const upper = primer.toUpperCase();
    const gc = (upper.match(/[GC]/g) || []).length;
    return Math.round((gc / primer.length) * 100);
}

// Find primer binding site
function findBindingSite(sequence: string, primer: string, isReverse: boolean): number | null {
    if (!sequence || !primer || primer.length < 10) return null;

    const upperSeq = sequence.toUpperCase();
    const upperPrimer = primer.toUpperCase();

    if (isReverse) {
        // For reverse primer, find reverse complement
        const complement: Record<string, string> = { A: 'T', T: 'A', G: 'C', C: 'G' };
        const revComp = upperPrimer.split('').reverse().map(b => complement[b] || b).join('');
        const pos = upperSeq.indexOf(revComp);
        return pos >= 0 ? pos : null;
    } else {
        const pos = upperSeq.indexOf(upperPrimer);
        return pos >= 0 ? pos : null;
    }
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
    const [result, setResult] = useState<{ sequence: string; length: number } | null>(null);

    // Calculate primer properties
    const fwdProps = useMemo(() => ({
        tm: calculateTm(forwardPrimer),
        gc: calculateGC(forwardPrimer),
        length: forwardPrimer.length,
        bindingSite: findBindingSite(sequenceData.sequence, forwardPrimer, false)
    }), [forwardPrimer, sequenceData.sequence]);

    const revProps = useMemo(() => ({
        tm: calculateTm(reversePrimer),
        gc: calculateGC(reversePrimer),
        length: reversePrimer.length,
        bindingSite: findBindingSite(sequenceData.sequence, reversePrimer, true)
    }), [reversePrimer, sequenceData.sequence]);

    // Predicted product size
    const predictedSize = useMemo(() => {
        if (fwdProps.bindingSite !== null && revProps.bindingSite !== null) {
            const start = fwdProps.bindingSite;
            const end = revProps.bindingSite + reversePrimer.length;
            if (end > start) {
                return end - start;
            }
        }
        return null;
    }, [fwdProps.bindingSite, revProps.bindingSite, reversePrimer.length]);

    // Update highlights when primers change
    const updateHighlights = () => {
        const regions: HighlightedRegion[] = [];

        if (fwdProps.bindingSite !== null) {
            regions.push({
                start: fwdProps.bindingSite,
                end: fwdProps.bindingSite + forwardPrimer.length,
                color: '#22c55e',
                label: 'Forward primer'
            });
        }

        if (revProps.bindingSite !== null) {
            regions.push({
                start: revProps.bindingSite,
                end: revProps.bindingSite + reversePrimer.length,
                color: '#ef4444',
                label: 'Reverse primer'
            });
        }

        onHighlight(regions);
    };

    // Run PCR
    const runPCR = async () => {
        if (!forwardPrimer || !reversePrimer) return;

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

            if (data.sequence) {
                const product = {
                    sequence: data.sequence.sequence,
                    length: data.sequence.length
                };
                setResult(product);
                onPCRComplete?.(product);
            }

        } catch (e) {
            setError(e instanceof Error ? e.message : 'Unknown error');
        } finally {
            setLoading(false);
        }
    };

    const canRun = forwardPrimer.length >= 15 && reversePrimer.length >= 15;
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
                        setTimeout(updateHighlights, 100);
                    }}
                    placeholder="ATGC..."
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-sm font-mono focus:border-blue-500 focus:outline-none"
                />
                {forwardPrimer && (
                    <div className="flex items-center gap-3 text-xs text-slate-400">
                        <span>{fwdProps.length} bp</span>
                        <span className={fwdProps.tm >= 55 && fwdProps.tm <= 65 ? 'text-emerald-400' : 'text-yellow-400'}>
                            Tm: {fwdProps.tm.toFixed(1)}°C
                        </span>
                        <span className={fwdProps.gc >= 40 && fwdProps.gc <= 60 ? 'text-emerald-400' : 'text-yellow-400'}>
                            GC: {fwdProps.gc}%
                        </span>
                        {fwdProps.bindingSite !== null ? (
                            <span className="text-emerald-400">✓ Binds @ {fwdProps.bindingSite + 1}</span>
                        ) : (
                            <span className="text-red-400">✗ No binding site</span>
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
                        setTimeout(updateHighlights, 100);
                    }}
                    placeholder="ATGC..."
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-sm font-mono focus:border-blue-500 focus:outline-none"
                />
                {reversePrimer && (
                    <div className="flex items-center gap-3 text-xs text-slate-400">
                        <span>{revProps.length} bp</span>
                        <span className={revProps.tm >= 55 && revProps.tm <= 65 ? 'text-emerald-400' : 'text-yellow-400'}>
                            Tm: {revProps.tm.toFixed(1)}°C
                        </span>
                        <span className={revProps.gc >= 40 && revProps.gc <= 60 ? 'text-emerald-400' : 'text-yellow-400'}>
                            GC: {revProps.gc}%
                        </span>
                        {revProps.bindingSite !== null ? (
                            <span className="text-emerald-400">✓ Binds @ {revProps.bindingSite + 1}</span>
                        ) : (
                            <span className="text-red-400">✗ No binding site</span>
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
                    <span className="font-mono text-emerald-400">{predictedSize.toLocaleString()} bp</span>
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
                        <span className="font-mono text-emerald-400">{result.length.toLocaleString()} bp</span>
                    </div>
                </div>
            )}
        </div>
    );
}
