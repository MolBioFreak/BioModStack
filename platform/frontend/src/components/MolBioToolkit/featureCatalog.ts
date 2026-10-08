export interface FeatureTypeDefinition {
    value: string;
    label: string;
    color: string;
    category: string;
}

export const FEATURE_TYPES: FeatureTypeDefinition[] = [
    { value: 'CDS', label: 'CDS', color: '#22c55e', category: 'Coding' },
    { value: 'gene', label: 'Gene', color: '#3b82f6', category: 'Coding' },
    { value: 'exon', label: 'Exon', color: '#10b981', category: 'Coding' },
    { value: 'promoter', label: 'Promoter', color: '#8b5cf6', category: 'Regulatory' },
    { value: 'enhancer', label: 'Enhancer', color: '#a855f7', category: 'Regulatory' },
    { value: 'terminator', label: 'Terminator', color: '#ef4444', category: 'Regulatory' },
    { value: "5'UTR", label: "5' UTR", color: '#06b6d4', category: 'Regulatory' },
    { value: "3'UTR", label: "3' UTR", color: '#0891b2', category: 'Regulatory' },
    { value: 'rep_origin', label: 'Origin of Replication', color: '#ec4899', category: 'Replication' },
    { value: 'oriT', label: 'oriT', color: '#db2777', category: 'Replication' },
    { value: 'resistance', label: 'Resistance Marker', color: '#dc2626', category: 'Marker' },
    { value: 'reporter', label: 'Reporter', color: '#65a30d', category: 'Marker' },
    { value: 'tag', label: 'Tag', color: '#f59e0b', category: 'Marker' },
    { value: 'primer_bind', label: 'Primer Binding Site', color: '#f59e0b', category: 'Binding' },
    { value: 'protein_bind', label: 'Protein Binding Site', color: '#0ea5e9', category: 'Binding' },
    { value: 'misc_feature', label: 'Misc Feature', color: '#6b7280', category: 'Other' },
    { value: 'misc_difference', label: 'Difference / Variant', color: '#fbbf24', category: 'Other' },
    { value: 'source', label: 'Source', color: '#94a3b8', category: 'Other' },
];

export const FEATURE_COLOR_PALETTE = [
    '#ef4444', '#f97316', '#f59e0b', '#eab308', '#84cc16',
    '#22c55e', '#10b981', '#14b8a6', '#06b6d4', '#0ea5e9',
    '#3b82f6', '#6366f1', '#8b5cf6', '#a855f7', '#d946ef',
    '#ec4899', '#f43f5e', '#64748b', '#475569', '#ffffff',
];

export function normalizeFeatureType(type: string): string {
    const trimmed = type.trim();
    if (!trimmed) return 'misc_feature';

    const compact = trimmed
        .replace(/[′’`]/g, "'")
        .toLowerCase()
        .replace(/[\s_'-]/g, '');
    if (compact === '5utr' || compact === '5primeutr') return "5'UTR";
    if (compact === '3utr' || compact === '3primeutr') return "3'UTR";

    return FEATURE_TYPES.find(
        (entry) => entry.value.toLowerCase() === trimmed.toLowerCase(),
    )?.value || trimmed;
}

export function getFeatureColor(type: string): string {
    const normalizedType = normalizeFeatureType(type);
    return FEATURE_TYPES.find((entry) => entry.value === normalizedType)?.color || '#6b7280';
}
