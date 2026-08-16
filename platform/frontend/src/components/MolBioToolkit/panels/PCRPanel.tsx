/**
 * PCRPanel - PCR amplification tool
 */

import { useState, useMemo, useEffect, useCallback } from 'react';
import { useLocation } from 'react-router-dom';
import type { SequenceData, HighlightedRegion } from '../types';
import {
    calculatePrimerTm,
    fetchPcrExperimentRevision,
    fetchPcrExperimentRevisions,
    runPcrOperation,
    type PcrExperimentRevision,
    type PcrOperationResponse,
    type PrimerTmOptionsResponse,
    type PrimerTmResult,
    type PrimerTmSettings,
} from '../../../lib/api';
import { useGlobalExperimentContext } from '../../experiments/GlobalExperimentContext';
import { PrimerTmSettingsPanel } from '../PrimerTmSettingsPanel';
import {
    isValidNucleotideSequence,
    resolvePrimerBindings,
    sequenceUnitLabel,
} from '../utils/nucleotides';

interface PCRPanelProps {
    sequenceData: SequenceData;
    sequenceId: string | null;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onPCRComplete?: (product: { sequence: string; length: number; start?: number; end?: number; wrapsOrigin?: boolean }) => void;
    tmOptions: PrimerTmOptionsResponse | null;
    tmSettings: PrimerTmSettings;
    onTmSettingsChange: (settings: PrimerTmSettings) => void;
}

function formatTm(result: PrimerTmResult | null | undefined): string {
    if (!result || result.tm === null || Number.isNaN(result.tm)) {
        return 'n/a';
    }
    return `${result.tm.toFixed(1)}°C`;
}

function validatePcrRevision(
    experimentId: string,
    revisionId: string,
    revision: PcrExperimentRevision,
): PcrExperimentRevision {
    if (
        revision.experiment_id !== experimentId
        || revision.id !== revisionId
        || revision.reopen_destination.surface !== 'molbio-pcr-experiment-revision'
        || revision.reopen_destination.params.experiment_id !== experimentId
        || revision.reopen_destination.params.revision_id !== revisionId
    ) {
        throw new Error('PCR revision response identity does not match the exact requested experiment/revision pair.');
    }
    return revision;
}

function validatePcrRevisionList(
    experimentId: string,
    revisions: PcrExperimentRevision[],
): PcrExperimentRevision[] {
    revisions.forEach((revision) => validatePcrRevision(experimentId, revision.id, revision));
    return revisions;
}

function immutablePcrPayload(revision: PcrExperimentRevision): Record<string, unknown> {
    return {
        operation_id: revision.operation_id,
        template_document_id: revision.template_document_id,
        template_revision_id: revision.template_revision_id,
        template_sha256: revision.template_sha256,
        template_snapshot: revision.template_snapshot,
        forward_primer_snapshot: revision.forward_primer_snapshot,
        reverse_primer_snapshot: revision.reverse_primer_snapshot,
        tm_model_revision_id: revision.tm_model_revision_id,
        tm_snapshot: revision.tm_snapshot,
        polymerase_preset_revision_id: revision.polymerase_preset_revision_id,
        polymerase_snapshot: revision.polymerase_snapshot,
        reaction_settings: revision.reaction_settings,
        cycling_assumptions: revision.cycling_assumptions,
        product_document_id: revision.product_document_id,
        product_revision_id: revision.product_revision_id,
        product_snapshot: revision.product_snapshot,
        warnings: revision.warnings,
        notes: revision.notes,
    };
}

