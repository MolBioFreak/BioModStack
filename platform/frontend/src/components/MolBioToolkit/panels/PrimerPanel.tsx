/**
 * PrimerPanel - Primer design and management
 */

import { useState, useMemo } from 'react';
import type { SequenceData, Primer, HighlightedRegion, SelectionInfo } from '../types';

interface PrimerPanelProps {
    sequenceData: SequenceData;
    selection: SelectionInfo | null;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onAddPrimer: (primer: Primer) => void;
    onRemovePrimer: (primerId: string) => void;
}

// Calculate Tm using Wallace rule / basic formula
function calculateTm(primer: string): number {
    if (!primer || primer.length === 0) return 0;
    const upper = primer.toUpperCase();
    const a = (upper.match(/A/g) || []).length;
    const t = (upper.match(/T/g) || []).length;
    const g = (upper.match(/G/g) || []).length;
    const c = (upper.match(/C/g) || []).length;

    if (primer.length < 14) {
        return 2 * (a + t) + 4 * (g + c);
    }
    return 64.9 + 41 * (g + c - 16.4) / primer.length;
}

// Calculate GC content
function calculateGC(primer: string): number {
    if (!primer || primer.length === 0) return 0;
    const upper = primer.toUpperCase();
    const gc = (upper.match(/[GC]/g) || []).length;
    return Math.round((gc / primer.length) * 100);
}

// Get reverse complement
function reverseComplement(seq: string): string {
    const complement: Record<string, string> = { A: 'T', T: 'A', G: 'C', C: 'G' };
    return seq.toUpperCase().split('').reverse().map(b => complement[b] || b).join('');
}

export function PrimerPanel({
    sequenceData,
    selection,
    onHighlight,
    onAddPrimer,
    onRemovePrimer
}: PrimerPanelProps) {
    const [newPrimerName, setNewPrimerName] = useState('');
    const [newPrimerSeq, setNewPrimerSeq] = useState('');
    const [isReverse, setIsReverse] = useState(false);
    const [hoveredPrimerId, setHoveredPrimerId] = useState<string | null>(null);

    // Get selected sequence region
    const selectedRegion = useMemo(() => {
        if (!selection || selection.start === selection.end) return null;
        const start = Math.min(selection.start, selection.end);
        const end = Math.max(selection.start, selection.end);
        const seq = sequenceData.sequence.slice(start, end);
        return { start, end, sequence: seq, length: seq.length };
    }, [selection, sequenceData.sequence]);

    // Use selection as primer
    const useSelectionAsPrimer = (reverse: boolean) => {
        if (!selectedRegion) return;
        const seq = reverse
            ? reverseComplement(selectedRegion.sequence)
            : selectedRegion.sequence;
        setNewPrimerSeq(seq);
        setIsReverse(reverse);
        setNewPrimerName(`Primer_${reverse ? 'Rev' : 'Fwd'}_${selectedRegion.start + 1}`);
    };

    // Add new primer
    const addPrimer = () => {
        if (!newPrimerSeq || newPrimerSeq.length < 10) return;

        // Find binding position
        const upperSeq = sequenceData.sequence.toUpperCase();
        const searchSeq = isReverse ? reverseComplement(newPrimerSeq) : newPrimerSeq.toUpperCase();
        const pos = upperSeq.indexOf(searchSeq);

        const primer: Primer = {
            id: `primer_${Date.now()}`,
            name: newPrimerName || `Primer_${sequenceData.primers?.length || 0 + 1}`,
            sequence: newPrimerSeq.toUpperCase(),
            start: pos >= 0 ? pos : 0,
            end: pos >= 0 ? pos + searchSeq.length : searchSeq.length,
            strand: isReverse ? -1 : 1,
            tm: calculateTm(newPrimerSeq),
            gc_percent: calculateGC(newPrimerSeq)
        };

        onAddPrimer(primer);
        setNewPrimerName('');
        setNewPrimerSeq('');
    };

    // Highlight specific primer
    const highlightPrimer = (primer: Primer | null) => {
        if (!primer) {
            // Show all primers
            const regions: HighlightedRegion[] = (sequenceData.primers || []).map(p => ({
                start: p.start,
                end: p.end,
                color: p.strand === 1 ? '#22c55e' : '#ef4444',
                label: p.name
            }));
            onHighlight(regions);
        } else {
            onHighlight([{
                start: primer.start,
                end: primer.end,
                color: primer.strand === 1 ? '#22c55e' : '#ef4444',
                label: primer.name
            }]);
        }
    };

    const primers = sequenceData.primers || [];

    return (
        <div className="primer-panel p-3 space-y-4">
            <h4 className="font-semibold text-slate-200">Primers</h4>

            {/* Selection helper */}
            {selectedRegion && (
                <div className="p-3 bg-slate-700/50 rounded space-y-2">
                    <div className="text-sm text-slate-300">
                        Selected: {selectedRegion.start + 1}–{selectedRegion.end} ({selectedRegion.length} bp)
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

            {/* Add primer form */}
            <div className="space-y-2 p-3 bg-slate-800 rounded border border-slate-700">
                <div className="text-sm font-medium text-slate-300">Add Primer</div>

                <input
                    type="text"
                    value={newPrimerName}
                    onChange={(e) => setNewPrimerName(e.target.value)}
                    placeholder="Primer name"
                    className="w-full px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm focus:border-blue-500 focus:outline-none"
                />

                <input
                    type="text"
                    value={newPrimerSeq}
                    onChange={(e) => setNewPrimerSeq(e.target.value.toUpperCase())}
                    placeholder="Sequence (5'→3')"
                    className="w-full px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm font-mono focus:border-blue-500 focus:outline-none"
                />

                <div className="flex items-center gap-4">
                    <label className="flex items-center gap-1 text-sm text-slate-400">
                        <input
                            type="checkbox"
                            checked={isReverse}
                            onChange={(e) => setIsReverse(e.target.checked)}
                            className="w-3 h-3"
                        />
                        Reverse primer
                    </label>

                    {newPrimerSeq && (
                        <div className="text-xs text-slate-400">
                            Tm: {calculateTm(newPrimerSeq).toFixed(1)}°C • GC: {calculateGC(newPrimerSeq)}%
                        </div>
                    )}
                </div>

                <button
                    onClick={addPrimer}
                    disabled={!newPrimerSeq || newPrimerSeq.length < 10}
                    className="w-full py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-sm transition-colors"
                >
                    Add Primer
                </button>
            </div>

            {/* Primer list */}
            <div className="space-y-1">
                <div className="flex items-center justify-between text-sm text-slate-400 mb-2">
                    <span>Saved Primers ({primers.length})</span>
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
                        {primers.map(primer => (
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
                                        {primer.sequence.length} bp • Tm: {primer.tm?.toFixed(1)}°C • GC: {primer.gc_percent}%
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
        </div>
    );
}
