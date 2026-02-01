/**
 * DigestPanel - Restriction enzyme digest tool
 */

import { useState, useMemo } from 'react';
import type { SequenceData, DigestFragment, HighlightedRegion } from '../types';

// Common restriction enzymes
const COMMON_ENZYMES = [
    { name: 'EcoRI', site: 'GAATTC' },
    { name: 'BamHI', site: 'GGATCC' },
    { name: 'HindIII', site: 'AAGCTT' },
    { name: 'XbaI', site: 'TCTAGA' },
    { name: 'SalI', site: 'GTCGAC' },
    { name: 'PstI', site: 'CTGCAG' },
    { name: 'NotI', site: 'GCGGCCGC' },
    { name: 'XhoI', site: 'CTCGAG' },
    { name: 'NcoI', site: 'CCATGG' },
    { name: 'NdeI', site: 'CATATG' },
    { name: 'BglII', site: 'AGATCT' },
    { name: 'SpeI', site: 'ACTAGT' },
    { name: 'KpnI', site: 'GGTACC' },
    { name: 'SacI', site: 'GAGCTC' },
    { name: 'ApaI', site: 'GGGCCC' },
];

interface DigestPanelProps {
    sequenceData: SequenceData;
    sequenceId: string | null;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onDigestComplete?: (fragments: DigestFragment[]) => void;
}

// Simple function to find cut sites
function findCutSites(sequence: string, site: string): number[] {
    const positions: number[] = [];
    const upperSeq = sequence.toUpperCase();
    const upperSite = site.toUpperCase();
    let pos = upperSeq.indexOf(upperSite);
    while (pos !== -1) {
        positions.push(pos);
        pos = upperSeq.indexOf(upperSite, pos + 1);
    }
    return positions;
}

export function DigestPanel({
    sequenceData,
    sequenceId,
    onHighlight,
    onDigestComplete
}: DigestPanelProps) {
    const [selectedEnzymes, setSelectedEnzymes] = useState<string[]>([]);
    const [loading, setLoading] = useState(false);
    const [fragments, setFragments] = useState<DigestFragment[]>([]);
    const [error, setError] = useState<string | null>(null);

    // Calculate cut sites for each selected enzyme
    const enzymeCutData = useMemo(() => {
        return COMMON_ENZYMES.map(enzyme => ({
            ...enzyme,
            cuts: findCutSites(sequenceData.sequence, enzyme.site)
        }));
    }, [sequenceData.sequence]);

    // Toggle enzyme selection
    const toggleEnzyme = (name: string) => {
        setSelectedEnzymes(prev =>
            prev.includes(name)
                ? prev.filter(e => e !== name)
                : [...prev, name]
        );
    };

    // Update highlights when selection changes
    const updateHighlights = () => {
        const regions: HighlightedRegion[] = [];
        for (const enzyme of enzymeCutData) {
            if (selectedEnzymes.includes(enzyme.name)) {
                for (const pos of enzyme.cuts) {
                    regions.push({
                        start: pos,
                        end: pos + enzyme.site.length,
                        color: '#ef4444',
                        label: enzyme.name
                    });
                }
            }
        }
        onHighlight(regions);
    };

    // Run digest
    const runDigest = async () => {
        if (selectedEnzymes.length === 0) return;

        setLoading(true);
        setError(null);

        try {
            const enzymes = selectedEnzymes.map(name => {
                const enz = COMMON_ENZYMES.find(e => e.name === name)!;
                return { name: enz.name, site: enz.site };
            });

            const payload: Record<string, unknown> = {
                enzymes,
                is_circular: sequenceData.circular,
                save: false
            };

            if (sequenceId) {
                payload.sequence_id = sequenceId;
            } else {
                payload.sequence = sequenceData.sequence;
                payload.name = sequenceData.name;
            }

            const res = await fetch('/api/molbio/digest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                throw new Error(`Digest failed: ${res.status}`);
            }

            const data = await res.json();
            setFragments(data.fragments || []);
            onDigestComplete?.(data.fragments || []);

            // Highlight fragments
            const regions: HighlightedRegion[] = (data.fragments || []).map((f: DigestFragment, i: number) => ({
                start: f.start,
                end: f.end,
                color: i % 2 === 0 ? '#3b82f6' : '#22c55e',
                label: `Fragment ${i + 1} (${f.end - f.start} bp)`
            }));
            onHighlight(regions);

        } catch (e) {
            setError(e instanceof Error ? e.message : 'Unknown error');
        } finally {
            setLoading(false);
        }
    };

    const selectedCount = selectedEnzymes.length;
    const totalCuts = enzymeCutData
        .filter(e => selectedEnzymes.includes(e.name))
        .reduce((sum, e) => sum + e.cuts.length, 0);

    return (
        <div className="digest-panel p-3 space-y-4">
            <h4 className="font-semibold text-slate-200">Restriction Digest</h4>

            {/* Enzyme selection */}
            <div className="space-y-2">
                <div className="flex items-center justify-between text-sm text-slate-400">
                    <span>Select Enzymes</span>
                    <span>{selectedCount} selected • {totalCuts} cuts</span>
                </div>

                <div className="grid grid-cols-2 gap-1 max-h-48 overflow-y-auto">
                    {enzymeCutData.map(enzyme => (
                        <label
                            key={enzyme.name}
                            className={`flex items-center justify-between px-2 py-1 rounded cursor-pointer text-sm transition-colors ${selectedEnzymes.includes(enzyme.name)
                                    ? 'bg-blue-900/50 text-blue-300'
                                    : 'hover:bg-slate-700/50 text-slate-300'
                                }`}
                        >
                            <div className="flex items-center gap-1">
                                <input
                                    type="checkbox"
                                    checked={selectedEnzymes.includes(enzyme.name)}
                                    onChange={() => {
                                        toggleEnzyme(enzyme.name);
                                        setTimeout(updateHighlights, 0);
                                    }}
                                    className="w-3 h-3"
                                />
                                <span>{enzyme.name}</span>
                            </div>
                            <span className={`text-xs ${enzyme.cuts.length > 0 ? 'text-emerald-400' : 'text-slate-500'}`}>
                                {enzyme.cuts.length}×
                            </span>
                        </label>
                    ))}
                </div>
            </div>

            {/* Run button */}
            <button
                onClick={runDigest}
                disabled={loading || selectedCount === 0}
                className="w-full py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-sm font-medium transition-colors"
            >
                {loading ? 'Digesting...' : `Run Digest (${selectedCount} enzymes)`}
            </button>

            {/* Error */}
            {error && (
                <div className="p-2 bg-red-900/50 border border-red-800 rounded text-sm text-red-300">
                    {error}
                </div>
            )}

            {/* Results */}
            {fragments.length > 0 && (
                <div className="space-y-2">
                    <h5 className="text-sm font-medium text-slate-300">
                        Fragments ({fragments.length})
                    </h5>
                    <div className="space-y-1 max-h-40 overflow-y-auto">
                        {fragments
                            .map((f, i) => ({ ...f, size: f.sequence?.length || (f.end - f.start) }))
                            .sort((a, b) => b.size - a.size)
                            .map((f, i) => (
                                <div
                                    key={i}
                                    className="flex items-center justify-between px-2 py-1 bg-slate-700/50 rounded text-sm"
                                >
                                    <span className="text-slate-300">Fragment {i + 1}</span>
                                    <span className="text-emerald-400 font-mono">{f.size.toLocaleString()} bp</span>
                                </div>
                            ))
                        }
                    </div>
                </div>
            )}
        </div>
    );
}
