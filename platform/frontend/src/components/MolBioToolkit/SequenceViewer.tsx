/**
 * SequenceViewer - Seqviz-based DNA/RNA sequence visualization
 * 
 * Replaces OVE Editor with clean, modern implementation.
 */

import { SeqViz } from "seqviz";
import { useMemo, type MouseEvent as ReactMouseEvent } from "react";
import type { PrimerTmSettings } from '../../lib/api';

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

export const COLOR_PALETTES: Record<ColorPaletteName, { name: string; description: string; colors: BpColors }> = {
    classic: {
        name: 'Classic',
        description: 'Traditional 4-color scheme (A=green, T=red, G=amber, C=blue)',
        colors: { A: '#22c55e', T: '#ef4444', G: '#f59e0b', C: '#3b82f6', U: '#ec4899' }
    },
    gc_at: {
        name: 'GC vs AT',
        description: 'Group by base pairing: GC (blue/cyan) vs AT (red/orange)',
        colors: { A: '#ef4444', T: '#f97316', G: '#3b82f6', C: '#06b6d4', U: '#f97316' }
    },
    purine_pyrimidine: {
        name: 'Purine/Pyrimidine',
        description: 'Purines A+G (warm) vs Pyrimidines C+T (cool)',
        colors: { A: '#f97316', T: '#3b82f6', G: '#eab308', C: '#8b5cf6', U: '#8b5cf6' }
    },
    muted: {
        name: 'Muted',
        description: 'Softer colors, easier on eyes for long sessions',
        colors: { A: '#6ee7b7', T: '#fca5a5', G: '#fcd34d', C: '#93c5fd', U: '#f9a8d4' }
    },
    vivid: {
        name: 'Vivid',
        description: 'High saturation for maximum contrast',
        colors: { A: '#00ff00', T: '#ff0000', G: '#ffff00', C: '#0088ff', U: '#ff00ff' }
    },
    monochrome: {
        name: 'Monochrome',
        description: 'Grayscale for printing or colorblind accessibility',
        colors: { A: '#404040', T: '#808080', G: '#c0c0c0', C: '#606060', U: '#909090' }
    },
    colorblind: {
        name: 'Colorblind Safe',
        description: 'Optimized for deuteranopia (red-green colorblindness)',
        colors: { A: '#e69f00', T: '#56b4e9', G: '#cc79a7', C: '#0072b2', U: '#f0e442' }
    },
    rasmol: {
        name: 'RasMol',
        description: 'Traditional molecular visualization colors',
        colors: { A: '#a0a0ff', T: '#ff8c4b', G: '#ff7070', C: '#ffc832', U: '#ff8080' }
    }
};

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
    features: Feature[];
    primers?: Primer[];
    translations?: Translation[];
    analysisTracks?: AnalysisTrack[];
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
    visibleFrames = new Set([1])
}: SequenceViewerProps) {

    // Build annotations array based on visibility toggles
    const annotations = useMemo(() => {
        const result: Array<{
            name: string;
            start: number;
            end: number;
            direction: 1 | -1;
            color: string;
            type?: string;
        }> = [];

        if (visibility.features) {
            result.push(...sequenceData.features.map(f => ({
                name: f.name,
                start: f.start,
                end: f.end,
                direction: f.strand,
                color: f.color || "#3b82f6",
                type: f.type
            })));
        }

        if (visibility.primers && sequenceData.primers) {
            result.push(...sequenceData.primers.map(p => ({
                name: p.tm
                    ? `${p.name} (Tm: ${p.tm.toFixed(1)}°C)`
                    : p.name,
                start: p.start,
                end: p.end,
                direction: p.strand,
                color: getTmColor(p.tm),
                type: "primer"
            })));
        }

        return result;
    }, [sequenceData.features, sequenceData.primers, visibility.features, visibility.primers]);

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
            // Check if this ORF overlaps with any already selected
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

        return nonOverlapping.map((t, i) => ({
            name: `ORF ${i + 1}`,
            start: t.start,
            end: t.end,
            direction: t.strand
        }));
    }, [sequenceData.translations, visibility.translations, visibleFrames]);

    // Handle RNA display - convert T → U
    const displaySequence = useMemo(() => {
        if (sequenceData.sequenceType === 'rna') {
            return sequenceData.sequence.replace(/T/gi, 'U');
        }
        return sequenceData.sequence;
    }, [sequenceData.sequence, sequenceData.sequenceType]);

    return (
        <div
            className={`sequence-viewer ${className || ''}`}
            style={{ height: '100%', width: '100%', position: 'relative' }}
            onContextMenu={onContextMenu}
        >
            <SeqViz
                name={sequenceData.name}
                seq={displaySequence}
                annotations={annotations}
                translations={translations}
                enzymes={visibility.cutsites ? selectedEnzymes : []}
                viewer={viewMode || (sequenceData.circular ? "both" : "linear")}
                showComplement={visibility.reverseComplement}
                rotateOnScroll
                disableExternalFonts
                zoom={{ linear: 62 }}

                // Base pair colors from selected palette
                bpColors={COLOR_PALETTES[colorPalette].colors as Record<string, string>}

                selection={selection
                    ? {
                        start: selection.start,
                        end: selection.end,
                        clockwise: selection.clockwise,
                    }
                    : undefined}

                // Selection handling with type info
                onSelection={(sel) => {
                    if (onSelection && sel && typeof sel.start === 'number' && typeof sel.end === 'number') {
                        onSelection({
                            start: sel.start,
                            end: sel.end,
                            clockwise: sel.clockwise,
                            type: sel.type,
                            name: sel.name
                        });
                    }
                }}

                // Search capability
                search={searchQuery ? { query: searchQuery, mismatch: 0 } : undefined}
                onSearch={onSearch}

                highlights={highlightedRegions}

                // Styling - seqviz needs explicit height
                style={{ height: "100%", width: "100%" }}
            />
        </div>
    );
}

// ═══════════════════════════════════════════════════════════════════════════════
// DEFAULT STATES
// ═══════════════════════════════════════════════════════════════════════════════

export const EMPTY_SEQUENCE: SequenceData = {
    name: "Untitled Sequence",
    description: "",
    sequence: "",
    circular: false,
    sequenceType: "dna",
    features: [],
    primers: [],
    translations: [],
    analysisTracks: [],
};

export const DEFAULT_VISIBILITY: VisibilityState = {
    features: true,
    primers: true,
    cutsites: false,
    translations: false,
    reverseComplement: true
};
