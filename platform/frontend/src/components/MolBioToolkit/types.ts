/**
 * Shared TypeScript types for MolBioToolkit
 */

// Import types from SequenceViewer for re-export and use
import type {
    Feature,
    Primer,
    Translation,
    SequenceData,
    VisibilityState,
    SelectionInfo
} from './SequenceViewer';

export {
    EMPTY_SEQUENCE,
    DEFAULT_VISIBILITY
} from './SequenceViewer';

// Re-export types
export type {
    Feature,
    Primer,
    Translation,
    SequenceData,
    VisibilityState,
    SelectionInfo
};

// ═══════════════════════════════════════════════════════════════════════════════
// API RESPONSE TYPES (matching nucleotide_sequences.py)
// ═══════════════════════════════════════════════════════════════════════════════

export interface NucleotideSequenceResponse {
    id: string;
    name: string;
    description: string | null;
    sequence: string;
    sequence_type: 'dna' | 'rna';
    is_circular: boolean;
    length: number;
    features: Feature[] | null;
    primers: Primer[] | null;
    organism: string | null;
    accession: string | null;
    source_file: string | null;
    gc_content: number | null;
    parent_id: string | null;
    operation: string | null;
    operation_params: Record<string, unknown> | null;
    version: number | null;
    entity_kind?: string;
    topology?: 'circular' | 'linear';
    created_at: string;
    updated_at: string | null;
}

export interface NucleotideSequenceListItem {
    id: string;
    name: string;
    description: string | null;
    sequence_type: 'dna' | 'rna';
    is_circular: boolean;
    length: number;
    gc_content: number | null;
    feature_count: number;
    organism?: string | null;
    accession?: string | null;
    source_file?: string | null;
    entity_kind?: string;
    topology?: 'circular' | 'linear';
    created_at: string;
    updated_at?: string | null;
}

// ═══════════════════════════════════════════════════════════════════════════════
// ENZYME TYPES
// ═══════════════════════════════════════════════════════════════════════════════

export interface EnzymeInfo {
    name: string;
    site: string;
    cutCount: number;
    overhangType: 'blunt' | '5_prime' | '3_prime';
    positions: number[];
}

export interface EnzymeFilter {
    cutCount?: { min: number; max: number };
    overhangType?: 'blunt' | '5_prime' | '3_prime' | 'all';
    suppliers?: string[];
    searchQuery?: string;
}

// ═══════════════════════════════════════════════════════════════════════════════
// DIGEST/PCR TYPES
// ═══════════════════════════════════════════════════════════════════════════════

export interface DigestFragment {
    sequence: string;
    start: number;
    end: number;
    length?: number;
    wraps_origin?: boolean;
}

export interface PCRProduct {
    id: string;
    name: string;
    sequence: string;
    length: number;
    forwardPrimer: Primer;
    reversePrimer: Primer;
    start?: number;
    end?: number;
    wrapsOrigin?: boolean;
}

// ═══════════════════════════════════════════════════════════════════════════════
// UI STATE TYPES
// ═══════════════════════════════════════════════════════════════════════════════

export type ActivePanel =
    | 'view'
    | 'digest'
    | 'pcr'
    | 'primers'
    | 'ligation'
    | 'features'
    | 'edit'
    | 'search'
    | null;

export interface HighlightedRegion {
    start: number;
    end: number;
    color: string;
    label?: string;
}
