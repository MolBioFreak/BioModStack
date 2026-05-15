/**
 * SequenceViewer - Seqviz-based DNA/RNA sequence visualization
 *
 * Replaces OVE Editor with clean, modern implementation.
 */

import { SeqViz } from "seqviz";
import {
    useEffect,
    useMemo,
    useRef,
    type MouseEvent as ReactMouseEvent,
} from "react";
import type { NucleotideMoleculeOrientation, NucleotideMoleculeStrandedness, PrimerTmSettings } from '../../lib/api';
import {
    getSeqVizTouchRotationWheelDelta,
    installSeqVizTouchBridge,
    shouldEnableSeqVizTouchBridge,
} from './utils/seqVizTouch';
import { COLOR_PALETTES } from './sequenceViewerConstants';
import {
    displayStrandForMoleculeOrientation,
    sequenceForDisplayStrand,
    transformDirectionForDisplayStrand,
    transformRangeForDisplayStrand,
    type NucleotideDisplayStrand,
} from './utils/nucleotides';

// ═══════════════════════════════════════════════════════════════════════════════
// COLOR PALETTES
// ═══════════════════════════════════════════════════════════════════════════════

export type ColorPaletteName =
    | 'classic'      // Traditional 4-color scheme
    | 'gc_at'        // GC vs AT grouping (blue/red)
    | 'purine_pyrimidine'  // Purines vs Pyrimidines
    | 'muted'        // Softer, less saturated
    | 'vivid'        // High contrast, saturated
    | 'monochrome'   // Grayscale for printing
    | 'colorblind'   // Deuteranopia-safe
    | 'rasmol';      // RasMol/Jmol convention

export interface BpColors {
    A: string;
    T: string;
    G: string;
    C: string;
    U?: string;
    [key: string]: string | undefined;
}



// ═══════════════════════════════════════════════════════════════════════════════
// TYPES
// ═══════════════════════════════════════════════════════════════════════════════

export interface Feature {
    id: string;
    name: string;
    type: string;
    start: number;
    end: number;
    strand: 1 | -1;
    color?: string;
    description?: string;
    notes?: Record<string, unknown>;
    qualifiers?: Record<string, unknown>;
    provenance?: Record<string, unknown>;
    segments?: Array<{
        start: number;
        end: number;
    }>;
}

export interface Primer {
    id: string;
    name: string;
    sequence: string;
    sequenceType?: 'dna' | 'rna';
    start: number;
    end: number;
    strand: 1 | -1;
    tm?: number;
    gc_percent?: number;
    tm_algorithm?: string;
    tm_salt_correction?: string;
    tm_settings?: PrimerTmSettings;
    notes?: Record<string, unknown>;
    provenance?: Record<string, unknown>;
    sites?: Array<{
        start: number;
        end: number;
        strand: 1 | -1;
        tm?: number;
        note?: string;
    }>;
}

export interface Translation {
    start: number;
    end: number;
    strand: 1 | -1;
    frame?: 1 | 2 | 3;  // Reading frame (1-3 for both + and - strand)
}

export interface AnalysisTrack {
    id: string;
    name: string;
    kind: 'reactivity' | 'coverage' | 'mismatch' | 'custom';
    description?: string;
    color?: string;
    sourceFormat?: string;
    sourceName?: string;
    sourceUrl?: string;
    normalization?: string;
    values: Array<number | null>;
    minValue?: number | null;
    maxValue?: number | null;
    createdAt?: string;
}

export interface SequenceData {
    name: string;
    description?: string;
    sequence: string;
    circular: boolean;
    sequenceType: 'dna' | 'rna' | 'protein';
    moleculeStrandedness?: NucleotideMoleculeStrandedness;
    moleculeOrientation?: NucleotideMoleculeOrientation;
    moleculeLabel?: string;
    features: Feature[];
    primers?: Primer[];
    translations?: Translation[];
    analysisTracks?: AnalysisTrack[];
    organism?: string;
    accession?: string;
    sourceFile?: string;
    parentId?: string | null;
    operation?: string | null;
    operationParams?: Record<string, unknown> | null;
    version?: number | null;
}

export interface VisibilityState {
    features: boolean;
    primers: boolean;
    cutsites: boolean;
    translations: boolean;
    reverseComplement: boolean;
}

export interface SelectionInfo {
    start: number;
    end: number;
    clockwise?: boolean;
    type?: string;
    name?: string;
    annotationId?: string;
}

