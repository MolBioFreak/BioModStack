import { useEffect, useMemo, useState } from 'react';
import {
    alignMolBioSequences,
    type SequenceAlignmentResult,
} from '../../../lib/api';
import type { Feature, HighlightedRegion, SelectionInfo, SequenceData } from '../types';
import { parseSequenceInput } from '../utils/nucleotides';

interface AlignmentPanelProps {
    sequenceData: SequenceData;
    selection: SelectionInfo | null;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onAddFeatures: (features: Feature[]) => void;
}

type AlignmentMode = 'placement' | 'local' | 'global';
type StrandMode = 'auto' | 'forward' | 'reverse';

type DisplayRegion = {
    start: number;
    end: number;
    color: string;
    label: string;
};

function formatHalfOpenSpan(start: number, end: number, wrapsOrigin = false) {
    const displayStart = start + 1;
    const displayEnd = Math.max(end, wrapsOrigin ? end : displayStart);
    return wrapsOrigin ? `${displayStart}->${displayEnd}` : `${displayStart}-${displayEnd}`;
}

function resolveQueryLabel(explicitName: string, parsedName: string) {
    const trimmed = explicitName.trim();
    if (trimmed) return trimmed;
    if (parsedName && parsedName !== 'Untitled Sequence') return parsedName;
    return 'Query sequence';
}

function buildHighlightRegions(
    start: number,
    end: number,
    wrapsOrigin: boolean,
    sequenceLength: number,
    color: string,
    label: string,
): DisplayRegion[] {
    if (!wrapsOrigin || end >= start) {
        return [{
            start,
            end: Math.max(end, start + 1),
            color,
            label,
        }];
    }

    return [
        {
            start,
            end: sequenceLength,
            color,
            label,
        },
        {
            start: 0,
            end: Math.max(end, 1),
            color,
            label,
        },
    ];
}

function alignmentBlocks(
    result: SequenceAlignmentResult,
    blockSize = 70,
    referenceLength?: number,
) {
    const blocks: Array<{
        referenceStart: number;
        referenceEnd: number;
        queryStart: number;
        queryEnd: number;
        reference: string;
        midline: string;
        query: string;
    }> = [];

    let referencePosition = result.reference_start;
    let queryPosition = result.query_start;
    const wraps = Boolean(result.reference_wraps_origin);
    const circularLength = wraps && referenceLength ? referenceLength : undefined;

    for (let offset = 0; offset < result.reference_aligned.length; offset += blockSize) {
        const reference = result.reference_aligned.slice(offset, offset + blockSize);
        const midline = result.midline.slice(offset, offset + blockSize);
        const query = result.query_aligned.slice(offset, offset + blockSize);

        const referenceAdvance = reference.replace(/-/g, '').length;
        const queryAdvance = query.replace(/-/g, '').length;

        let referenceEnd = referencePosition + referenceAdvance;
        if (circularLength) {
            referencePosition %= circularLength;
            referenceEnd %= circularLength;
        }
        const queryEnd = queryPosition + queryAdvance;

        blocks.push({
            referenceStart: referencePosition,
            referenceEnd,
            queryStart: queryPosition,
            queryEnd,
            reference,
            midline,
            query,
        });

        referencePosition = circularLength
            ? (referencePosition + referenceAdvance) % circularLength
            : referencePosition + referenceAdvance;
        queryPosition = queryEnd;
    }

    return blocks;
}