export function PCRPanel(props: PCRPanelProps) {
    const {
        sequenceData,
        sequenceId,
        onHighlight,
        onPCRComplete,
        tmOptions,
        tmSettings,
        onTmSettingsChange,
    } = props;
    const location = useLocation();
    const { updateQueryParams, contextHref } = useGlobalExperimentContext();
    const queryParams = useMemo(() => new URLSearchParams(location.search), [location.search]);
    const requestedPcrExperimentId = queryParams.get('pcr_experiment_id')?.trim() || null;
    const requestedPcrRevisionId = queryParams.get('pcr_revision_id')?.trim() || null;
    const hasExactPcrPair = requestedPcrExperimentId !== null && requestedPcrRevisionId !== null;
    const hasIncompletePcrPair = (requestedPcrExperimentId === null) !== (requestedPcrRevisionId === null);
    const [forwardPrimer, setForwardPrimer] = useState('');
    const [reversePrimer, setReversePrimer] = useState('');
    const [productName, setProductName] = useState('');
    const [persistImmutableRevision, setPersistImmutableRevision] = useState(Boolean(sequenceId));
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<{ sequence: string; length: number; start?: number; end?: number; wrapsOrigin?: boolean } | null>(null);
    const [persistedResult, setPersistedResult] = useState<Pick<PcrOperationResponse, 'experiment_id' | 'experiment_revision_id'> | null>(null);
    const [exactRevision, setExactRevision] = useState<PcrExperimentRevision | null>(null);
    const [revisionHistory, setRevisionHistory] = useState<PcrExperimentRevision[]>([]);
    const [authorityLoading, setAuthorityLoading] = useState(false);
    const [authorityError, setAuthorityError] = useState<string | null>(null);
    const [tmLoading, setTmLoading] = useState(false);
    const [tmResults, setTmResults] = useState<{ forward: PrimerTmResult | null; reverse: PrimerTmResult | null }>({
        forward: null,
        reverse: null,
    });

    const sequenceType: 'dna' | 'rna' = sequenceData.sequenceType === 'rna' ? 'rna' : 'dna';
    const unitLabel = sequenceUnitLabel(sequenceType);

    useEffect(() => {
        setPersistImmutableRevision(Boolean(sequenceId));
    }, [sequenceId]);

    useEffect(() => {
        if (!hasExactPcrPair || !requestedPcrExperimentId || !requestedPcrRevisionId) {
            setExactRevision(null);
            setRevisionHistory([]);
            setAuthorityLoading(false);
            setAuthorityError(null);
            return;
        }

        let cancelled = false;
        setExactRevision(null);
        setRevisionHistory([]);
        setAuthorityLoading(true);
        setAuthorityError(null);
        void Promise.all([
            fetchPcrExperimentRevision(requestedPcrExperimentId, requestedPcrRevisionId),
            fetchPcrExperimentRevisions(requestedPcrExperimentId),
        ])
            .then(([revision, revisions]) => ({
                revision: validatePcrRevision(requestedPcrExperimentId, requestedPcrRevisionId, revision),
                revisions: validatePcrRevisionList(requestedPcrExperimentId, revisions),
            }))
            .then(({ revision, revisions }) => {
                if (cancelled) return;
                setExactRevision(revision);
                setRevisionHistory(revisions);
            })
            .catch((authorityFailure) => {
                if (!cancelled) {
                    setAuthorityError(authorityFailure instanceof Error ? authorityFailure.message : String(authorityFailure));
                }
            })
            .finally(() => {
                if (!cancelled) setAuthorityLoading(false);
            });

        return () => {
            cancelled = true;
        };
    }, [
        hasExactPcrPair,
        requestedPcrExperimentId,
        requestedPcrRevisionId,
    ]);

    const selectExactRevision = useCallback((revision: PcrExperimentRevision) => {
        updateQueryParams({
            pcr_experiment_id: revision.experiment_id,
            pcr_revision_id: revision.id,
        });
    }, [updateQueryParams]);

    const fwdBindings = useMemo(() => resolvePrimerBindings(sequenceData.sequence, forwardPrimer, {
        reverse: false,
        sequenceType,
        circular: sequenceData.circular,
    }), [forwardPrimer, sequenceData.sequence, sequenceData.circular, sequenceType]);

    const revBindings = useMemo(() => resolvePrimerBindings(sequenceData.sequence, reversePrimer, {
        reverse: true,
        sequenceType,
        circular: sequenceData.circular,
    }), [reversePrimer, sequenceData.sequence, sequenceData.circular, sequenceType]);

    const fwdBinding = fwdBindings[0] ?? null;
    const revBinding = revBindings[0] ?? null;

    const forwardAnnealSequence = useMemo(() => {
        if (!forwardPrimer) return '';
        if (!fwdBinding) return forwardPrimer;
        return forwardPrimer.slice(forwardPrimer.length - fwdBinding.annealLength);
    }, [forwardPrimer, fwdBinding]);

    const reverseAnnealSequence = useMemo(() => {
        if (!reversePrimer) return '';
        if (!revBinding) return reversePrimer;
        return reversePrimer.slice(reversePrimer.length - revBinding.annealLength);
    }, [reversePrimer, revBinding]);

    useEffect(() => {
        if (!forwardPrimer && !reversePrimer) {
            setTmResults({ forward: null, reverse: null });
            setTmLoading(false);
            return;
        }
        if ((forwardPrimer && !isValidNucleotideSequence(forwardPrimer)) || (reversePrimer && !isValidNucleotideSequence(reversePrimer))) {
            setTmResults({ forward: null, reverse: null });
            setTmLoading(false);
            return;
        }

        let cancelled = false;
        setTmLoading(true);
        const timer = window.setTimeout(async () => {
            try {
                const response = await calculatePrimerTm({
                    primers: [
                        ...(forwardAnnealSequence ? [{ id: 'forward', sequence: forwardAnnealSequence, sequence_type: sequenceType }] : []),
                        ...(reverseAnnealSequence ? [{ id: 'reverse', sequence: reverseAnnealSequence, sequence_type: sequenceType }] : []),
                    ],
                    settings: tmSettings,
                });
                if (cancelled) {
                    return;
                }
                const forwardResult = response.data.find((entry) => entry.id === 'forward') ?? null;
                const reverseResult = response.data.find((entry) => entry.id === 'reverse') ?? null;
                setTmResults({ forward: forwardResult, reverse: reverseResult });
            } catch (tmError) {
                console.error('Failed to calculate PCR primer Tm:', tmError);
                if (!cancelled) {
                    setTmResults({ forward: null, reverse: null });
                }
            } finally {
                if (!cancelled) {
                    setTmLoading(false);
                }
            }
        }, 250);

        return () => {
            cancelled = true;
            window.clearTimeout(timer);
        };
    }, [forwardAnnealSequence, forwardPrimer, reverseAnnealSequence, reversePrimer, sequenceType, tmSettings]);

    const predictedSize = useMemo(() => {
        if (fwdBinding && revBinding) {
            const templateLength = sequenceData.sequence.length;
            const templateSpan = sequenceData.circular
                ? (() => {
                    let span = (revBinding.end - fwdBinding.start) % templateLength;
                    if (span === 0) {
                        span = templateLength;
                    }
                    return span;
                })()
                : revBinding.end > fwdBinding.start
                    ? revBinding.end - fwdBinding.start
                    : null;

            if (templateSpan !== null) {
                return templateSpan + fwdBinding.overhangLength + revBinding.overhangLength;
            }
        }
        return null;
    }, [fwdBinding, revBinding, sequenceData.circular, sequenceData.sequence.length]);

    const buildBindingHighlights = useCallback(() => {
        const regions: HighlightedRegion[] = [];
        const sequenceLength = sequenceData.sequence.length;

        const addBinding = (
            binding: typeof fwdBinding,
            color: string,
            label: string,
        ) => {
            if (!binding) return;
            if (binding.end > sequenceLength && sequenceLength > 0) {
                regions.push({
                    start: binding.start,
                    end: sequenceLength,
                    color,
                    label,
                });
                regions.push({
                    start: 0,
                    end: binding.end % sequenceLength,
                    color,
                    label,
                });
                return;
            }
            regions.push({
                start: binding.start,
                end: binding.end,
                color,
                label,
            });
        };

        addBinding(fwdBinding, '#22c55e', 'Forward primer');
        addBinding(revBinding, '#ef4444', 'Reverse primer');
        return regions;
    }, [fwdBinding, revBinding, sequenceData.sequence.length]);

    useEffect(() => {
        if (!forwardPrimer && !reversePrimer) {
            onHighlight([]);
            return;
        }
        onHighlight(buildBindingHighlights());
    }, [forwardPrimer, reversePrimer, buildBindingHighlights, onHighlight]);

    const runPCR = async () => {
        if (!forwardPrimer || !reversePrimer) return;
        if (!isValidNucleotideSequence(forwardPrimer) || !isValidNucleotideSequence(reversePrimer)) {
            setError('Primers contain invalid nucleotide characters.');
            return;
        }

        setLoading(true);
        setError(null);
        setPersistedResult(null);

        try {
            const shouldPersist = Boolean(sequenceId) && persistImmutableRevision;
            const data = await runPcrOperation({
                primer_fwd: forwardPrimer,
                primer_rev: reversePrimer,
                is_circular: sequenceData.circular,
                save: shouldPersist,
                persist_experiment: shouldPersist,
                new_name: productName || `${sequenceData.name}_PCR`,
                tm_settings: tmSettings,
                ...(sequenceId
                    ? { sequence_id: sequenceId }
                    : {
                        sequence: sequenceData.sequence,
                        name: sequenceData.name,
                        sequence_type: sequenceType,
                    }),
            });

            const responseProduct = data.product;
            const persistedSequence = data.sequence;
            const product = responseProduct
                ? {
                    sequence: responseProduct.sequence,
                    length: responseProduct.length,
                    start: responseProduct.start,
                    end: responseProduct.end,
                    wrapsOrigin: responseProduct.wraps_origin,
                }
                : persistedSequence
                    ? {
                        sequence: persistedSequence.sequence,
                        length: persistedSequence.length,
                    }
                    : null;

            if (product) {
                setResult(product);
                onPCRComplete?.(product);

                const regions: HighlightedRegion[] = [];
                if (typeof product.start === 'number' && typeof product.end === 'number') {
                    const label = `PCR product (${product.length.toLocaleString()} ${unitLabel})`;
                    if (product.wrapsOrigin) {
                        regions.push({
                            start: product.start,
                            end: sequenceData.sequence.length,
                            color: '#38bdf8',
                            label,
                        });
                        if (product.end > 0) {
                            regions.push({
                                start: 0,
                                end: product.end,
                                color: '#38bdf8',
                                label,
                            });
                        }
                    } else {
                        regions.push({
                            start: product.start,
                            end: product.end,
                            color: '#38bdf8',
                            label,
                        });
                    }
                }
                if (regions.length > 0) {
                    onHighlight(regions);
                }
            }

            const returnedIds = {
                experiment_id: data.experiment_id,
                experiment_revision_id: data.experiment_revision_id,
            };
            setPersistedResult(returnedIds);
            const hasExperimentId = Boolean(data.experiment_id);
            const hasRevisionId = Boolean(data.experiment_revision_id);
            if (hasExperimentId !== hasRevisionId) {
                throw new Error('PCR persistence returned an incomplete experiment/revision identity pair; no reopen authority was inferred.');
            }
            if (shouldPersist && (!data.experiment_id || !data.experiment_revision_id)) {
                throw new Error('PCR completed, but the server did not return the requested immutable experiment/revision identity pair.');
            }
            if (data.experiment_id && data.experiment_revision_id) {
                updateQueryParams({
                    pcr_experiment_id: data.experiment_id,
                    pcr_revision_id: data.experiment_revision_id,
                });
            }
        } catch (runError) {
            setError(runError instanceof Error ? runError.message : 'Unknown error');
        } finally {
            setLoading(false);
        }
    };

    const canRun = forwardPrimer.length >= 15 && reversePrimer.length >= 15 && Boolean(fwdBinding) && Boolean(revBinding);
    const tmDiff = (tmResults.forward?.tm !== null && tmResults.forward?.tm !== undefined && tmResults.reverse?.tm !== null && tmResults.reverse?.tm !== undefined)
        ? Math.abs(tmResults.forward.tm - tmResults.reverse.tm)
        : 0;

    return (
        <div className="pcr-panel p-3 space-y-4">
            <h4 className="font-semibold text-slate-200">PCR Amplification</h4>

            <section className="space-y-3 rounded-lg border border-slate-700 bg-slate-900/60 p-3" aria-label="Exact immutable PCR revision authority">
                <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-cyan-300">
                    Exact immutable PCR revision authority
                </div>
                {hasIncompletePcrPair ? (
                    <div className="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-xs text-red-300">
                        Exact PCR reopen requires both pcr_experiment_id and pcr_revision_id. No identifier is inferred from the other.
                    </div>
                ) : authorityLoading ? (
                    <div className="text-xs text-cyan-200">Loading the exact PCR revision and its immutable revision history…</div>
                ) : authorityError ? (
                    <div className="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-xs text-red-300">
                        Unable to load exact PCR revision authority: {authorityError}
                    </div>
                ) : exactRevision ? (
                    <div className="space-y-3">
                        <div className="rounded border border-amber-700/70 bg-amber-950/20 p-3">
                            <div className="font-medium text-amber-200">Read-only immutable revision · {exactRevision.relation}</div>
                            <p className="mt-1 text-xs text-slate-400">
                                Historical selection changes only the exact PCR query pair and never replaces the live calculator inputs.
                            </p>
                            <dl className="mt-3 grid gap-2 text-xs">
                                <div><dt className="text-slate-500">Experiment ID</dt><dd className="break-all font-mono text-slate-200">{exactRevision.experiment_id}</dd></div>
                                <div><dt className="text-slate-500">Revision ID</dt><dd className="break-all font-mono text-slate-200">{exactRevision.id}</dd></div>
                                <div><dt className="text-slate-500">Revision number</dt><dd className="text-slate-200">#{exactRevision.revision_number}</dd></div>
                                <div><dt className="text-slate-500">Review state</dt><dd className="text-slate-200">{exactRevision.review_state.replace(/_/g, ' ')}</dd></div>
                                <div><dt className="text-slate-500">Created</dt><dd className="text-slate-200">{new Date(exactRevision.created_at).toLocaleString()}</dd></div>
                                <div><dt className="text-slate-500">Payload SHA-256</dt><dd className="break-all font-mono text-slate-200">{exactRevision.payload_sha256}</dd></div>
                                <div><dt className="text-slate-500">Template SHA-256</dt><dd className="break-all font-mono text-slate-200">{exactRevision.template_sha256}</dd></div>
                                <div><dt className="text-slate-500">Parent revision</dt><dd className="break-all font-mono text-slate-200">{exactRevision.parent_revision_id ?? 'root revision'}</dd></div>
                            </dl>
                            <a
                                href={contextHref(location.pathname, {
                                    pcr_experiment_id: exactRevision.reopen_destination.params.experiment_id,
                                    pcr_revision_id: exactRevision.reopen_destination.params.revision_id,
                                })}
                                className="mt-3 inline-flex text-xs font-medium text-cyan-300 hover:text-cyan-200"
                            >
                                Exact context-preserving reopen link
                            </a>
                            <details className="mt-3 rounded border border-slate-700 bg-slate-950/60 px-3 py-2">
                                <summary className="cursor-pointer text-xs font-medium text-slate-300">Immutable PCR payload</summary>
                                <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all text-[11px] text-slate-400">{JSON.stringify(immutablePcrPayload(exactRevision), null, 2)}</pre>
                            </details>
                            <details className="mt-2 rounded border border-slate-700 bg-slate-950/60 px-3 py-2">
                                <summary className="cursor-pointer text-xs font-medium text-slate-300">Provenance</summary>
                                <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all text-[11px] text-slate-400">{JSON.stringify(exactRevision.provenance, null, 2)}</pre>
                            </details>
                            <details className="mt-2 rounded border border-slate-700 bg-slate-950/60 px-3 py-2">
                                <summary className="cursor-pointer text-xs font-medium text-slate-300">Reopen metadata</summary>
                                <pre className="mt-2 overflow-auto whitespace-pre-wrap break-all text-[11px] text-slate-400">{JSON.stringify(exactRevision.reopen_destination, null, 2)}</pre>
                            </details>
                        </div>

                        <div className="space-y-2">
                            <div className="flex items-center justify-between gap-3 text-[11px] uppercase tracking-[0.1em] text-slate-500">
                                <span>Server immutable PCR revision history</span>
                                <span>{revisionHistory.length} revisions</span>
                            </div>
                            {revisionHistory.map((revision) => {
                                const selected = revision.id === exactRevision.id;
                                return (
                                    <button
                                        key={revision.id}
                                        type="button"
                                        onClick={() => selectExactRevision(revision)}
                                        disabled={selected}
                                        className={`w-full rounded border px-3 py-2 text-left text-xs ${selected
                                            ? 'cursor-default border-cyan-700 bg-cyan-950/30 text-cyan-200'
                                            : 'border-slate-700 bg-slate-950/50 text-slate-300 hover:border-slate-500'
                                        }`}
                                    >
                                        <div className="flex items-center justify-between gap-3">
                                            <span>Revision #{revision.revision_number} · {revision.relation} · {revision.review_state.replace(/_/g, ' ')}</span>
                                            <span>{new Date(revision.created_at).toLocaleString()}</span>
                                        </div>
                                        <div className="mt-1 break-all font-mono text-[11px] text-slate-500">{revision.id}</div>
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                ) : (
                    <div className="text-xs text-slate-500">No exact immutable PCR revision is selected.</div>
                )}
            </section>

            <PrimerTmSettingsPanel
                sequenceType={sequenceType}
                options={tmOptions}
                settings={tmSettings}
                onChange={onTmSettingsChange}
            />

            <div className="space-y-1">
                <label className="text-sm text-slate-400">Forward Primer (5'→3')</label>
                <input
                    type="text"
                    value={forwardPrimer}
                    onChange={(event) => {
                        setForwardPrimer(event.target.value.toUpperCase());
                        setResult(null);
                        setError(null);
                    }}
                    placeholder="ATGC..."
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-sm font-mono focus:border-blue-500 focus:outline-none"
                />
                {forwardPrimer && (
                    <div className="space-y-1 text-xs text-slate-400">
                        <div className="flex items-center gap-3 flex-wrap">
                            <span>{forwardPrimer.length} {unitLabel}</span>
                            <span className="text-emerald-300">
                                {fwdBinding && fwdBinding.overhangLength > 0 ? 'Annealing Tm' : 'Tm'}: {tmLoading ? 'Calculating...' : formatTm(tmResults.forward)}
                            </span>
                            <span>GC: {tmResults.forward?.gc_percent ?? 'n/a'}%</span>
                            {fwdBinding ? (
                                <span className="text-emerald-400">
                                    ✓ Anneals @ {fwdBinding.start + 1}
                                    {fwdBinding.overhangLength > 0 ? ` (+${fwdBinding.overhangLength} ${unitLabel} tail)` : ''}
                                </span>
                            ) : (
                                <span className="text-red-400">✗ No annealing site</span>
                            )}
                        </div>
                        {(tmResults.forward?.warnings || []).map((warning) => (
                            <div key={`f-${warning}`} className="text-yellow-300">
                                {warning}
                            </div>
                        ))}
                    </div>
                )}
            </div>

            <div className="space-y-1">
                <label className="text-sm text-slate-400">Reverse Primer (5'→3')</label>
                <input
                    type="text"
                    value={reversePrimer}
                    onChange={(event) => {
                        setReversePrimer(event.target.value.toUpperCase());
                        setResult(null);
                        setError(null);
                    }}
                    placeholder="ATGC..."
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-sm font-mono focus:border-blue-500 focus:outline-none"
                />
                {reversePrimer && (
                    <div className="space-y-1 text-xs text-slate-400">
                        <div className="flex items-center gap-3 flex-wrap">
                            <span>{reversePrimer.length} {unitLabel}</span>
                            <span className="text-emerald-300">
                                {revBinding && revBinding.overhangLength > 0 ? 'Annealing Tm' : 'Tm'}: {tmLoading ? 'Calculating...' : formatTm(tmResults.reverse)}
                            </span>
                            <span>GC: {tmResults.reverse?.gc_percent ?? 'n/a'}%</span>
                            {revBinding ? (
                                <span className="text-emerald-400">
                                    ✓ Anneals @ {revBinding.start + 1}
                                    {revBinding.overhangLength > 0 ? ` (+${revBinding.overhangLength} ${unitLabel} tail)` : ''}
                                </span>
                            ) : (
                                <span className="text-red-400">✗ No annealing site</span>
                            )}
                        </div>
                        {(tmResults.reverse?.warnings || []).map((warning) => (
                            <div key={`r-${warning}`} className="text-yellow-300">
                                {warning}
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {tmDiff > 5 && forwardPrimer && reversePrimer && (
                <div className="p-2 bg-yellow-900/30 border border-yellow-800/50 rounded text-xs text-yellow-300">
                    ⚠️ Tm difference: {tmDiff.toFixed(1)}°C (ideally &lt; 5°C)
                </div>
            )}

            {predictedSize && (
                <div className="p-2 bg-slate-700/50 rounded text-sm text-slate-300">
                    <span className="text-slate-400">Predicted product:</span>{' '}
                    <span className="font-mono text-emerald-400">{predictedSize.toLocaleString()} {unitLabel}</span>
                </div>
            )}

            <div className="space-y-1">
                <label className="text-sm text-slate-400">Product Name</label>
                <input
                    type="text"
                    value={productName}
                    onChange={(event) => setProductName(event.target.value)}
                    placeholder={`${sequenceData.name}_PCR`}
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded text-sm focus:border-blue-500 focus:outline-none"
                />
            </div>

            <label className={`flex items-start gap-2 rounded border px-3 py-2 text-sm ${sequenceId
                ? 'border-slate-600 bg-slate-900/50 text-slate-300'
                : 'cursor-not-allowed border-slate-700 bg-slate-900/30 text-slate-600'
            }`}>
                <input
                    type="checkbox"
                    checked={persistImmutableRevision}
                    onChange={(event) => setPersistImmutableRevision(event.target.checked)}
                    disabled={!sequenceId}
                    className="mt-0.5"
                />
                <span>
                    Persist immutable PCR revision
                    <span className="mt-0.5 block text-xs text-slate-500">
                        {sequenceId
                            ? 'Saves the PCR product and its immutable experiment revision; uncheck for calculator-only execution.'
                            : 'A saved template sequence is required for immutable PCR experiment persistence.'}
                    </span>
                </span>
            </label>

            <button
                onClick={runPCR}
                disabled={loading || !canRun}
                className="w-full py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-sm font-medium transition-colors"
            >
                {loading ? 'Running PCR...' : 'Run PCR'}
            </button>

            {error && (
                <div className="p-2 bg-red-900/50 border border-red-800 rounded text-sm text-red-300">
                    {error}
                </div>
            )}

            {persistedResult && (persistedResult.experiment_id || persistedResult.experiment_revision_id) && (
                <div className="space-y-2 rounded border border-cyan-800/60 bg-cyan-950/20 p-3 text-xs">
                    <div className="font-medium text-cyan-200">Persisted immutable PCR authority</div>
                    <div><span className="text-slate-500">Experiment ID: </span><span className="break-all font-mono text-slate-200">{persistedResult.experiment_id ?? 'missing'}</span></div>
                    <div><span className="text-slate-500">Revision ID: </span><span className="break-all font-mono text-slate-200">{persistedResult.experiment_revision_id ?? 'missing'}</span></div>
                </div>
            )}

            {result && (
                <div className="p-3 bg-emerald-900/30 border border-emerald-800/50 rounded space-y-2">
                    <div className="flex items-center justify-between">
                        <span className="text-sm text-emerald-300">PCR Product</span>
                        <span className="font-mono text-emerald-400">{result.length.toLocaleString()} {unitLabel}</span>
                    </div>
                    {result.wrapsOrigin && (
                        <div className="text-xs text-emerald-200/80">
                            Product spans the plasmid origin.
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
