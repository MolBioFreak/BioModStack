/**
 * DigestPanel - Restriction enzyme digest tool with enzyme selector for SeqViz display
 */

import { useState, useMemo } from 'react';
import type { SequenceData, DigestFragment, HighlightedRegion } from '../types';

// All restriction enzymes supported by SeqViz (organized by category)
const ALL_ENZYMES = {
    common: [
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
        { name: 'SmaI', site: 'CCCGGG' },
        { name: 'MluI', site: 'ACGCGT' },
        { name: 'ClaI', site: 'ATCGAT' },
        { name: 'EcoRV', site: 'GATATC' },
        { name: 'NheI', site: 'GCTAGC' },
    ],
    goldenGate: [
        { name: 'BsaI', site: 'GGTCTC' },
        { name: 'BbsI', site: 'GAAGAC' },
        { name: 'SapI', site: 'GCTCTTC' },
        { name: 'BsmBI', site: 'CGTCTC' },
        { name: 'AarI', site: 'CACCTGC' },
    ],
    rareCutters: [
        { name: 'AgeI', site: 'ACCGGT' },
        { name: 'AscI', site: 'GGCGCGCC' },
        { name: 'PacI', site: 'TTAATTAA' },
        { name: 'SfiI', site: 'GGCCNNNNNGGCC' },
        { name: 'FseI', site: 'GGCCGGCC' },
        { name: 'PmeI', site: 'GTTTAAAC' },
        { name: 'SwaI', site: 'ATTTAAAT' },
        { name: 'SgrAI', site: 'CRCCGGYG' },
    ],
    additional: [
        { name: 'AatII', site: 'GACGTC' },
        { name: 'AccI', site: 'GTMKAC' },
        { name: 'AflII', site: 'CTTAAG' },
        { name: 'AflIII', site: 'ACRYGT' },
        { name: 'AluI', site: 'AGCT' },
        { name: 'AseI', site: 'ATTAAT' },
        { name: 'AvaI', site: 'CYCGRG' },
        { name: 'AvrII', site: 'CCTAGG' },
        { name: 'BanI', site: 'GGYRCC' },
        { name: 'BanII', site: 'GRGCYC' },
        { name: 'BclI', site: 'TGATCA' },
        { name: 'BlpI', site: 'GCTNAGC' },
        { name: 'BstXI', site: 'CCANNNNNNTGG' },
        { name: 'DpnI', site: 'GATC' },
        { name: 'DraI', site: 'TTTAAA' },
        { name: 'EagI', site: 'CGGCCG' },
        { name: 'FokI', site: 'GGATG' },
        { name: 'HaeIII', site: 'GGCC' },
        { name: 'HhaI', site: 'GCGC' },
        { name: 'HincII', site: 'GTYRAC' },
        { name: 'HinfI', site: 'GANTC' },
        { name: 'HpaI', site: 'GTTAAC' },
        { name: 'HpaII', site: 'CCGG' },
        { name: 'MboI', site: 'GATC' },
        { name: 'MfeI', site: 'CAATTG' },
        { name: 'MscI', site: 'TGGCCA' },
        { name: 'MseI', site: 'TTAA' },
        { name: 'MspI', site: 'CCGG' },
        { name: 'NaeI', site: 'GCCGGC' },
        { name: 'NarI', site: 'GGCGCC' },
        { name: 'NciI', site: 'CCSGG' },
        { name: 'NlaIII', site: 'CATG' },
        { name: 'NruI', site: 'TCGCGA' },
        { name: 'NsiI', site: 'ATGCAT' },
        { name: 'PciI', site: 'ACATGT' },
        { name: 'PvuI', site: 'CGATCG' },
        { name: 'PvuII', site: 'CAGCTG' },
        { name: 'RsaI', site: 'GTAC' },
        { name: 'SacII', site: 'CCGCGG' },
        { name: 'ScaI', site: 'AGTACT' },
        { name: 'SphI', site: 'GCATGC' },
        { name: 'SspI', site: 'AATATT' },
        { name: 'StuI', site: 'AGGCCT' },
        { name: 'TaqI', site: 'TCGA' },
        { name: 'XcmI', site: 'CCANNNNNNNNNTGG' },
        { name: 'XmaI', site: 'CCCGGG' },
        { name: 'XmnI', site: 'GAANNNNTTC' },
        { name: 'ZraI', site: 'GACGTC' },
    ]
};

