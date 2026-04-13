/**
 * DigestPanel - Restriction analysis workspace with quick filters and digest basket
 */

import { useMemo, useState } from 'react';
import type { DigestFragment, HighlightedRegion, SelectionInfo, SequenceData } from '../types';
import {
    ALL_RESTRICTION_ENZYMES,
    getRestrictionEnzyme,
    findRestrictionSites,
    type RestrictionEnzymeCategory,
    type RestrictionEnzymeDefinition,
} from '../utils/restrictionEnzymes';

type CutFilter = 'all' | 'zero' | 'unique' | 'double' | 'three_plus' | 'selection';
type GroupFilter = 'all' | 'common' | 'golden_gate' | 'rare' | 'nicking' | 'viewer_supported';

interface DigestPanelProps {
    sequenceData: SequenceData;
    sequenceId: string | null;
    selection?: SelectionInfo | null;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onDigestComplete?: (fragments: DigestFragment[]) => void;
    selectedEnzymes?: string[];
    onEnzymesChange?: (enzymes: string[]) => void;
}

interface EnzymeCutData extends RestrictionEnzymeDefinition {
    cuts: number[];
    selectionCuts: number;
}

const CUT_FILTER_OPTIONS: Array<{ value: CutFilter; label: string }> = [
    { value: 'all', label: 'All' },
    { value: 'unique', label: '1x' },
    { value: 'double', label: '2x' },
    { value: 'three_plus', label: '3x+' },
    { value: 'zero', label: '0x' },
    { value: 'selection', label: 'In Selection' },
];

const GROUP_FILTER_OPTIONS: Array<{ value: GroupFilter; label: string }> = [
    { value: 'all', label: 'All Types' },
    { value: 'common', label: 'Common' },
    { value: 'golden_gate', label: 'Golden Gate' },
    { value: 'rare', label: 'Rare' },
    { value: 'nicking', label: 'Nicking' },
    { value: 'viewer_supported', label: 'Map-Ready' },
];

const CATEGORY_LABELS: Record<RestrictionEnzymeCategory, string> = {
    common: 'Common',
    golden_gate: 'Golden Gate',
    rare: 'Rare',
    additional: 'Additional',
    nicking: 'Nicking',
};

function getSelectionRanges(
    selection: SelectionInfo | null | undefined,
    sequenceLength: number,
    circular: boolean,
): Array<{ start: number; end: number }> {
    if (!selection || sequenceLength <= 0) {
        return [];
    }

    const rawStart = Math.max(0, Math.min(selection.start, sequenceLength));
    const rawEnd = Math.max(0, Math.min(selection.end, sequenceLength));
    if (rawStart === rawEnd) {
        return [];
    }

    if (!circular) {
        return [{ start: Math.min(rawStart, rawEnd), end: Math.max(rawStart, rawEnd) }];
    }

    if (selection.clockwise && rawStart > rawEnd) {
        return [
            { start: rawStart, end: sequenceLength },
            { start: 0, end: rawEnd },
        ];
    }

    if (rawStart > rawEnd) {
        return [
            { start: rawStart, end: sequenceLength },
            { start: 0, end: rawEnd },
        ];
    }

    return [{ start: rawStart, end: rawEnd }];
}

function isPositionInRanges(position: number, ranges: Array<{ start: number; end: number }>): boolean {
    return ranges.some((range) => position >= range.start && position < range.end);
}

function selectionLength(ranges: Array<{ start: number; end: number }>): number {
    return ranges.reduce((total, range) => total + Math.max(0, range.end - range.start), 0);
}

function formatSelectionLabel(
    ranges: Array<{ start: number; end: number }>,
    circular: boolean,
): string | null {
    if (ranges.length === 0) {
        return null;
    }
    if (ranges.length === 1) {
        const range = ranges[0];
        return `${range.start + 1}-${range.end}${circular ? ' on circular template' : ''}`;
    }
    return `${ranges[0].start + 1}-${ranges[0].end} + ${ranges[1].start + 1}-${ranges[1].end}`;
}

