/**
 * SequenceViewer - Seqviz-based DNA/RNA sequence visualization
 *
 * Replaces OVE Editor with clean, modern implementation.
 */

import { SeqViz } from "seqviz";
import {
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
    type KeyboardEvent as ReactKeyboardEvent,
    type MouseEvent as ReactMouseEvent,
} from "react";
import type { NucleotideMoleculeOrientation, NucleotideMoleculeStrandedness, PrimerTmSettings } from '../../lib/api';
import type { RestrictionAnalysisResponse, RestrictionOccurrence } from '../../lib/restrictionAnalysis';
import {
    getSeqVizTouchRotationWheelDelta,
    installSeqVizTouchBridge,
    shouldEnableSeqVizTouchBridge,
} from './utils/seqVizTouch';
import { COLOR_PALETTES } from './sequenceViewerConstants';
import {
    displayStrandForMoleculeOrientation,
    sequenceForDisplayStrand,
    shouldReverseComplementForDisplay,
    transformDirectionForDisplayStrand,
    transformRangeForDisplayStrand,
    type NucleotideDisplayStrand,
} from './utils/nucleotides';
import {
    getPrimerRenderableSites,
    getSelectionRanges,
    mapSeqVizSelectionToSource,
} from './utils/selectionActions';

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

interface NickingMapAnnotation {
    name: string;
    start: number;
    end: number;
    direction: 1 | -1;
    color: string;
    type: 'nickase';
}

export function buildNickingMapAnnotations({
    occurrences,
    selectedEnzymes,
    sequenceLength,
    sourceDisplayStrand,
    resolvedDisplayStrand,
}: {
    occurrences: RestrictionOccurrence[];
    selectedEnzymes: string[];
    sequenceLength: number;
    sourceDisplayStrand: NucleotideDisplayStrand;
    resolvedDisplayStrand: NucleotideDisplayStrand;
}): NickingMapAnnotation[] {
    const selected = new Set(selectedEnzymes);
    return occurrences.flatMap((occurrence) => {
        if (!selected.has(occurrence.enzyme_id)) return [];
        return occurrence.nicks
            .filter((nick) => nick.status === 'complete' && nick.boundary !== null)
            .map((nick) => {
                const sourceDirection = (nick.strand === 'top'
                    ? (occurrence.orientation === 'forward' ? 1 : -1)
                    : (occurrence.orientation === 'forward' ? -1 : 1)) as 1 | -1;
                const displayDirection = transformDirectionForDisplayStrand(
                    sourceDirection,
                    sourceDisplayStrand,
                    resolvedDisplayStrand,
                );
                const displayRange = transformRangeForDisplayStrand(
                    nick.boundary!,
                    Math.min(sequenceLength, nick.boundary! + 1),
                    sequenceLength,
                    sourceDisplayStrand,
                    resolvedDisplayStrand,
                );
                return {
                    name: occurrence.canonical_name,
                    start: displayRange.start,
                    end: displayRange.end,
                    direction: displayDirection,
                    color: displayDirection === 1 ? '#f472b6' : '#a78bfa',
                    type: 'nickase' as const,
                };
            });
    });
}

export function buildRestrictionMapAnnotations({ occurrences, selectedEnzymes, sequenceLength, sourceDisplayStrand, resolvedDisplayStrand }: {
    occurrences: RestrictionOccurrence[];
    selectedEnzymes: string[];
    sequenceLength: number;
    sourceDisplayStrand: NucleotideDisplayStrand;
    resolvedDisplayStrand: NucleotideDisplayStrand;
}) {
    const selected = new Set(selectedEnzymes);
    return occurrences.flatMap((occurrence) => selected.has(occurrence.enzyme_id)
        ? occurrence.site_segments.map(([start, end], segmentIndex) => {
            const range = transformRangeForDisplayStrand(start, end, sequenceLength, sourceDisplayStrand, resolvedDisplayStrand);
            const sourceDirection = occurrence.orientation === 'forward' ? 1 : -1;
            const semantic = occurrence.nicks.length > 0 ? 'nick' : occurrence.double_strand_events.length > 0 ? 'DSB' : 'recognition';
            return {
                name: `${occurrence.canonical_name} · ${occurrence.certainty} ${semantic}`,
                start: range.start,
                end: range.end,
                direction: transformDirectionForDisplayStrand(sourceDirection, sourceDisplayStrand, resolvedDisplayStrand),
                color: occurrence.certainty === 'possible' ? '#f59e0b' : semantic === 'nick' ? '#f472b6' : '#22d3ee',
                type: `restriction_${semantic}`,
                id: `${occurrence.occurrence_id}:segment:${segmentIndex}`,
            };
        })
        : []);
}