// Flatten all enzymes for lookup
const ALL_ENZYME_LIST = [
    ...ALL_ENZYMES.common,
    ...ALL_ENZYMES.goldenGate,
    ...ALL_ENZYMES.rareCutters,
    ...ALL_ENZYMES.additional,
];

interface DigestPanelProps {
    sequenceData: SequenceData;
    sequenceId: string | null;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onDigestComplete?: (fragments: DigestFragment[]) => void;
    selectedEnzymes?: string[];
    onEnzymesChange?: (enzymes: string[]) => void;
}

// Simple function to find cut sites
function findCutSites(sequence: string, site: string): number[] {
    const positions: number[] = [];
    const upperSeq = sequence.toUpperCase();
    // Handle degenerate bases for site matching
    const regexSite = site.toUpperCase()
        .replace(/N/g, '[ACGT]')
        .replace(/R/g, '[AG]')
        .replace(/Y/g, '[CT]')
        .replace(/M/g, '[AC]')
        .replace(/K/g, '[GT]')
        .replace(/S/g, '[GC]')
        .replace(/W/g, '[AT]')
        .replace(/H/g, '[ACT]')
        .replace(/B/g, '[CGT]')
        .replace(/V/g, '[ACG]')
        .replace(/D/g, '[AGT]');

    try {
        const regex = new RegExp(regexSite, 'g');
        let match;
        while ((match = regex.exec(upperSeq)) !== null) {
            positions.push(match.index);
        }
    } catch {
        // Fallback to simple indexOf for invalid regex
        let pos = upperSeq.indexOf(site.toUpperCase());
        while (pos !== -1) {
            positions.push(pos);
            pos = upperSeq.indexOf(site.toUpperCase(), pos + 1);
        }
    }
    return positions;
}

