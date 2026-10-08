/**
 * SequenceViewer - Seqviz-based DNA/RNA sequence visualization
 * 
 * Replaces OVE Editor with clean, modern implementation.
 */

import { SeqViz } from "seqviz";
import { useMemo } from "react";

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
    notes?: Record<string, string>;
}

export interface Primer {
    id: string;
    name: string;
    sequence: string;
    start: number;
    end: number;
    strand: 1 | -1;
    tm?: number;
    gc_percent?: number;
}

export interface Translation {
    start: number;
    end: number;
    strand: 1 | -1;
}

export interface SequenceData {
    name: string;
    sequence: string;
    circular: boolean;
    sequenceType: 'dna' | 'rna' | 'protein';
    features: Feature[];
    primers?: Primer[];
    translations?: Translation[];
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
    onSelection?: (sel: SelectionInfo) => void;
    onSearch?: (results: { start: number; end: number }[]) => void;
    highlightedRegions?: { start: number; end: number; color: string }[];
    className?: string;
    viewMode?: 'linear' | 'circular' | 'both' | 'both_flip';
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
    onSelection,
    onSearch,
    highlightedRegions,
    className,
    viewMode
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

    // Build translations array if visible
    const translations = useMemo(() => {
        if (!visibility.translations || !sequenceData.translations) return [];
        return sequenceData.translations.map((t, i) => ({
            name: `ORF ${i + 1}`,
            start: t.start,
            end: t.end,
            direction: t.strand
        }));
    }, [sequenceData.translations, visibility.translations]);

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

                // Base pair colors for dark mode visibility
                bpColors={{
                    A: "#22c55e",  // Green for Adenine
                    T: "#ef4444",  // Red for Thymine  
                    G: "#f59e0b",  // Amber for Guanine
                    C: "#3b82f6",  // Blue for Cytosine
                    U: "#ec4899",  // Pink for Uracil (RNA)
                }}

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
    sequence: "",
    circular: false,
    sequenceType: "dna",
    features: [],
    primers: [],
    translations: []
};

export const DEFAULT_VISIBILITY: VisibilityState = {
    features: true,
    primers: true,
    cutsites: false,
    translations: false,
    reverseComplement: true
};