export function getNickingMarkerGeometry({
    position,
    direction,
    sequenceLength,
    centralIndex,
    width,
    height,
    viewerMode,
}: {
    position: number;
    direction: 1 | -1;
    sequenceLength: number;
    centralIndex: number;
    width: number;
    height: number;
    viewerMode: 'circular' | 'both' | 'both_flip';
}) {
    const paneWidth = viewerMode === 'circular' ? width : width / 2;
    const paneOffsetX = viewerMode === 'both_flip' ? width / 2 : 0;
    const centerX = paneOffsetX + paneWidth / 2;
    const centerY = height / 2;
    const plasmidRadius = Math.min(height, paneWidth) * 0.34;
    const radius = direction === 1
        ? plasmidRadius + 36
        : Math.max(8, plasmidRadius - 18);
    const adjustedPosition = position - centralIndex;
    const radians = (adjustedPosition / Math.max(1, sequenceLength)) * Math.PI * 2 - Math.PI / 2;
    const lineStartX = centerX + Math.cos(radians) * plasmidRadius;
    const lineStartY = centerY + Math.sin(radians) * plasmidRadius;
    const textX = centerX + Math.cos(radians) * radius;
    const textY = centerY + Math.sin(radians) * radius;
    return {
        x: textX,
        y: textY,
        textX,
        textY,
        lineStartX,
        lineStartY,
        textAnchor: textX < centerX ? 'end' as const : 'start' as const,
        centerX,
        centerY,
        radius,
        plasmidRadius,
    };
}

export interface LabelRectangle {
    x: number;
    y: number;
    width: number;
    height: number;
}

export function rectanglesOverlap(a: LabelRectangle, b: LabelRectangle, padding = 0): boolean {
    return a.x < b.x + b.width + padding
        && a.x + a.width + padding > b.x
        && a.y < b.y + b.height + padding
        && a.y + a.height + padding > b.y;
}