export function AlignmentPanel({
    sequenceData,
    selection,
    onHighlight,
    onAddFeatures,
}: AlignmentPanelProps) {
    const [referenceScope, setReferenceScope] = useState<'full' | 'selection'>('full');
    const [mode, setMode] = useState<AlignmentMode>('placement');
    const [strand, setStrand] = useState<StrandMode>('auto');
    const [circularReference, setCircularReference] = useState(sequenceData.circular);
    const [queryName, setQueryName] = useState('');
    const [queryRaw, setQueryRaw] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<SequenceAlignmentResult | null>(null);

    const selectionRange = useMemo(() => {
        if (!selection || selection.start === selection.end) return null;
        const start = Math.min(selection.start, selection.end);
        const end = Math.max(selection.start, selection.end);
        return { start, end };
    }, [selection]);

    useEffect(() => {
        if (referenceScope === 'selection') {
            setCircularReference(false);
        } else {
            setCircularReference(sequenceData.circular);
        }
    }, [referenceScope, sequenceData.circular]);

    const referenceSequence = useMemo(() => {
        if (referenceScope === 'selection' && selectionRange) {
            return sequenceData.sequence.slice(selectionRange.start, selectionRange.end);
        }
        return sequenceData.sequence;
    }, [referenceScope, selectionRange, sequenceData.sequence]);

    const referenceName = referenceScope === 'selection' && selectionRange
        ? `${sequenceData.name} ${selectionRange.start + 1}-${selectionRange.end}`
        : sequenceData.name;
    const referenceOffset = referenceScope === 'selection' && selectionRange ? selectionRange.start : 0;
    const parsedQuery = useMemo(() => parseSequenceInput(queryRaw), [queryRaw]);
    const queryLabel = resolveQueryLabel(queryName, parsedQuery.name);
    const invalidQuery = parsedQuery.invalidCharacters.length > 0;
    const effectiveCircularReference = referenceScope === 'full' && circularReference && sequenceData.circular;
    const blocks = useMemo(
        () => (result ? alignmentBlocks(result, 70, referenceScope === 'full' ? sequenceData.sequence.length : undefined) : []),
        [referenceScope, result, sequenceData.sequence.length],
    );

    const baseHighlights = useMemo<HighlightedRegion[]>(() => {
        if (!result) return [];
        const start = referenceOffset + result.reference_start;
        const end = referenceOffset + result.reference_end;
        return buildHighlightRegions(
            start,
            end,
            Boolean(result.reference_wraps_origin) && referenceScope === 'full',
            sequenceData.sequence.length,
            '#22d3ee',
            `${queryLabel}: aligned span`,
        );
    }, [queryLabel, referenceOffset, referenceScope, result, sequenceData.sequence.length]);

    useEffect(() => {
        if (baseHighlights.length > 0) {
            onHighlight(baseHighlights);
            return () => onHighlight([]);
        }
        onHighlight([]);
        return undefined;
    }, [baseHighlights, onHighlight]);

    const canRun = Boolean(referenceSequence) && Boolean(parsedQuery.sequence) && !invalidQuery;

    const runAlignment = async () => {
        if (!referenceSequence || !parsedQuery.sequence) {
            setError('Provide both a reference and a query sequence before running alignment.');
            return;
        }
        if (invalidQuery) {
            setError(`Query contains invalid characters: ${parsedQuery.invalidCharacters.join(', ')}`);
            return;
        }

        setLoading(true);
        setError(null);
        try {
            const response = await alignMolBioSequences({
                reference_name: referenceName,
                reference_sequence: referenceSequence,
                query_name: queryLabel,
                query_sequence: parsedQuery.sequence,
                settings: {
                    mode,
                    strand,
                    reference_is_circular: effectiveCircularReference,
                },
            });
            setResult(response.data);
        } catch (alignmentError) {
            setError(alignmentError instanceof Error ? alignmentError.message : 'Alignment failed');
        } finally {
            setLoading(false);
        }
    };

    const annotateVariants = () => {
        if (!result || result.variants.length === 0) return;

        const features = result.variants.flatMap((variant, index) => {
            const absoluteStart = referenceOffset + variant.start;
            const absoluteEnd = referenceOffset + variant.end;
            const color = variant.type === 'substitution'
                ? '#f59e0b'
                : variant.type === 'deletion'
                    ? '#ef4444'
                    : '#06b6d4';

            const baseFeature = {
                name: `${queryLabel}: ${variant.label}`,
                type: 'misc_difference' as const,
                strand: 1 as const,
                color,
                description: `Alignment-derived ${variant.type} against ${queryLabel}`,
                notes: {
                    source: 'alignment',
                    query_name: queryLabel,
                    variant_type: variant.type,
                    reference: variant.reference,
                    query: variant.query,
                    query_start: variant.query_start,
                    query_end: variant.query_end,
                    alignment_mode: result.mode,
                    alignment_strand: result.strand,
                    reference_wraps_origin: Boolean(variant.reference_wraps_origin),
                },
            };

            if (variant.reference_wraps_origin && referenceScope === 'full') {
                return [
                    {
                        id: `align_variant_${Date.now().toString(36)}_${index}_a`,
                        ...baseFeature,
                        start: absoluteStart,
                        end: sequenceData.sequence.length,
                    },
                    {
                        id: `align_variant_${Date.now().toString(36)}_${index}_b`,
                        ...baseFeature,
                        start: 0,
                        end: Math.max(absoluteEnd, 1),
                    },
                ];
            }

            const boundedStart = Math.max(0, Math.min(absoluteStart, Math.max(sequenceData.sequence.length - 1, 0)));
            const boundedEnd = variant.type === 'insertion'
                ? Math.min(sequenceData.sequence.length, boundedStart + 1)
                : Math.max(boundedStart + 1, Math.min(absoluteEnd, sequenceData.sequence.length));
            return [{
                id: `align_variant_${Date.now().toString(36)}_${index}`,
                ...baseFeature,
                start: boundedStart,
                end: boundedEnd,
            }];
        });

        onAddFeatures(features);
    };

    return (
        <div className="space-y-4 p-3 text-sm">
            <div>
                <h4 className="font-semibold text-slate-200">Alignment</h4>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                    Use `placement` to map a fragment onto a construct, `local` for best internal hits, and `global` only for true end-to-end comparisons.
                </p>
            </div>

            <div className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                <div className="grid gap-2 sm:grid-cols-2">
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Reference span</span>
                        <select
                            value={referenceScope}
                            onChange={(event) => setReferenceScope(event.target.value as 'full' | 'selection')}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5"
                        >
                            <option value="full">Whole construct</option>
                            <option value="selection" disabled={!selectionRange}>Current selection</option>
                        </select>
                    </label>
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Mode</span>
                        <select
                            value={mode}
                            onChange={(event) => setMode(event.target.value as AlignmentMode)}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5"
                        >
                            <option value="placement">Place query on reference</option>
                            <option value="local">Best local span</option>
                            <option value="global">Full-length global</option>
                        </select>
                    </label>
                </div>

                <div className="grid gap-2 sm:grid-cols-2">
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Strand</span>
                        <select
                            value={strand}
                            onChange={(event) => setStrand(event.target.value as StrandMode)}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5"
                        >
                            <option value="auto">Auto-pick best strand</option>
                            <option value="forward">Forward only</option>
                            <option value="reverse">Reverse complement only</option>
                        </select>
                    </label>
                    <label className="flex items-end gap-2 rounded border border-slate-800 bg-slate-950/70 px-3 py-2 text-xs text-slate-400">
                        <input
                            type="checkbox"
                            className="rounded border-slate-600 bg-slate-800"
                            checked={effectiveCircularReference}
                            disabled={referenceScope !== 'full' || !sequenceData.circular}
                            onChange={(event) => setCircularReference(event.target.checked)}
                        />
                        Treat reference as circular
                    </label>
                </div>

                <div className="rounded border border-slate-800 bg-slate-950/70 px-3 py-2 text-xs text-slate-400">
                    {referenceName} • {referenceSequence.length.toLocaleString()} nt
                    {effectiveCircularReference ? ' • circular reference placement enabled' : ''}
                </div>

                <label className="space-y-1">
                    <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Query label</span>
                    <input
                        value={queryName}
                        onChange={(event) => setQueryName(event.target.value)}
                        placeholder={parsedQuery.name !== 'Untitled Sequence' ? parsedQuery.name : 'Query sequence'}
                        className="w-full rounded border border-slate-600 bg-slate-800 px-3 py-2"
                    />
                </label>

                <label className="space-y-1">
                    <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Query sequence</span>
                    <textarea
                        value={queryRaw}
                        onChange={(event) => setQueryRaw(event.target.value)}
                        rows={8}
                        className="w-full rounded border border-slate-600 bg-slate-800 px-3 py-2 font-mono text-xs"
                    />
                </label>

                <div className={`rounded border px-3 py-2 text-xs ${invalidQuery ? 'border-red-800 bg-red-900/20 text-red-300' : 'border-slate-800 bg-slate-950/70 text-slate-400'}`}>
                    Parsed query: {parsedQuery.sequence.length.toLocaleString()} nt
                    {invalidQuery
                        ? ` • invalid characters present: ${parsedQuery.invalidCharacters.join(', ')}`
                        : ''}
                </div>

                <button
                    onClick={() => void runAlignment()}
                    disabled={loading || !canRun}
                    className="w-full rounded-lg bg-blue-600 px-3 py-2 font-medium text-white transition-colors hover:bg-blue-500 disabled:opacity-50"
                >
                    {loading ? 'Aligning…' : 'Run Alignment'}
                </button>
            </div>

            {error && (
                <div className="rounded border border-red-800 bg-red-900/30 px-3 py-2 text-sm text-red-300">
                    {error}
                </div>
            )}

            {result && (
                <>
                    <div className="rounded-xl border border-slate-700 bg-slate-900/50 px-3 py-2 text-xs text-slate-400">
                        {result.mode} • {result.strand} strand • Ref {formatHalfOpenSpan(referenceOffset + result.reference_start, referenceOffset + result.reference_end, Boolean(result.reference_wraps_origin))}
                        {' '}• Query {formatHalfOpenSpan(result.query_start, result.query_end)}
                    </div>

                    <div className="grid grid-cols-2 gap-2">
                        <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                            <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Aligned Identity</div>
                            <div className="mt-2 text-xl font-semibold text-slate-100">{(result.identity_pct ?? result.ungapped_identity).toFixed(2)}%</div>
                            <div className="mt-1 text-xs text-slate-400">{result.matches} matches • {result.mismatches} mismatches</div>
                        </div>
                        <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                            <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Query Covered</div>
                            <div className="mt-2 text-xl font-semibold text-slate-100">{result.query_coverage.toFixed(1)}%</div>
                            <div className="mt-1 text-xs text-slate-400">
                                {result.query_aligned_bases ?? (result.query_end - result.query_start)} aligned bases
                            </div>
                        </div>
                    </div>

                    <div className="grid grid-cols-2 gap-2">
                        <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                            <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Reference Span</div>
                            <div className="mt-2 text-xl font-semibold text-slate-100">{result.reference_coverage.toFixed(1)}%</div>
                            <div className="mt-1 text-xs text-slate-400">
                                {result.reference_aligned_bases ?? 0} covered bases
                                {result.reference_wraps_origin ? ' • wraps origin' : ''}
                            </div>
                        </div>
                        <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                            <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Alignment Score</div>
                            <div className="mt-2 text-xl font-semibold text-slate-100">{result.score.toFixed(1)}</div>
                            <div className="mt-1 text-xs text-slate-400">
                                {result.aligned_columns ?? result.alignment_length} aligned columns
                                {(result.query_soft_clip_left || result.query_soft_clip_right)
                                    ? ` • query soft clips ${result.query_soft_clip_left ?? 0}/${result.query_soft_clip_right ?? 0}`
                                    : ''}
                            </div>
                        </div>
                    </div>

                    <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                        <div className="flex items-center justify-between">
                            <div>
                                <div className="font-medium text-slate-200">Variant Calls</div>
                                <div className="mt-1 text-xs text-slate-500">
                                    {result.variants.length} difference event{result.variants.length === 1 ? '' : 's'}
                                </div>
                            </div>
                            <button
                                onClick={annotateVariants}
                                disabled={result.variants.length === 0}
                                className="rounded-lg bg-amber-600 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-amber-500 disabled:opacity-50"
                            >
                                Annotate Variants
                            </button>
                        </div>

                        <div className="mt-3 space-y-2 max-h-48 overflow-y-auto">
                            {result.variants.length === 0 ? (
                                <div className="text-xs text-slate-500">No mismatches or indels within the aligned span.</div>
                            ) : result.variants.map((variant) => (
                                <button
                                    key={`${variant.type}:${variant.start}:${variant.end}:${variant.query}`}
                                    onMouseEnter={() => {
                                        const regions = buildHighlightRegions(
                                            referenceOffset + variant.start,
                                            referenceOffset + variant.end,
                                            Boolean(variant.reference_wraps_origin) && referenceScope === 'full',
                                            sequenceData.sequence.length,
                                            variant.type === 'substitution' ? '#f59e0b' : variant.type === 'deletion' ? '#ef4444' : '#06b6d4',
                                            variant.label,
                                        );
                                        onHighlight([...baseHighlights, ...regions]);
                                    }}
                                    onMouseLeave={() => onHighlight(baseHighlights)}
                                    className="w-full rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2 text-left transition-colors hover:border-slate-600"
                                >
                                    <div className="flex items-center justify-between gap-3">
                                        <span className="font-medium text-slate-200">{variant.label}</span>
                                        <span className="text-xs uppercase text-slate-500">{variant.type}</span>
                                    </div>
                                    <div className="mt-1 text-xs text-slate-400">
                                        Ref {formatHalfOpenSpan(referenceOffset + variant.start, referenceOffset + variant.end, Boolean(variant.reference_wraps_origin))}
                                        {' '}• Query {formatHalfOpenSpan(variant.query_start, variant.query_end)}
                                    </div>
                                </button>
                            ))}
                        </div>
                    </div>

                    <div className="rounded-xl border border-slate-700 bg-slate-950/70 p-3">
                        <div className="mb-2 font-medium text-slate-200">Rendered Alignment</div>
                        <div className="space-y-3 max-h-[26rem] overflow-auto font-mono text-xs">
                            {blocks.map((block) => (
                                <div key={`${block.referenceStart}:${block.queryStart}`} className="space-y-1 rounded border border-slate-800 bg-slate-900/60 p-2">
                                    <div className="text-slate-300">
                                        <span className="mr-3 inline-block w-20 text-slate-500">Ref {block.referenceStart + referenceOffset + 1}</span>
                                        {block.reference}
                                        <span className="ml-3 text-slate-500">{block.referenceEnd + referenceOffset}</span>
                                    </div>
                                    <div>
                                        <span className="mr-3 inline-block w-20 text-slate-600"> </span>
                                        <span className="text-emerald-300">{block.midline}</span>
                                    </div>
                                    <div className="text-cyan-200">
                                        <span className="mr-3 inline-block w-20 text-slate-500">Qry {block.queryStart + 1}</span>
                                        {block.query}
                                        <span className="ml-3 text-slate-500">{block.queryEnd}</span>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}