interface SequenceViewerProps {
    sequenceData: SequenceData;
    visibility: VisibilityState;
    selectedEnzymes?: string[];
    searchQuery?: string;
    selection?: SelectionInfo | null;
    onSelection?: (sel: SelectionInfo) => void;
    onSearch?: (results: { start: number; end: number }[]) => void;
    onContextMenu?: (event: ReactMouseEvent<HTMLDivElement>) => void;
    highlightedRegions?: { start: number; end: number; color: string }[];
    className?: string;
    viewMode?: 'linear' | 'circular' | 'both' | 'both_flip';
    colorPalette?: ColorPaletteName;
    visibleFrames?: Set<1 | 2 | 3 | -1 | -2 | -3>;
    activeDisplayStrand?: NucleotideDisplayStrand;
}

// ═══════════════════════════════════════════════════════════════════════════════
// HELPER FUNCTIONS
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Get color based on Tm value - green for optimal range
 */
function getTmColor(tm?: number): string {
    if (!tm) return "#94a3b8";  // Gray for unknown
    if (tm >= 55 && tm <= 65) return "#22c55e";  // Green - optimal
    if (tm >= 50 && tm < 55) return "#eab308";   // Yellow - low
    if (tm > 65 && tm <= 72) return "#f97316";   // Orange - high
    return "#ef4444";  // Red - extreme
}

// ═══════════════════════════════════════════════════════════════════════════════
// COMPONENT
// ═══════════════════════════════════════════════════════════════════════════════