export function DigestPanel({
    sequenceData,
    sequenceId,
    onHighlight,
    onDigestComplete,
    selectedEnzymes = [],
    onEnzymesChange
}: DigestPanelProps) {
    const [digestEnzymes, setDigestEnzymes] = useState<string[]>([]);
    const [loading, setLoading] = useState(false);
    const [fragments, setFragments] = useState<DigestFragment[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [showAllEnzymes, setShowAllEnzymes] = useState(false);

    // Calculate cut sites for all enzymes
    const enzymeCutData = useMemo(() => {
        return ALL_ENZYME_LIST.map(enzyme => ({
            ...enzyme,
            cuts: findCutSites(sequenceData.sequence, enzyme.site)
        }));
    }, [sequenceData.sequence]);

    // Filter enzymes by search
    const filteredEnzymes = useMemo(() => {
        if (!searchQuery) return enzymeCutData;
        const q = searchQuery.toLowerCase();
        return enzymeCutData.filter(e =>
            e.name.toLowerCase().includes(q) ||
            e.site.toLowerCase().includes(q)
        );
    }, [enzymeCutData, searchQuery]);

    // Toggle enzyme in the viewer display (SeqViz)
    const toggleViewerEnzyme = (name: string) => {
        if (!onEnzymesChange) return;
        const newEnzymes = selectedEnzymes.includes(name)
            ? selectedEnzymes.filter(e => e !== name)
            : [...selectedEnzymes, name];
        onEnzymesChange(newEnzymes);
    };

    // Toggle enzyme for digest operation
    const toggleDigestEnzyme = (name: string) => {
        setDigestEnzymes(prev =>
            prev.includes(name)
                ? prev.filter(e => e !== name)
                : [...prev, name]
        );
    };

    // Select all visible enzymes
    const selectAll = () => {
        if (!onEnzymesChange) return;
        const names = filteredEnzymes.map(e => e.name);
        const allSelected = names.every(n => selectedEnzymes.includes(n));
        if (allSelected) {
            // Deselect all filtered
            onEnzymesChange(selectedEnzymes.filter(e => !names.includes(e)));
        } else {
            // Select all filtered
            const newEnzymes = [...new Set([...selectedEnzymes, ...names])];
            onEnzymesChange(newEnzymes);
        }
    };

    // Clear all viewer enzymes
    const clearAll = () => {
        onEnzymesChange?.([]);
    };

    // Run digest
    const runDigest = async () => {
        if (digestEnzymes.length === 0) return;

        setLoading(true);
        setError(null);

        try {
            const enzymes = digestEnzymes.map(name => {
                const enz = ALL_ENZYME_LIST.find(e => e.name === name);
                return { name, site: enz?.site || '' };
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

    const viewerCount = selectedEnzymes.length;
    const digestCount = digestEnzymes.length;
    const totalCuts = enzymeCutData
        .filter(e => selectedEnzymes.includes(e.name))
        .reduce((sum, e) => sum + e.cuts.length, 0);

    return (
        <div className="digest-panel p-3 space-y-4 text-sm">
            <h4 className="font-semibold text-slate-200">Restriction Enzymes</h4>

            {/* Search & controls */}
            <div className="space-y-2">
                <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search enzymes..."
                    className="w-full px-2 py-1.5 bg-slate-800 border border-slate-600 rounded text-sm focus:outline-none focus:border-blue-500"
                />
                <div className="flex gap-2 text-xs">
                    <button onClick={selectAll} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded">
                        Toggle All
                    </button>
                    <button onClick={clearAll} className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded">
                        Clear
                    </button>
                    <button
                        onClick={() => setShowAllEnzymes(!showAllEnzymes)}
                        className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded"
                    >
                        {showAllEnzymes ? 'Show Less' : 'Show All'}
                    </button>
                </div>
                <div className="text-xs text-slate-400">
                    {viewerCount} enzymes • {totalCuts} cut sites on sequence
                </div>
            </div>

            {/* Enzyme selection grid */}
            <div className="space-y-2 max-h-64 overflow-y-auto">
                {/* Common enzymes - always shown */}
                <div>
                    <div className="text-xs text-slate-500 mb-1 font-medium">Common 6-Cutters</div>
                    <div className="grid grid-cols-2 gap-0.5">
                        {enzymeCutData
                            .filter(e => ALL_ENZYMES.common.some(c => c.name === e.name))
                            .filter(e => !searchQuery || e.name.toLowerCase().includes(searchQuery.toLowerCase()))
                            .map(enzyme => (
                                <EnzymeButton
                                    key={enzyme.name}
                                    enzyme={enzyme}
                                    isViewerSelected={selectedEnzymes.includes(enzyme.name)}
                                    isDigestSelected={digestEnzymes.includes(enzyme.name)}
                                    onViewerToggle={() => toggleViewerEnzyme(enzyme.name)}
                                    onDigestToggle={() => toggleDigestEnzyme(enzyme.name)}
                                />
                            ))
                        }
                    </div>
                </div>

                {/* Golden Gate enzymes */}
                <div>
                    <div className="text-xs text-slate-500 mb-1 font-medium">Golden Gate / MoClo</div>
                    <div className="grid grid-cols-2 gap-0.5">
                        {enzymeCutData
                            .filter(e => ALL_ENZYMES.goldenGate.some(c => c.name === e.name))
                            .filter(e => !searchQuery || e.name.toLowerCase().includes(searchQuery.toLowerCase()))
                            .map(enzyme => (
                                <EnzymeButton
                                    key={enzyme.name}
                                    enzyme={enzyme}
                                    isViewerSelected={selectedEnzymes.includes(enzyme.name)}
                                    isDigestSelected={digestEnzymes.includes(enzyme.name)}
                                    onViewerToggle={() => toggleViewerEnzyme(enzyme.name)}
                                    onDigestToggle={() => toggleDigestEnzyme(enzyme.name)}
                                />
                            ))
                        }
                    </div>
                </div>

                {/* Rare cutters */}
                <div>
                    <div className="text-xs text-slate-500 mb-1 font-medium">Rare Cutters (8bp+)</div>
                    <div className="grid grid-cols-2 gap-0.5">
                        {enzymeCutData
                            .filter(e => ALL_ENZYMES.rareCutters.some(c => c.name === e.name))
                            .filter(e => !searchQuery || e.name.toLowerCase().includes(searchQuery.toLowerCase()))
                            .map(enzyme => (
                                <EnzymeButton
                                    key={enzyme.name}
                                    enzyme={enzyme}
                                    isViewerSelected={selectedEnzymes.includes(enzyme.name)}
                                    isDigestSelected={digestEnzymes.includes(enzyme.name)}
                                    onViewerToggle={() => toggleViewerEnzyme(enzyme.name)}
                                    onDigestToggle={() => toggleDigestEnzyme(enzyme.name)}
                                />
                            ))
                        }
                    </div>
                </div>

                {/* Additional enzymes - only shown when expanded */}
                {showAllEnzymes && (
                    <div>
                        <div className="text-xs text-slate-500 mb-1 font-medium">Additional Enzymes</div>
                        <div className="grid grid-cols-2 gap-0.5">
                            {enzymeCutData
                                .filter(e => ALL_ENZYMES.additional.some(c => c.name === e.name))
                                .filter(e => !searchQuery || e.name.toLowerCase().includes(searchQuery.toLowerCase()))
                                .map(enzyme => (
                                    <EnzymeButton
                                        key={enzyme.name}
                                        enzyme={enzyme}
                                        isViewerSelected={selectedEnzymes.includes(enzyme.name)}
                                        isDigestSelected={digestEnzymes.includes(enzyme.name)}
                                        onViewerToggle={() => toggleViewerEnzyme(enzyme.name)}
                                        onDigestToggle={() => toggleDigestEnzyme(enzyme.name)}
                                    />
                                ))
                            }
                        </div>
                    </div>
                )}
            </div>

            {/* Digest section */}
            <div className="border-t border-slate-700 pt-3 space-y-2">
                <h5 className="text-xs font-medium text-slate-400 uppercase tracking-wide">Run Digest</h5>
                <div className="text-xs text-slate-400 mb-2">
                    Right-click enzymes above to add to digest, or select below:
                </div>

                {digestCount > 0 && (
                    <div className="flex flex-wrap gap-1 mb-2">
                        {digestEnzymes.map(name => (
                            <span
                                key={name}
                                className="px-1.5 py-0.5 bg-amber-900/50 text-amber-300 rounded text-xs cursor-pointer hover:bg-amber-800/50"
                                onClick={() => toggleDigestEnzyme(name)}
                            >
                                {name} ×
                            </span>
                        ))}
                    </div>
                )}

                <button
                    onClick={runDigest}
                    disabled={loading || digestCount === 0}
                    className="w-full py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-sm font-medium transition-colors"
                >
                    {loading ? 'Digesting...' : `Run Digest (${digestCount} enzymes)`}
                </button>
            </div>

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
                            .map((f) => ({ ...f, size: f.sequence?.length || (f.end - f.start) }))
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

// Individual enzyme button component
interface EnzymeButtonProps {
    enzyme: { name: string; site: string; cuts: number[] };
    isViewerSelected: boolean;
    isDigestSelected: boolean;
    onViewerToggle: () => void;
    onDigestToggle: () => void;
}

function EnzymeButton({ enzyme, isViewerSelected, isDigestSelected, onViewerToggle, onDigestToggle }: EnzymeButtonProps) {
    return (
        <div
            className={`flex items-center justify-between px-1.5 py-1 rounded cursor-pointer text-xs transition-colors ${isViewerSelected
                    ? 'bg-blue-900/50 text-blue-300 border border-blue-700'
                    : 'hover:bg-slate-700/50 text-slate-300 border border-transparent'
                } ${isDigestSelected ? 'ring-1 ring-amber-500' : ''}`}
            onClick={onViewerToggle}
            onContextMenu={(e) => {
                e.preventDefault();
                onDigestToggle();
            }}
            title={`${enzyme.site} • ${enzyme.cuts.length} cuts\nLeft-click: toggle viewer\nRight-click: add to digest`}
        >
            <span className="font-medium">{enzyme.name}</span>
            <span className={`${enzyme.cuts.length > 0 ? 'text-emerald-400' : 'text-slate-500'}`}>
                {enzyme.cuts.length}×
            </span>
        </div>
    );
}