export function resolveNickingLabelCollisionOffset({
    label,
    obstacles,
    tangent,
    radial,
    bounds,
    acceptOffset,
}: {
    label: LabelRectangle;
    obstacles: LabelRectangle[];
    tangent: { x: number; y: number };
    radial: { x: number; y: number };
    bounds: { width: number; height: number };
    acceptOffset?: (offset: { x: number; y: number }) => boolean;
}) {
    const candidates = [{ x: 0, y: 0 }];
    for (let step = 1; step <= 16; step += 1) {
        const tangentDistance = step * 18;
        candidates.push(
            { x: tangent.x * tangentDistance, y: tangent.y * tangentDistance },
            { x: -tangent.x * tangentDistance, y: -tangent.y * tangentDistance },
        );
        if (step <= 8) {
            const radialDistance = step * 14;
            candidates.push(
                { x: radial.x * radialDistance, y: radial.y * radialDistance },
                {
                    x: radial.x * radialDistance + tangent.x * tangentDistance,
                    y: radial.y * radialDistance + tangent.y * tangentDistance,
                },
                {
                    x: radial.x * radialDistance - tangent.x * tangentDistance,
                    y: radial.y * radialDistance - tangent.y * tangentDistance,
                },
            );
        }
    }

    return candidates.find((offset) => {
        const shifted = {
            ...label,
            x: label.x + offset.x,
            y: label.y + offset.y,
        };
        const withinBounds = shifted.x >= 4
            && shifted.y >= 4
            && shifted.x + shifted.width <= bounds.width - 4
            && shifted.y + shifted.height <= bounds.height - 4;
        return withinBounds
            && (acceptOffset?.(offset) ?? true)
            && !obstacles.some((obstacle) => rectanglesOverlap(shifted, obstacle, 4));
    }) || { x: 0, y: 0 };
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
    length?: number;
    segments?: Array<{ start: number; end: number }>;
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
    restrictionAnalysis?: RestrictionAnalysisResponse | null;
    searchQuery?: string;
    selection?: SelectionInfo | null;
    onSelection?: (sel: SelectionInfo) => void;
    onSearch?: (results: { start: number; end: number }[]) => void;
    onContextMenu?: (
        event: ReactMouseEvent<HTMLDivElement> | ReactKeyboardEvent<HTMLDivElement>
    ) => void;
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
    restrictionAnalysis = null,
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
    const normalizedSequenceType = sequenceData.sequenceType.toLowerCase();
    const nucleotideSequenceType = normalizedSequenceType === 'rna' ? 'rna' : 'dna';
    const sourceDisplayStrand = normalizedSequenceType === 'protein'
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
            result.push(...sequenceData.primers.flatMap(p => getPrimerRenderableSites(
                p,
                sequenceLength,
                sequenceData.circular,
            ).map((site) => {
                const displayRange = transformRangeForDisplayStrand(
                    site.start,
                    site.end,
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
                    direction: transformDirectionForDisplayStrand(site.strand, sourceDisplayStrand, resolvedDisplayStrand),
                    color: getTmColor(site.tm ?? p.tm),
                    type: "primer"
                };
            })));
        }

        if (visibility.cutsites && restrictionAnalysis) {
            result.push(...buildRestrictionMapAnnotations({
                occurrences: restrictionAnalysis.analysis.occurrences,
                selectedEnzymes,
                sequenceLength,
                sourceDisplayStrand,
                resolvedDisplayStrand,
            }));
        }

        return result;
    }, [featureAnnotations, resolvedDisplayStrand, restrictionAnalysis, selectedEnzymes, sequenceData.primers, sequenceLength, sourceDisplayStrand, visibility.cutsites, visibility.features, visibility.primers]);

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
        const translationSegments = (translation: Translation) => (
            translation.segments && translation.segments.length > 0
                ? translation.segments
                : [{ start: translation.start, end: translation.end }]
        );
        const translationLength = (translation: Translation) => (
            translation.length
            ?? translationSegments(translation).reduce(
                (total, segment) => total + (segment.end - segment.start),
                0,
            )
        );
        const overlapLength = (left: Translation, right: Translation) => (
            translationSegments(left).reduce((total, leftSegment) => (
                total + translationSegments(right).reduce((subtotal, rightSegment) => (
                    subtotal + Math.max(
                        0,
                        Math.min(leftSegment.end, rightSegment.end)
                            - Math.max(leftSegment.start, rightSegment.start),
                    )
                ), 0)
            ), 0)
        );
        const sorted = [...frameFiltered].sort((a, b) => translationLength(b) - translationLength(a));
        const nonOverlapping: typeof sorted = [];

        for (const orf of sorted) {
            // Check if this ORF overlaps with unknown already selected
            const overlaps = nonOverlapping.some(existing => {
                // Same strand and positions overlap by more than 50%
                if (existing.strand !== orf.strand) return false;
                const overlapLen = overlapLength(existing, orf);
                if (overlapLen <= 0) return false;
                const shorterLen = Math.min(translationLength(existing), translationLength(orf));
                return (overlapLen / shorterLen) > 0.5;
            });

            if (!overlaps && nonOverlapping.length < 6) {
                nonOverlapping.push(orf);
            }
        }

        return nonOverlapping.flatMap((t, i) => (
            translationSegments(t).map((segment) => {
                const displayRange = transformRangeForDisplayStrand(
                    segment.start,
                    segment.end,
                    sequenceLength,
                    sourceDisplayStrand,
                    resolvedDisplayStrand,
                );

                return {
                    name: `ORF ${i + 1}`,
                    start: displayRange.start,
                    end: displayRange.end,
                    direction: transformDirectionForDisplayStrand(t.strand, sourceDisplayStrand, resolvedDisplayStrand),
                };
            })
        ));
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
    const nickingOverlayRef = useRef<SVGSVGElement | null>(null);
    const previousSelectionRef = useRef<SelectionInfo | null | undefined>(selection);
    const selectionPointerButtonRef = useRef<number | null>(null);
    const pendingPointerSelectionRef = useRef<SelectionInfo | null>(null);
    const selectionCommitFrameRef = useRef<number | null>(null);
    const [selectionResetVersion, setSelectionResetVersion] = useState(0);
    const [circularCentralIndex, setCircularCentralIndex] = useState(0);
    const [viewerSize, setViewerSize] = useState({ width: 0, height: 0 });
    const [nickingLabelOffsets, setNickingLabelOffsets] = useState<Record<string, { x: number; y: number }>>({});
    const activePaletteColors = COLOR_PALETTES[colorPalette].colors as Record<string, string>;

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

    const durableSelectionHighlights = useMemo(() => (
        getSelectionRanges(selection, sequenceLength, sequenceData.circular).map((range) => {
            const displayRange = transformRangeForDisplayStrand(
                range.start,
                range.end,
                sequenceLength,
                sourceDisplayStrand,
                resolvedDisplayStrand,
            );
            return {
                start: displayRange.start,
                end: displayRange.end,
                color: '#22d3ee',
            };
        })
    ), [resolvedDisplayStrand, selection, sequenceData.circular, sequenceLength, sourceDisplayStrand]);

    const mergedHighlightedRegions = useMemo(
        () => [...(displayHighlightedRegions || []), ...durableSelectionHighlights],
        [displayHighlightedRegions, durableSelectionHighlights],
    );

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

    const resolvedViewerMode = viewMode || 'both';
    const seqVizSeqType = normalizedSequenceType === 'protein' ? 'aa' : nucleotideSequenceType;
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
    const nickingMapMarkers = useMemo(() => (
        visibility.cutsites && sequenceData.circular && resolvedViewerMode !== 'linear'
            ? buildNickingMapAnnotations({
                occurrences: restrictionAnalysis?.analysis.occurrences ?? [],
                selectedEnzymes,
                sequenceLength,
                sourceDisplayStrand,
                resolvedDisplayStrand,
            })
            : []
    ), [resolvedDisplayStrand, resolvedViewerMode, restrictionAnalysis, selectedEnzymes, sequenceData.circular, sequenceLength, sourceDisplayStrand, visibility.cutsites]);

    useEffect(() => {
        const viewer = viewerRef.current;
        if (!viewer) return undefined;
        const measure = () => {
            const bounds = viewer.getBoundingClientRect();
            setViewerSize({ width: bounds.width, height: bounds.height });
        };
        measure();
        const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
        observer?.observe(viewer);
        window.addEventListener('resize', measure);
        return () => {
            observer?.disconnect();
            window.removeEventListener('resize', measure);
        };
    }, [resolvedViewerMode]);

    useEffect(() => {
        const viewer = viewerRef.current;
        const overlay = nickingOverlayRef.current;
        if (!viewer || !overlay || nickingMapMarkers.length === 0 || sequenceLength === 0 || resolvedViewerMode === 'linear') {
            if (Object.keys(nickingLabelOffsets).length > 0) {
                setNickingLabelOffsets({});
            }
            return undefined;
        }

        const frame = window.requestAnimationFrame(() => {
            const viewerBounds = viewer.getBoundingClientRect();
            const obstacles: LabelRectangle[] = [...viewer.querySelectorAll<SVGTextElement>('svg text')]
                .filter((text) => !overlay.contains(text))
                .map((text) => text.getBoundingClientRect())
                .filter((bounds) => bounds.width > 0 && bounds.height > 0)
                .map((bounds) => ({
                    x: bounds.x - viewerBounds.x,
                    y: bounds.y - viewerBounds.y,
                    width: bounds.width,
                    height: bounds.height,
                }));
            const nextOffsets: Record<string, { x: number; y: number }> = {};

            [...overlay.querySelectorAll<SVGGElement>('g[data-nick-key]')].forEach((group) => {
                const key = group.dataset.nickKey;
                const markerIndex = Number(group.dataset.nickIndex);
                const marker = nickingMapMarkers[markerIndex];
                const text = group.querySelector<SVGTextElement>('text');
                if (!key || !marker || !text) return;

                const currentOffset = nickingLabelOffsets[key] || { x: 0, y: 0 };
                const bounds = text.getBoundingClientRect();
                const label = {
                    x: bounds.x - viewerBounds.x - currentOffset.x,
                    y: bounds.y - viewerBounds.y - currentOffset.y,
                    width: bounds.width,
                    height: bounds.height,
                };
                const radians = ((marker.start - circularCentralIndex) / sequenceLength) * Math.PI * 2 - Math.PI / 2;
                const tangent = { x: -Math.sin(radians), y: Math.cos(radians) };
                const radialSign = marker.direction === 1 ? 1 : -1;
                const radial = {
                    x: Math.cos(radians) * radialSign,
                    y: Math.sin(radians) * radialSign,
                };
                const geometry = getNickingMarkerGeometry({
                    position: marker.start,
                    direction: marker.direction,
                    sequenceLength,
                    centralIndex: circularCentralIndex,
                    width: viewerSize.width,
                    height: viewerSize.height,
                    viewerMode: resolvedViewerMode,
                });
                const offset = resolveNickingLabelCollisionOffset({
                    label,
                    obstacles,
                    tangent,
                    radial,
                    bounds: viewerSize,
                    acceptOffset: (candidate) => {
                        const radius = Math.hypot(
                            geometry.textX + candidate.x - geometry.centerX,
                            geometry.textY + candidate.y - geometry.centerY,
                        );
                        return marker.direction === 1
                            ? radius >= geometry.plasmidRadius + 8
                            : radius <= geometry.plasmidRadius - 8;
                    },
                });
                nextOffsets[key] = offset;
                obstacles.push({
                    ...label,
                    x: label.x + offset.x,
                    y: label.y + offset.y,
                });
            });

            const keys = Object.keys(nextOffsets);
            const unchanged = keys.length === Object.keys(nickingLabelOffsets).length
                && keys.every((key) => (
                    nextOffsets[key].x === nickingLabelOffsets[key]?.x
                    && nextOffsets[key].y === nickingLabelOffsets[key]?.y
                ));
            if (!unchanged) {
                setNickingLabelOffsets(nextOffsets);
            }
        });

        return () => window.cancelAnimationFrame(frame);
    }, [circularCentralIndex, nickingLabelOffsets, nickingMapMarkers, resolvedViewerMode, sequenceLength, viewerSize]);

    useEffect(() => {
        setCircularCentralIndex(0);
    }, [viewerSequenceKey]);

    const trackCircularRotation = useCallback((deltaY: number) => {
        if (!sequenceLength || deltaY === 0) return;
        let delta = Math.floor(sequenceLength * (deltaY / 5000));
        if (delta === 0) delta = deltaY > 0 ? 1 : -1;
        setCircularCentralIndex((current) => (current + delta + sequenceLength) % sequenceLength);
    }, [sequenceLength]);

    const flushPendingPointerSelection = useCallback(() => {
        selectionCommitFrameRef.current = null;
        const pendingSelection = pendingPointerSelectionRef.current;
        pendingPointerSelectionRef.current = null;
        if (pendingSelection) {
            onSelection?.(pendingSelection);
        }
    }, [onSelection]);

    const finishPointerSelection = useCallback(() => {
        if (selectionPointerButtonRef.current !== 0) {
            return;
        }
        if (selectionCommitFrameRef.current !== null) {
            window.cancelAnimationFrame(selectionCommitFrameRef.current);
        }
        selectionCommitFrameRef.current = window.requestAnimationFrame(() => {
            flushPendingPointerSelection();
            selectionPointerButtonRef.current = null;
        });
    }, [flushPendingPointerSelection]);

    const cancelPointerSelection = useCallback(() => {
        pendingPointerSelectionRef.current = null;
        selectionPointerButtonRef.current = null;
    }, []);

    useEffect(() => {
        window.addEventListener('pointerup', finishPointerSelection, true);
        window.addEventListener('pointercancel', cancelPointerSelection, true);
        window.addEventListener('blur', cancelPointerSelection);
        return () => {
            window.removeEventListener('pointerup', finishPointerSelection, true);
            window.removeEventListener('pointercancel', cancelPointerSelection, true);
            window.removeEventListener('blur', cancelPointerSelection);
            if (selectionCommitFrameRef.current !== null) {
                window.cancelAnimationFrame(selectionCommitFrameRef.current);
            }
        };
    }, [cancelPointerSelection, finishPointerSelection]);

    useEffect(() => {
        if (!touchBridgeEnabled || !viewerRef.current) {
            return undefined;
        }

        return installSeqVizTouchBridge(viewerRef.current);
    }, [touchBridgeEnabled]);

    useEffect(() => {
        if (previousSelectionRef.current && !selection) {
            // SeqViz has no clear-selection imperative API. It remains uncontrolled
            // for reliable dragging, so remount only when the operator explicitly
            // clears the durable selection.
            setSelectionResetVersion((current) => current + 1);
        }
        previousSelectionRef.current = selection;
    }, [selection]);

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
            style={{
                height: '100%',
                width: '100%',
                position: 'relative',
                overflowY: resolvedViewerMode === 'both' ? 'auto' : 'hidden',
            }}
            tabIndex={0}
            aria-label="Molecular sequence viewer. Use Shift+F10 or the Menu key for selection actions."
            onContextMenu={onContextMenu}
            onWheelCapture={(event) => {
                const target = event.target;
                if (target instanceof Element && target.closest('.la-vz-viewer-circular')) {
                    trackCircularRotation(event.deltaY);
                }
            }}
            onKeyDown={(event) => {
                if ((event.shiftKey && event.key === 'F10') || event.key === 'ContextMenu') {
                    onContextMenu?.(event);
                }
            }}
            onPointerDownCapture={(event) => {
                selectionPointerButtonRef.current = event.button;
                if (event.button === 0) {
                    pendingPointerSelectionRef.current = null;
                }
            }}
            onPointerUpCapture={finishPointerSelection}
            onPointerCancelCapture={cancelPointerSelection}
        >
            {!sequenceData.circular && resolvedViewerMode !== 'linear' && (
                <div
                    data-linear-circular-projection="true"
                    data-linear-break-marker="true"
                    className="pointer-events-none absolute top-2 z-20 flex -translate-x-1/2 flex-col items-center"
                    style={{ left: resolvedViewerMode === 'both' ? '25%' : '50%' }}
                    title="Linear projection break: end → 1; display only, stored molecule remains linear"
                >
                    <div className="rounded-md border-2 border-amber-300 bg-amber-950 px-3 py-1 text-center font-bold tracking-wide text-amber-100 shadow-[0_0_18px_rgba(251,191,36,0.55)]">
                        <div className="text-[11px] leading-none">LINEAR BREAK</div>
                        <div className="mt-1 font-mono text-[10px] leading-none">
                            {sequenceData.sequence.length.toLocaleString()} → 1
                        </div>
                    </div>
                    <div className="h-10 w-1 bg-amber-300 shadow-[0_0_10px_rgba(251,191,36,0.9)]" />
                    <div className="-mt-1 h-3 w-3 rotate-45 border-b-2 border-r-2 border-amber-300" />
                    <div className="mt-1 rounded bg-slate-950/90 px-2 py-0.5 text-[9px] font-medium text-amber-200 shadow">
                        Linear projection • stored topology remains linear
                    </div>
                </div>
            )}
            {showTouchRotationControls && (
                <div className="pointer-events-none absolute right-3 top-3 z-20 flex items-center gap-2">
                    <button
                        type="button"
                        data-seqviz-touch-control="true"
                        onClick={() => handleRotatePlasmid('left')}
                        className="pointer-events-auto inline-flex min-h-12 min-w-12 items-center justify-center rounded-full border border-slate-600 bg-slate-900/85 px-3 py-1.5 text-sm font-medium text-slate-100 shadow-lg transition-colors hover:bg-slate-800"
                        aria-label="Rotate plasmid left"
                        title="Rotate plasmid left"
                    >
                        ↺
                    </button>
                    <button
                        type="button"
                        data-seqviz-touch-control="true"
                        onClick={() => handleRotatePlasmid('right')}
                        className="pointer-events-auto inline-flex min-h-12 min-w-12 items-center justify-center rounded-full border border-slate-600 bg-slate-900/85 px-3 py-1.5 text-sm font-medium text-slate-100 shadow-lg transition-colors hover:bg-slate-800"
                        aria-label="Rotate plasmid right"
                        title="Rotate plasmid right"
                    >
                        ↻
                    </button>
                </div>
            )}
            <SeqViz
                key={`${viewerSequenceKey}:selection-reset-${selectionResetVersion}`}
                name={sequenceData.name}
                seq={displaySequence}
                seqType={seqVizSeqType}
                annotations={annotations}
                translations={translations}
                enzymes={[]}
                viewer={resolvedViewerMode}
                showComplement={visibility.reverseComplement}
                rotateOnScroll
                disableExternalFonts
                zoom={{ linear: 62 }}

                // Base pair colors from selected palette
                bpColors={activePaletteColors}

                // Selection handling with type info
                onSelection={(sel) => {
                    if (onSelection && sel && typeof sel.start === 'number' && typeof sel.end === 'number') {
                        const sourceSelection = mapSeqVizSelectionToSource(
                            {
                                start: sel.start,
                                end: sel.end,
                                clockwise: sel.clockwise,
                                type: sel.type,
                                name: sel.name,
                                annotationId: 'id' in sel ? String((sel as { id?: string }).id || '') || undefined : undefined,
                            },
                            sequenceLength,
                            sequenceData.circular,
                            shouldReverseComplementForDisplay(sourceDisplayStrand, resolvedDisplayStrand),
                            selectionPointerButtonRef.current,
                        );
                        if (sourceSelection) {
                            if (selectionPointerButtonRef.current === 0) {
                                pendingPointerSelectionRef.current = sourceSelection;
                            } else {
                                onSelection(sourceSelection);
                            }
                        }
                    }
                }}

                // Search capability
                search={searchQuery ? { query: searchQuery, mismatch: 0 } : undefined}
                onSearch={onSearch}

                highlights={mergedHighlightedRegions}

                // Styling - seqviz needs explicit height
                style={{ height: "100%", width: "100%" }}
            />
            {nickingMapMarkers.length > 0
                && resolvedViewerMode !== 'linear'
                && viewerSize.width > 0
                && viewerSize.height > 0 && (
                <svg
                    ref={nickingOverlayRef}
                    data-nicking-map-overlay="true"
                    className="pointer-events-none absolute inset-0 z-10 overflow-visible"
                    aria-label="Strand-aware nicking map markers"
                    viewBox={`0 0 ${viewerSize.width} ${viewerSize.height}`}
                    width={viewerSize.width}
                    height={viewerSize.height}
                >
                    {nickingMapMarkers.map((marker, index) => {
                        const geometry = getNickingMarkerGeometry({
                            position: marker.start,
                            direction: marker.direction,
                            sequenceLength,
                            centralIndex: circularCentralIndex,
                            width: viewerSize.width,
                            height: viewerSize.height,
                            viewerMode: resolvedViewerMode,
                        });
                        const strand = marker.direction === 1 ? 'top' : 'bottom';
                        const markerKey = `${marker.name}:${marker.start}:${index}`;
                        const offset = nickingLabelOffsets[markerKey] || { x: 0, y: 0 };
                        const textX = geometry.textX + offset.x;
                        const textY = geometry.textY + offset.y;
                        return (
                            <g
                                key={markerKey}
                                data-nick-key={markerKey}
                                data-nick-index={index}
                                data-nick-strand={strand}
                                data-nick-position={marker.start}
                                aria-label={marker.name}
                            >
                                <title>{`${marker.name}: ${strand} strand nick`}</title>
                                <path
                                    className="la-vz-label-line"
                                    d={`M${geometry.lineStartX} ${geometry.lineStartY} L${textX} ${textY}`}
                                    fill="none"
                                    stroke="rgb(158, 170, 184)"
                                    strokeWidth="1"
                                    vectorEffect="non-scaling-stroke"
                                />
                                <text
                                    className="la-vz-circular-label"
                                    x={textX}
                                    y={textY}
                                    dominantBaseline="middle"
                                    textAnchor={geometry.textAnchor}
                                    style={{
                                        fontFamily: 'Roboto Mono, Monaco, monospace',
                                        fontSize: 12,
                                        fontWeight: 300,
                                        userSelect: 'none',
                                    }}
                                >
                                    {marker.name}
                                </text>
                            </g>
                        );
                    })}
                </svg>
            )}
        </div>
    );
}