function FilterButton({
    active,
    disabled = false,
    label,
    onClick,
}: {
    active: boolean;
    disabled?: boolean;
    label: string;
    onClick: () => void;
}) {
    return (
        <button
            onClick={onClick}
            disabled={disabled}
            className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                active
                    ? 'border-cyan-500 bg-cyan-500/15 text-cyan-200'
                    : 'border-slate-600 bg-slate-800 text-slate-300 hover:border-slate-500 hover:bg-slate-700'
            } ${disabled ? 'cursor-not-allowed opacity-40' : ''}`}
        >
            {label}
        </button>
    );
}

function BasketChip({
    label,
    onRemove,
}: {
    label: string;
    onRemove: () => void;
}) {
    return (
        <button
            onClick={onRemove}
            className="rounded-full border border-amber-500/35 bg-amber-500/10 px-2 py-0.5 text-xs text-amber-200 hover:bg-amber-500/20"
        >
            {label} ×
        </button>
    );
}

function EnzymeRow({
    enzyme,
    selectionActive,
    isViewerSelected,
    isDigestSelected,
    onViewerToggle,
    onDigestToggle,
}: {
    enzyme: EnzymeCutData;
    selectionActive: boolean;
    isViewerSelected: boolean;
    isDigestSelected: boolean;
    onViewerToggle: () => void;
    onDigestToggle: () => void;
}) {
    const nickLabel = enzyme.nickingStrand ? `Nick ${enzyme.nickingStrand}` : null;
    const mapDisabled = enzyme.viewerSupported === false;
    const digestDisabled = Boolean(enzyme.nickingStrand);

    return (
        <div className="grid grid-cols-[1fr_auto] gap-2 rounded-lg border border-slate-700/80 bg-slate-800/60 px-2 py-2">
            <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-medium text-slate-100">{enzyme.name}</span>
                    <span className="rounded-full bg-slate-700 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-300">
                        {CATEGORY_LABELS[enzyme.category]}
                    </span>
                    {nickLabel && (
                        <span className="rounded-full bg-fuchsia-500/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-fuchsia-200">
                            {nickLabel}
                        </span>
                    )}
                    {selectionActive && enzyme.selectionCuts > 0 && (
                        <span className="rounded-full bg-cyan-500/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-cyan-200">
                            {enzyme.selectionCuts} in selection
                        </span>
                    )}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                    <span className="font-mono tracking-wide text-slate-300">{enzyme.site}</span>
                    <span>{enzyme.cuts.length} cut{enzyme.cuts.length === 1 ? '' : 's'}</span>
                    {enzyme.viewerSupported === false && (
                        <span className="text-amber-300">analysis-only</span>
                    )}
                </div>
            </div>

            <div className="flex items-center gap-1">
                <button
                    onClick={onViewerToggle}
                    disabled={mapDisabled}
                    title={mapDisabled ? 'SeqViz map overlay currently supports standard double-strand enzymes only' : 'Toggle on map'}
                    className={`rounded-md border px-2 py-1 text-[11px] font-medium transition-colors ${
                        isViewerSelected
                            ? 'border-blue-500 bg-blue-500/15 text-blue-200'
                            : 'border-slate-600 bg-slate-900/60 text-slate-300 hover:bg-slate-700'
                    } ${mapDisabled ? 'cursor-not-allowed opacity-40' : ''}`}
                >
                    Map
                </button>
                <button
                    onClick={onDigestToggle}
                    disabled={digestDisabled}
                    title={digestDisabled ? 'Digest simulation is disabled for nicking enzymes until strand-specific nick support is added' : 'Add or remove from digest basket'}
                    className={`rounded-md border px-2 py-1 text-[11px] font-medium transition-colors ${
                        isDigestSelected
                            ? 'border-amber-500 bg-amber-500/15 text-amber-200'
                            : 'border-slate-600 bg-slate-900/60 text-slate-300 hover:bg-slate-700'
                    } ${digestDisabled ? 'cursor-not-allowed opacity-40' : ''}`}
                >
                    Digest
                </button>
            </div>
        </div>
    );
}

export function DigestPanel({
    sequenceData,
    sequenceId,
    selection,
    onHighlight,
    onDigestComplete,
    selectedEnzymes = [],
    onEnzymesChange,
}: DigestPanelProps) {
    const [digestEnzymes, setDigestEnzymes] = useState<string[]>([]);
    const [loading, setLoading] = useState(false);
    const [fragments, setFragments] = useState<DigestFragment[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [cutFilter, setCutFilter] = useState<CutFilter>('unique');
    const [groupFilter, setGroupFilter] = useState<GroupFilter>('all');

    const selectionRanges = useMemo(
        () => getSelectionRanges(selection, sequenceData.sequence.length, sequenceData.circular),
        [selection, sequenceData.sequence.length, sequenceData.circular],
    );

    const selectionActive = selectionRanges.length > 0;
    const selectionBp = selectionLength(selectionRanges);
    const selectionLabel = formatSelectionLabel(selectionRanges, sequenceData.circular);

    const enzymeCutData = useMemo<EnzymeCutData[]>(() => (
        ALL_RESTRICTION_ENZYMES.map((enzyme) => {
            const cuts = findRestrictionSites(sequenceData.sequence, enzyme.site, sequenceData.circular);
            const selectionCuts = selectionActive
                ? cuts.filter((position) => isPositionInRanges(position, selectionRanges)).length
                : 0;
            return {
                ...enzyme,
                cuts,
                selectionCuts,
            };
        })
    ), [sequenceData.sequence, sequenceData.circular, selectionActive, selectionRanges]);

    const filteredEnzymes = useMemo(() => {
        const query = searchQuery.trim().toLowerCase();
        return enzymeCutData
            .filter((enzyme) => {
                if (query && ![
                    enzyme.name.toLowerCase(),
                    enzyme.site.toLowerCase(),
                    CATEGORY_LABELS[enzyme.category].toLowerCase(),
                    ...(enzyme.tags || []).map((tag) => tag.toLowerCase()),
                    enzyme.nickingStrand ? `nick ${enzyme.nickingStrand}` : '',
                ].some((field) => field.includes(query))) {
                    return false;
                }

                if (groupFilter === 'viewer_supported' && enzyme.viewerSupported === false) {
                    return false;
                }
                if (groupFilter !== 'all' && groupFilter !== 'viewer_supported' && enzyme.category !== groupFilter) {
                    return false;
                }

                if (cutFilter === 'zero') return enzyme.cuts.length === 0;
                if (cutFilter === 'unique') return enzyme.cuts.length === 1;
                if (cutFilter === 'double') return enzyme.cuts.length === 2;
                if (cutFilter === 'three_plus') return enzyme.cuts.length >= 3;
                if (cutFilter === 'selection') return selectionActive && enzyme.selectionCuts > 0;
                return true;
            })
            .sort((left, right) => {
                if (cutFilter === 'selection' && right.selectionCuts !== left.selectionCuts) {
                    return right.selectionCuts - left.selectionCuts;
                }
                if (left.cuts.length !== right.cuts.length) {
                    return left.cuts.length - right.cuts.length;
                }
                return left.name.localeCompare(right.name);
            });
    }, [cutFilter, enzymeCutData, groupFilter, searchQuery, selectionActive]);

    const filteredViewerNames = filteredEnzymes
        .filter((enzyme) => enzyme.viewerSupported !== false)
        .map((enzyme) => enzyme.name);

    const filteredDigestNames = filteredEnzymes
        .filter((enzyme) => !enzyme.nickingStrand)
        .map((enzyme) => enzyme.name);

    const viewerCount = selectedEnzymes.length;
    const digestCount = digestEnzymes.length;
    const totalVisibleCuts = enzymeCutData
        .filter((enzyme) => selectedEnzymes.includes(enzyme.name))
        .reduce((sum, enzyme) => sum + enzyme.cuts.length, 0);
    const uniqueCutterCount = enzymeCutData.filter((enzyme) => enzyme.cuts.length === 1).length;

    const toggleViewerEnzyme = (name: string) => {
        const enzyme = getRestrictionEnzyme(name);
        if (!onEnzymesChange || enzyme?.viewerSupported === false) {
            return;
        }
        const next = selectedEnzymes.includes(name)
            ? selectedEnzymes.filter((item) => item !== name)
            : [...selectedEnzymes, name];
        onEnzymesChange(next);
    };

    const toggleDigestEnzyme = (name: string) => {
        const enzyme = getRestrictionEnzyme(name);
        if (enzyme?.nickingStrand) {
            return;
        }
        setDigestEnzymes((previous) => (
            previous.includes(name)
                ? previous.filter((item) => item !== name)
                : [...previous, name]
        ));
    };

    const replaceViewerWithFiltered = () => {
        onEnzymesChange?.(filteredViewerNames);
    };

    const addFilteredToDigest = () => {
        setDigestEnzymes((previous) => Array.from(new Set([...previous, ...filteredDigestNames])));
    };

    const copyViewerSetToDigest = () => {
        const viewerDigestable = selectedEnzymes.filter((name) => !getRestrictionEnzyme(name)?.nickingStrand);
        setDigestEnzymes(Array.from(new Set(viewerDigestable)));
    };

    const clearViewer = () => {
        onEnzymesChange?.([]);
    };

    const clearDigest = () => {
        setDigestEnzymes([]);
        setFragments([]);
        setError(null);
    };

    const runDigest = async () => {
        if (digestEnzymes.length === 0) return;

        setLoading(true);
        setError(null);

        try {
            const enzymes = digestEnzymes.map((name) => {
                const definition = getRestrictionEnzyme(name);
                return {
                    name,
                    site: definition?.site || '',
                };
            });

            const payload: Record<string, unknown> = {
                enzymes,
                is_circular: sequenceData.circular,
                save: false,
            };

            if (sequenceId) {
                payload.sequence_id = sequenceId;
            } else {
                payload.sequence = sequenceData.sequence;
                payload.name = sequenceData.name;
                payload.sequence_type = sequenceData.sequenceType === 'rna' ? 'rna' : 'dna';
            }

            const response = await fetch('/api/molbio/digest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });

            if (!response.ok) {
                throw new Error(`Digest failed: ${response.status}`);
            }

            const data = await response.json();
            const nextFragments = data.fragments || [];
            setFragments(nextFragments);
            onDigestComplete?.(nextFragments);

            const regions: HighlightedRegion[] = [];
            nextFragments.forEach((fragment: DigestFragment, index: number) => {
                const color = index % 2 === 0 ? '#38bdf8' : '#34d399';
                const length = fragment.length ?? fragment.sequence?.length ?? Math.max(0, fragment.end - fragment.start);
                const label = `Fragment ${index + 1} (${length.toLocaleString()} bp)`;

                if ((fragment as DigestFragment & { wraps_origin?: boolean }).wraps_origin && sequenceData.sequence.length > 0) {
                    if (fragment.start < sequenceData.sequence.length) {
                        regions.push({
                            start: fragment.start,
                            end: sequenceData.sequence.length,
                            color,
                            label,
                        });
                    }
                    if (fragment.end > 0) {
                        regions.push({
                            start: 0,
                            end: fragment.end,
                            color,
                            label,
                        });
                    }
                } else {
                    regions.push({
                        start: fragment.start,
                        end: fragment.end,
                        color,
                        label,
                    });
                }
            });
            onHighlight(regions);
        } catch (caught) {
            setError(caught instanceof Error ? caught.message : 'Unknown error');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="digest-panel space-y-4 p-3 text-sm">
            <div>
                <h4 className="font-semibold text-slate-200">Restriction Analysis</h4>
                <p className="mt-1 text-xs text-slate-500">
                    Filter by cutter behavior, move a filtered set onto the map, or build a digest basket explicitly.
                </p>
            </div>

            <div className="grid grid-cols-3 gap-2">
                <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-2">
                    <div className="text-[11px] uppercase tracking-wide text-slate-500">Map Overlay</div>
                    <div className="mt-1 text-lg font-semibold text-slate-100">{viewerCount}</div>
                    <div className="text-[11px] text-slate-500">{totalVisibleCuts} visible cut sites</div>
                </div>
                <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-2">
                    <div className="text-[11px] uppercase tracking-wide text-slate-500">Digest Basket</div>
                    <div className="mt-1 text-lg font-semibold text-slate-100">{digestCount}</div>
                    <div className="text-[11px] text-slate-500">explicit enzymes selected</div>
                </div>
                <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-2">
                    <div className="text-[11px] uppercase tracking-wide text-slate-500">Unique Cutters</div>
                    <div className="mt-1 text-lg font-semibold text-cyan-300">{uniqueCutterCount}</div>
                    <div className="text-[11px] text-slate-500">1-cut enzymes on this construct</div>
                </div>
            </div>

            <div className="space-y-2">
                <input
                    type="text"
                    value={searchQuery}
                    onChange={(event) => setSearchQuery(event.target.value)}
                    placeholder="Search enzyme, site, category, or tag..."
                    className="w-full rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm focus:border-cyan-500 focus:outline-none"
                />

                <div className="space-y-1.5">
                    <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Cut Filter</div>
                    <div className="flex flex-wrap gap-1.5">
                        {CUT_FILTER_OPTIONS.map((option) => (
                            <FilterButton
                                key={option.value}
                                active={cutFilter === option.value}
                                disabled={option.value === 'selection' && !selectionActive}
                                label={option.label}
                                onClick={() => setCutFilter(option.value)}
                            />
                        ))}
                    </div>
                </div>

                <div className="space-y-1.5">
                    <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Class Filter</div>
                    <div className="flex flex-wrap gap-1.5">
                        {GROUP_FILTER_OPTIONS.map((option) => (
                            <FilterButton
                                key={option.value}
                                active={groupFilter === option.value}
                                label={option.label}
                                onClick={() => setGroupFilter(option.value)}
                            />
                        ))}
                    </div>
                </div>

                {selectionActive && selectionLabel && (
                    <div className="rounded-lg border border-cyan-500/25 bg-cyan-500/8 px-3 py-2 text-xs text-cyan-200">
                        Active selection: {selectionLabel} ({selectionBp.toLocaleString()} bp)
                    </div>
                )}

                {groupFilter === 'nicking' && (
                    <div className="rounded-lg border border-amber-500/30 bg-amber-500/8 px-3 py-2 text-xs text-amber-200">
                        Nicking enzymes are indexed for discovery and cut-site counting. SeqViz overlays and digest simulation remain disabled for them until strand-specific nick support is added.
                    </div>
                )}
            </div>

            <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                <div className="mb-2 flex items-center justify-between">
                    <div>
                        <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">Filtered Enzymes</div>
                        <div className="text-[11px] text-slate-500">{filteredEnzymes.length} match the current filters</div>
                    </div>
                    <div className="flex flex-wrap justify-end gap-1.5">
                        <button
                            onClick={replaceViewerWithFiltered}
                            disabled={filteredViewerNames.length === 0}
                            className="rounded-md border border-blue-500/35 bg-blue-500/10 px-2 py-1 text-[11px] font-medium text-blue-200 hover:bg-blue-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            Map = Filtered
                        </button>
                        <button
                            onClick={addFilteredToDigest}
                            disabled={filteredDigestNames.length === 0}
                            className="rounded-md border border-amber-500/35 bg-amber-500/10 px-2 py-1 text-[11px] font-medium text-amber-200 hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            Add Filtered To Digest
                        </button>
                    </div>
                </div>

                <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
                    {filteredEnzymes.length === 0 ? (
                        <div className="rounded-lg border border-dashed border-slate-700 px-3 py-5 text-center text-sm text-slate-500">
                            No enzymes match the current filters.
                        </div>
                    ) : (
                        filteredEnzymes.map((enzyme) => (
                            <EnzymeRow
                                key={enzyme.name}
                                enzyme={enzyme}
                                selectionActive={selectionActive}
                                isViewerSelected={selectedEnzymes.includes(enzyme.name)}
                                isDigestSelected={digestEnzymes.includes(enzyme.name)}
                                onViewerToggle={() => toggleViewerEnzyme(enzyme.name)}
                                onDigestToggle={() => toggleDigestEnzyme(enzyme.name)}
                            />
                        ))
                    )}
                </div>
            </div>

            <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                <div className="mb-2 flex items-center justify-between">
                    <div>
                        <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">Digest Basket</div>
                        <div className="text-[11px] text-slate-500">Use explicit enzymes here instead of hidden gestures.</div>
                    </div>
                    <div className="flex gap-1.5">
                        <button
                            onClick={copyViewerSetToDigest}
                            disabled={selectedEnzymes.length === 0}
                            className="rounded-md border border-slate-600 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            Use Map Set
                        </button>
                        <button
                            onClick={clearViewer}
                            disabled={viewerCount === 0}
                            className="rounded-md border border-slate-600 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            Clear Map
                        </button>
                        <button
                            onClick={clearDigest}
                            disabled={digestCount === 0}
                            className="rounded-md border border-slate-600 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            Clear Digest
                        </button>
                    </div>
                </div>

                {digestCount > 0 ? (
                    <div className="mb-3 flex flex-wrap gap-1.5">
                        {digestEnzymes.map((name) => (
                            <BasketChip key={name} label={name} onRemove={() => toggleDigestEnzyme(name)} />
                        ))}
                    </div>
                ) : (
                    <div className="mb-3 rounded-lg border border-dashed border-slate-700 px-3 py-4 text-center text-xs text-slate-500">
                        Add enzymes from the filtered list, or seed the basket from the current map overlay.
                    </div>
                )}

                <button
                    onClick={runDigest}
                    disabled={loading || digestCount === 0}
                    className="w-full rounded-lg bg-cyan-600 py-2 text-sm font-medium text-white transition-colors hover:bg-cyan-500 disabled:cursor-not-allowed disabled:bg-slate-600"
                >
                    {loading ? 'Digesting...' : `Run Digest (${digestCount} enzyme${digestCount === 1 ? '' : 's'})`}
                </button>
            </div>

            {error && (
                <div className="rounded-lg border border-red-800 bg-red-900/40 p-2 text-sm text-red-200">
                    {error}
                </div>
            )}

            {fragments.length > 0 && (
                <div className="space-y-3">
                    <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                        <div className="mb-2 flex items-center justify-between">
                            <h5 className="text-sm font-medium text-slate-200">Fragments</h5>
                            <span className="text-[11px] text-slate-500">{fragments.length} exact sequence fragments</span>
                        </div>
                        <div className="max-h-44 space-y-1 overflow-y-auto">
                            {fragments
                                .map((fragment) => ({
                                    ...fragment,
                                    size: fragment.length ?? fragment.sequence?.length ?? Math.abs(fragment.end - fragment.start),
                                }))
                                .sort((left, right) => right.size - left.size)
                                .map((fragment, index) => (
                                    <div
                                        key={`${fragment.start}-${fragment.end}-${index}`}
                                        className="grid grid-cols-[auto_1fr_auto] gap-2 rounded-lg bg-slate-800/70 px-2 py-1.5 text-xs"
                                    >
                                        <span className="text-slate-400">#{index + 1}</span>
                                        <span className="truncate text-slate-300">
                                            {fragment.start.toLocaleString()} → {fragment.end.toLocaleString()}
                                        </span>
                                        <span className="font-mono text-cyan-300">{fragment.size.toLocaleString()} bp</span>
                                    </div>
                                ))}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