export function SequenceViewer({
    sequenceData,
    visibility,
    selectedEnzymes = [],
    searchQuery,
    selection,
    onSelection,
    onSearch,
    onContextMenu,
    highlightedRegions,
    className,
    viewMode,
    colorPalette = 'classic',
    visibleFrames = new Set([1]),
    activeDisplayStrand,
}: SequenceViewerProps) {
    const nucleotideSequenceType = sequenceData.sequenceType === 'rna' ? 'rna' : 'dna';
    const sourceDisplayStrand = sequenceData.sequenceType === 'protein'
        ? 'plus'
        : displayStrandForMoleculeOrientation(sequenceData.moleculeOrientation);
    const resolvedDisplayStrand = activeDisplayStrand ?? sourceDisplayStrand;
    const sequenceLength = sequenceData.sequence.length;

    const featureAnnotations = useMemo(() => {
        return sequenceData.features.flatMap((feature) => {
            const segments = feature.segments && feature.segments.length > 0
                ? feature.segments
                : [{ start: feature.start, end: feature.end }];

            return segments.map((segment, index) => {
                const displayRange = transformRangeForDisplayStrand(
                    segment.start,
                    segment.end,
                    sequenceLength,
                    sourceDisplayStrand,
                    resolvedDisplayStrand,
                );

                return {
                    name: segments.length > 1 ? `${feature.name} [${index + 1}/${segments.length}]` : feature.name,
                    start: displayRange.start,
                    end: displayRange.end,
                    direction: transformDirectionForDisplayStrand(feature.strand, sourceDisplayStrand, resolvedDisplayStrand),
                    color: feature.color || "#3b82f6",
                    type: feature.type,
                    id: segments.length > 1 ? `${feature.id}::segment:${index}` : feature.id,
                };
            });
        });
    }, [resolvedDisplayStrand, sequenceData.features, sequenceLength, sourceDisplayStrand]);

    // Build annotations array based on visibility toggles
    const annotations = useMemo(() => {
        const result: Array<{
            name: string;
            start: number;
            end: number;
            direction: 1 | -1;
            color: string;
            type?: string;
            id?: string;
        }> = [];

        if (visibility.features) {
            result.push(...featureAnnotations);
        }

        if (visibility.primers && sequenceData.primers) {
            result.push(...sequenceData.primers.map(p => {
                const displayRange = transformRangeForDisplayStrand(
                    p.start,
                    p.end,
                    sequenceLength,
                    sourceDisplayStrand,
                    resolvedDisplayStrand,
                );

                return {
                    name: p.tm
                        ? `${p.name} (Tm: ${p.tm.toFixed(1)}°C)`
                        : p.name,
                    start: displayRange.start,
                    end: displayRange.end,
                    direction: transformDirectionForDisplayStrand(p.strand, sourceDisplayStrand, resolvedDisplayStrand),
                    color: getTmColor(p.tm),
                    type: "primer"
                };
            }));
        }

        return result;
    }, [featureAnnotations, resolvedDisplayStrand, sequenceData.primers, sequenceLength, sourceDisplayStrand, visibility.features, visibility.primers]);

    // Build translations array if visible - filter by selected reading frames
    // Also filter overlapping ORFs to prevent visual chaos
    const translations = useMemo(() => {
        if (!visibility.translations || !sequenceData.translations) return [];

        // First filter by visible frames
        const frameFiltered = sequenceData.translations.filter(t => {
            // Compute reading frame: frame is 1, 2, or 3 based on start position mod 3
            // Combined with strand direction to get -3/-2/-1 or +1/+2/+3
            const baseFrame = (t.frame ?? ((t.start % 3) + 1)) as 1 | 2 | 3;
            const combinedFrame = (t.strand === 1 ? baseFrame : -baseFrame) as 1 | 2 | 3 | -1 | -2 | -3;
            return visibleFrames.has(combinedFrame);
        });

        // Sort by length (longest first) and filter out overlapping ORFs
        // This prevents the visual overload when many ORFs overlap
        const sorted = [...frameFiltered].sort((a, b) => (b.end - b.start) - (a.end - a.start));
        const nonOverlapping: typeof sorted = [];

        for (const orf of sorted) {
            // Check if this ORF overlaps with unknown already selected
            const overlaps = nonOverlapping.some(existing => {
                // Same strand and positions overlap by more than 50%
                if (existing.strand !== orf.strand) return false;
                const overlapStart = Math.max(existing.start, orf.start);
                const overlapEnd = Math.min(existing.end, orf.end);
                if (overlapEnd <= overlapStart) return false;
                const overlapLen = overlapEnd - overlapStart;
                const shorterLen = Math.min(existing.end - existing.start, orf.end - orf.start);
                return (overlapLen / shorterLen) > 0.5;
            });

            if (!overlaps && nonOverlapping.length < 6) {
                nonOverlapping.push(orf);
            }
        }

        return nonOverlapping.map((t, i) => {
            const displayRange = transformRangeForDisplayStrand(
                t.start,
                t.end,
                sequenceLength,
                sourceDisplayStrand,
                resolvedDisplayStrand,
            );

            return {
                name: `ORF ${i + 1}`,
                start: displayRange.start,
                end: displayRange.end,
                direction: transformDirectionForDisplayStrand(t.strand, sourceDisplayStrand, resolvedDisplayStrand)
            };
        });
    }, [resolvedDisplayStrand, sequenceData.translations, sequenceLength, sourceDisplayStrand, visibility.translations, visibleFrames]);

    // Build the sequence that SeqViz should render for the selected display strand.
    // Stored coordinates remain in the imported/source strand; display-only reverse
    // complementation is handled here and selection/annotation coordinates are
    // transformed at the viewer boundary.
    const displaySequence = useMemo(() => {
        if (sequenceData.sequenceType === 'protein') {
            return sequenceData.sequence;
        }

        return sequenceForDisplayStrand(
            sequenceData.sequence,
            nucleotideSequenceType,
            sourceDisplayStrand,
            resolvedDisplayStrand,
        );
    }, [nucleotideSequenceType, resolvedDisplayStrand, sequenceData.sequence, sequenceData.sequenceType, sourceDisplayStrand]);

    const viewerRef = useRef<HTMLDivElement | null>(null);
    const activePaletteColors = COLOR_PALETTES[colorPalette].colors as Record<string, string>;

    const seqVizSelection = useMemo(() => {
        if (!selection) {
            return undefined;
        }

        const displaySelectionRange = transformRangeForDisplayStrand(
            selection.start,
            selection.end,
            sequenceLength,
            sourceDisplayStrand,
            resolvedDisplayStrand,
        );

        return {
            start: displaySelectionRange.start,
            end: displaySelectionRange.end,
            clockwise: selection.clockwise ?? true,
            // SeqViz treats externally controlled selections without a type as
            // programmatic and scrolls the linear viewer on every update. Keeping
            // a SEQ marker makes mouse-drag selection behave like native SeqViz
            // user selection instead of fighting the drag gesture.
            type: selection.type || 'SEQ',
        } as unknown as { start: number; end: number; clockwise?: boolean };
    }, [resolvedDisplayStrand, selection, sequenceLength, sourceDisplayStrand]);

    const displayHighlightedRegions = useMemo(() => {
        if (!highlightedRegions || highlightedRegions.length === 0) {
            return highlightedRegions;
        }

        return highlightedRegions.map((region) => {
            const displayRange = transformRangeForDisplayStrand(
                region.start,
                region.end,
                sequenceLength,
                sourceDisplayStrand,
                resolvedDisplayStrand,
            );
            return {
                ...region,
                start: displayRange.start,
                end: displayRange.end,
            };
        });
    }, [highlightedRegions, resolvedDisplayStrand, sequenceLength, sourceDisplayStrand]);

    const touchBridgeEnabled = useMemo(() => {
        if (typeof navigator === 'undefined') {
            return false;
        }

        const coarsePointer = typeof window !== 'undefined'
            && typeof window.matchMedia === 'function'
            && window.matchMedia('(pointer: coarse)').matches;

        return shouldEnableSeqVizTouchBridge({
            maxTouchPoints: navigator.maxTouchPoints,
            coarsePointer,
        });
    }, []);

    const resolvedViewerMode = viewMode || (sequenceData.circular ? 'both' : 'linear');
    const seqVizSeqType = sequenceData.sequenceType === 'protein' ? 'aa' : sequenceData.sequenceType;
    const viewerSequenceKey = useMemo(() => {
        const head = displaySequence.slice(0, 24);
        const tail = displaySequence.slice(-24);
        const topology = sequenceData.circular ? 'circular' : 'linear';
        const viewerStrand = `${sourceDisplayStrand}->${resolvedDisplayStrand}`;
        return `${sequenceData.name}:${sequenceData.sequenceType}:${sequenceData.moleculeLabel || ''}:${topology}:${viewerStrand}:${displaySequence.length}:${head}:${tail}:${resolvedViewerMode}:${colorPalette}`;
    }, [colorPalette, displaySequence, resolvedDisplayStrand, resolvedViewerMode, sequenceData.circular, sequenceData.moleculeLabel, sequenceData.name, sequenceData.sequenceType, sourceDisplayStrand]);
    const showTouchRotationControls = touchBridgeEnabled
        && sequenceData.circular
        && resolvedViewerMode !== 'linear';

    useEffect(() => {
        if (!touchBridgeEnabled || !viewerRef.current) {
            return undefined;
        }

        return installSeqVizTouchBridge(viewerRef.current);
    }, [touchBridgeEnabled]);

    const handleRotatePlasmid = (direction: 'left' | 'right') => {
        const circularViewer = viewerRef.current?.querySelector<HTMLElement>('.la-vz-viewer-circular');
        if (!circularViewer) {
            return;
        }

        circularViewer.dispatchEvent(new WheelEvent('wheel', {
            bubbles: true,
            cancelable: true,
            deltaY: getSeqVizTouchRotationWheelDelta(direction),
        }));
    };

    return (
        <div
            ref={viewerRef}
            className={`sequence-viewer ${className || ''}`}
            style={{ height: '100%', width: '100%', position: 'relative' }}
            onContextMenu={onContextMenu}
        >
            {showTouchRotationControls && (
                <div className="pointer-events-none absolute right-3 top-3 z-20 flex items-center gap-2">
                    <button
                        type="button"
                        data-seqviz-touch-control="true"
                        onClick={() => handleRotatePlasmid('left')}
                        className="pointer-events-auto rounded-full border border-slate-600 bg-slate-900/85 px-3 py-1.5 text-sm font-medium text-slate-100 shadow-lg transition-colors hover:bg-slate-800"
                        aria-label="Rotate plasmid left"
                        title="Rotate plasmid left"
                    >
                        ↺
                    </button>
                    <button
                        type="button"
                        data-seqviz-touch-control="true"
                        onClick={() => handleRotatePlasmid('right')}
                        className="pointer-events-auto rounded-full border border-slate-600 bg-slate-900/85 px-3 py-1.5 text-sm font-medium text-slate-100 shadow-lg transition-colors hover:bg-slate-800"
                        aria-label="Rotate plasmid right"
                        title="Rotate plasmid right"
                    >
                        ↻
                    </button>
                </div>
            )}
            <SeqViz
                key={viewerSequenceKey}
                name={sequenceData.name}
                seq={displaySequence}
                seqType={seqVizSeqType}
                annotations={annotations}
                translations={translations}
                enzymes={visibility.cutsites ? selectedEnzymes : []}
                viewer={resolvedViewerMode}
                showComplement={visibility.reverseComplement}
                rotateOnScroll
                disableExternalFonts
                zoom={{ linear: 62 }}

                // Base pair colors from selected palette
                bpColors={activePaletteColors}

                selection={seqVizSelection}

                // Selection handling with type info
                onSelection={(sel) => {
                    if (onSelection && sel && typeof sel.start === 'number' && typeof sel.end === 'number') {
                        const sourceRange = transformRangeForDisplayStrand(
                            sel.start,
                            sel.end,
                            sequenceLength,
                            resolvedDisplayStrand,
                            sourceDisplayStrand,
                        );
                        onSelection({
                            start: sourceRange.start,
                            end: sourceRange.end,
                            clockwise: sel.clockwise,
                            type: sel.type,
                            name: sel.name,
                            annotationId: 'id' in sel ? String((sel as { id?: string }).id || '') || undefined : undefined,
                        });
                    }
                }}

                // Search capability
                search={searchQuery ? { query: searchQuery, mismatch: 0 } : undefined}
                onSearch={onSearch}

                highlights={displayHighlightedRegions}

                // Styling - seqviz needs explicit height
                style={{ height: "100%", width: "100%" }}
            />
        </div>
    );
}
