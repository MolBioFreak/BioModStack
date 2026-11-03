/**
 * PCRPanel - PCR amplification tool
 */

import { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { useInputOwnership } from './useInputOwnership';
import { useLocation } from 'react-router-dom';
import type { SequenceData, HighlightedRegion } from '../types';
import {
    fetchPcrExperimentRevision,
    fetchPcrExperimentSummaryPage,
    type PcrExperimentRevisionSummary,
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
    const [minAnnealLength, setMinAnnealLength] = useState(8);
    const [forwardSite, setForwardSite] = useState('');
    const [reverseSite, setReverseSite] = useState('');
    const [preview, setPreview] = useState<PcrOperationResponse['product']>(null);
    const [previewError, setPreviewError] = useState<string | null>(null);
    const [previewLoading, setPreviewLoading] = useState(false);
    const [persistImmutableRevision, setPersistImmutableRevision] = useState(Boolean(sequenceId));
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<{ sequence: string; length: number; start?: number; end?: number; wrapsOrigin?: boolean } | null>(null);
    const [persistedResult, setPersistedResult] = useState<Pick<PcrOperationResponse, 'experiment_id' | 'experiment_revision_id'> | null>(null);
    const [exactRevision, setExactRevision] = useState<PcrExperimentRevision | null>(null);
    const [revisionHistory, setRevisionHistory] = useState<PcrExperimentRevisionSummary[]>([]);
    const [historyLoading, setHistoryLoading] = useState(false);
    const [historyError, setHistoryError] = useState<string | null>(null);
    const [historyPage, setHistoryPage] = useState({ owner: requestedPcrExperimentId, offset: 0 });
    const [historyNextOffset, setHistoryNextOffset] = useState<number | null>(null);
    const [currentRevisionId, setCurrentRevisionId] = useState<string | null>(null);
    const historyOwner = hasExactPcrPair ? requestedPcrExperimentId : null;
    if (historyPage.owner !== historyOwner) {
        setHistoryPage({ owner: historyOwner, offset: 0 });
        setRevisionHistory([]);
        setHistoryError(null);
        setHistoryNextOffset(null);
        setCurrentRevisionId(null);
    }
    const historyOffset = historyPage.owner === historyOwner ? historyPage.offset : 0;
    const [authorityLoading, setAuthorityLoading] = useState(false);
    const [authorityError, setAuthorityError] = useState<string | null>(null);
    const tmLoading = previewLoading;
    const tmResults = { forward: preview?.forward_primer?.annealing_tm ?? null, reverse: preview?.reverse_primer?.annealing_tm ?? null };

    const sequenceType: 'dna' | 'rna' = sequenceData.sequenceType === 'rna' ? 'rna' : 'dna';
    const unitLabel = sequenceUnitLabel(sequenceType);
    const pcrOwner = useInputOwnership([sequenceId, sequenceData.sequence, sequenceData.name, sequenceData.circular, sequenceType, forwardPrimer, reversePrimer, tmSettings, productName, persistImmutableRevision, minAnnealLength, forwardSite, reverseSite]);
    useEffect(() => { setResult(null); setPersistedResult(null); setLoading(false); }, [pcrOwner.token]);
    useEffect(() => { setForwardSite(''); setReverseSite(''); }, [sequenceData.sequence, sequenceData.circular, forwardPrimer, reversePrimer, minAnnealLength]);

    useEffect(() => {
        setPersistImmutableRevision(Boolean(sequenceId));
    }, [sequenceId]);

    useEffect(() => {
        if (!hasExactPcrPair || !requestedPcrExperimentId || !requestedPcrRevisionId) {
            setExactRevision(null);
            setAuthorityLoading(false);
            setAuthorityError(null);
            return;
        }

        let cancelled = false;
        setExactRevision(null);
        setAuthorityLoading(true);
        setAuthorityError(null);
        void fetchPcrExperimentRevision(requestedPcrExperimentId, requestedPcrRevisionId)
            .then((revision) => {
                if (cancelled) return;
                setExactRevision(validatePcrRevision(requestedPcrExperimentId, requestedPcrRevisionId, revision));
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

    useEffect(() => {
        const controller = new AbortController();
        if (historyOffset === 0) {
            setRevisionHistory([]);
            setCurrentRevisionId(null);
        }
        setHistoryError(null);
        setHistoryNextOffset(null);
        setHistoryLoading(hasExactPcrPair);
        if (!hasExactPcrPair || !requestedPcrExperimentId) return;
        void fetchPcrExperimentSummaryPage(requestedPcrExperimentId, 50, historyOffset, controller.signal)
            .then((page) => {
                if (controller.signal.aborted) return;
                if (page.id !== requestedPcrExperimentId || page.revisions.some((revision) => revision.experiment_id !== requestedPcrExperimentId)) {
                    throw new Error('PCR history summary belongs to a different experiment.');
                }
                setRevisionHistory((current) => historyOffset === 0 ? page.revisions : [...current, ...page.revisions]);
                setHistoryNextOffset(page.has_more ? page.next_offset : null);
                setCurrentRevisionId(page.current_revision_id);
            })
            .catch((failure) => {
                if (!controller.signal.aborted) setHistoryError(failure instanceof Error ? failure.message : String(failure));
            })
            .finally(() => { if (!controller.signal.aborted) setHistoryLoading(false); });
        return () => controller.abort();
    }, [hasExactPcrPair, requestedPcrExperimentId, historyOffset, historyPage]);

    const selectExactRevision = useCallback((revision: PcrExperimentRevisionSummary) => {
        updateQueryParams({
            pcr_experiment_id: revision.experiment_id,
            pcr_revision_id: revision.id,
        });
    }, [updateQueryParams]);

    const previewController = useRef<AbortController | null>(null);
    useEffect(() => () => previewController.current?.abort(), [pcrOwner.token]);

    const fwdBindings = useMemo(() => resolvePrimerBindings(sequenceData.sequence, forwardPrimer, {
        reverse: false, sequenceType, circular: sequenceData.circular, minAnnealLength,
    }), [forwardPrimer, sequenceData.sequence, sequenceData.circular, sequenceType, minAnnealLength]);
    const revBindings = useMemo(() => resolvePrimerBindings(sequenceData.sequence, reversePrimer, {
        reverse: true, sequenceType, circular: sequenceData.circular, minAnnealLength,
    }), [reversePrimer, sequenceData.sequence, sequenceData.circular, sequenceType, minAnnealLength]);
    const siteKey = (binding: (typeof fwdBindings)[number]) => `${binding.start}:${binding.annealLength}`;
    const selectedSite = (value: string) => {
        if (!value) return null;
        const [start, anneal_length] = value.split(':').map(Number);
        return { start, anneal_length };
    };
    const fwdBinding = fwdBindings.find(b => forwardSite ? siteKey(b) === forwardSite
        : preview?.forward_primer ? b.start === preview.forward_primer.binding.start && b.annealLength === preview.forward_primer.binding.anneal_length
            : fwdBindings.length === 1) ?? null;
    const revBinding = revBindings.find(b => reverseSite ? siteKey(b) === reverseSite
        : preview?.reverse_primer ? b.start === preview.reverse_primer.binding.start && b.annealLength === preview.reverse_primer.binding.anneal_length
            : revBindings.length === 1) ?? null;

    // The API owns product geometry and thermodynamics for preview and saving.
    useEffect(() => {
        setPreview(null);
        setPreviewError(null);
        setPreviewLoading(false);
        if (sequenceType !== 'dna' || !Number.isInteger(minAnnealLength) || minAnnealLength < 8 || minAnnealLength > 100
            || forwardPrimer.replace(/\s/g, '').length < minAnnealLength || reversePrimer.replace(/\s/g, '').length < minAnnealLength
            || !isValidNucleotideSequence(forwardPrimer) || !isValidNucleotideSequence(reversePrimer)) return;
        const controller = new AbortController();
        setPreviewLoading(true);
        const timer = window.setTimeout(async () => {
            try {
                const data = await runPcrOperation({
                    schema_version: 'pcr-v2',
                    sequence: sequenceData.sequence, name: sequenceData.name, sequence_type: 'dna', is_circular: sequenceData.circular,
                    primer_fwd: forwardPrimer, primer_rev: reversePrimer, min_anneal_length: minAnnealLength,
                    forward_binding: selectedSite(forwardSite), reverse_binding: selectedSite(reverseSite),
                    save: false, persist_experiment: false, new_name: productName || `${sequenceData.name}_PCR`, tm_settings: tmSettings,
                }, controller.signal);
                if (pcrOwner.isCurrent() && !controller.signal.aborted) setPreview(data.product);
            } catch (cause) {
                if (pcrOwner.isCurrent() && !controller.signal.aborted) setPreviewError(cause instanceof Error ? cause.message : 'PCR preview failed');
            } finally {
                if (pcrOwner.isCurrent() && !controller.signal.aborted) setPreviewLoading(false);
            }
        }, 250);
        return () => { window.clearTimeout(timer); controller.abort(); };
    }, [pcrOwner.token]);
    const predictedSize = preview?.length ?? null;

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

        const shouldPersist = Boolean(sequenceId) && persistImmutableRevision;
        previewController.current?.abort();
        const controller = shouldPersist ? null : new AbortController();
        previewController.current = controller;
        try {
            const payload = {
                schema_version: 'pcr-v2' as const,
                primer_fwd: forwardPrimer,
                primer_rev: reversePrimer,
                min_anneal_length: minAnnealLength,
                forward_binding: selectedSite(forwardSite),
                reverse_binding: selectedSite(reverseSite),
                is_circular: sequenceData.circular,
                save: shouldPersist,
                persist_experiment: shouldPersist,
                new_name: productName || `${sequenceData.name}_PCR`,
                tm_settings: tmSettings,
                // Execute the same editable template used by the binding preview.
                sequence: sequenceData.sequence,
                name: sequenceData.name,
                sequence_type: sequenceType,
            };
            const data = controller
                ? await runPcrOperation(payload, controller.signal)
                : await runPcrOperation(payload);

            if (!pcrOwner.isCurrent() || controller?.signal.aborted) return;
            const responseProduct = data.product;
            const persistedSequence = data.sequence;
            const product = responseProduct
                ? {
                    sequence: responseProduct.sequence,
                    length: responseProduct.length,
                    start: responseProduct.start,
                    end: responseProduct.end,
                    wrapsOrigin: responseProduct.wraps_origin,
                    templateSpan: responseProduct.template_span,
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
                    if ('templateSpan' in product && product.templateSpan != null && product.templateSpan >= sequenceData.sequence.length) {
                        regions.push({ start: 0, end: sequenceData.sequence.length, color: '#38bdf8', label });
                    } else if (product.wrapsOrigin) {
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
            if (pcrOwner.isCurrent() && !controller?.signal.aborted) setError(runError instanceof Error ? runError.message : 'Unknown error');
        } finally {
            if (pcrOwner.isCurrent() && !controller?.signal.aborted) setLoading(false);
        }
    };

    const canRun = sequenceType === 'dna' && Boolean(preview) && !previewLoading && !previewError;
    const tmDiff = (tmResults.forward?.tm !== null && tmResults.forward?.tm !== undefined && tmResults.reverse?.tm !== null && tmResults.reverse?.tm !== undefined)
        ? Math.abs(tmResults.forward.tm - tmResults.reverse.tm)
        : 0;

    return (
        <div className="pcr-panel p-3 space-y-4">
            <h4 className="font-semibold text-slate-200">PCR sequence prediction</h4>
            <p className="text-xs text-slate-400">Predicts a product from exact 3′ matches. It does not predict laboratory yield or amplification success. Primer tails are included in the product.</p>
            {sequenceType !== 'dna' && <p role="alert" className="text-sm text-amber-300">PCR requires a DNA template. Convert RNA to cDNA in a reverse-transcription workflow first.</p>}
            <label className="block space-y-1 text-sm text-slate-300">
                <span>Minimum matching 3′ bases</span>
                <input aria-label="Minimum matching 3′ bases" type="number" min={8} max={100} step={1} value={minAnnealLength}
                    onChange={event => setMinAnnealLength(Number(event.target.value))}
                    className="block w-24 rounded border border-slate-600 bg-slate-800 px-2 py-1" />
                <span className="block text-xs text-slate-400">8–100 nt. Search threshold; short matches may not amplify under your reaction conditions.</span>
            </label>

            <section className="space-y-3 rounded-lg border border-slate-700 bg-slate-900/60 p-3" aria-label="Exact immutable PCR revision authority">
                <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-cyan-300">
                    Exact immutable PCR revision authority
                </div>
                {hasIncompletePcrPair ? (
                    <div className="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-xs text-red-300">
                        Exact PCR reopen requires both pcr_experiment_id and pcr_revision_id. No identifier is inferred from the other.
                    </div>
                ) : authorityLoading ? (
                    <div className="text-xs text-cyan-200">Loading the exact PCR revision…</div>
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
                                <span>{revisionHistory.length} revisions loaded</span>
                            </div>
                            {historyLoading && <p>Loading PCR revision summaries…</p>}
                            {historyError && <p role="alert">Unable to load PCR revision history: {historyError}. Exact revision remains available.</p>}
                            {(historyNextOffset !== null || historyError) && (
                                <button type="button" disabled={historyLoading} onClick={() => setHistoryPage({ owner: requestedPcrExperimentId, offset: historyError ? historyOffset : historyNextOffset! })}>
                                    {historyError ? 'Retry PCR history' : 'Load more PCR revisions'}
                                </button>
                            )}
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
                                            <span>Revision #{revision.revision_number} · {revision.id === currentRevisionId ? 'current' : 'historical'} · {revision.review_state.replace(/_/g, ' ')}</span>
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
                {fwdBindings.length > 1 && <label className="block text-xs text-slate-300">
                    Forward binding site ({fwdBindings.length} candidates)
                    <select aria-label="Forward binding site" value={forwardSite} onChange={event => setForwardSite(event.target.value)} className="mt-1 block w-full rounded border border-slate-600 bg-slate-800 p-2">
                        <option value="">Automatic — only if the product is unique</option>
                        {fwdBindings.map(binding => <option key={siteKey(binding)} value={siteKey(binding)}>Position {binding.start + 1} · {binding.annealLength} matching bases · {binding.overhangLength} tail bases</option>)}
                    </select>
                </label>}
                {forwardPrimer && (
                    <div className="space-y-1 text-xs text-slate-400">
                        <div className="flex items-center gap-3 flex-wrap">
                            <span>{forwardPrimer.length} {unitLabel}</span>
                            <span className="text-emerald-300">
                                Annealing Tm: {tmLoading ? 'Calculating...' : formatTm(tmResults.forward)}
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
                {revBindings.length > 1 && <label className="block text-xs text-slate-300">
                    Reverse binding site ({revBindings.length} candidates)
                    <select aria-label="Reverse binding site" value={reverseSite} onChange={event => setReverseSite(event.target.value)} className="mt-1 block w-full rounded border border-slate-600 bg-slate-800 p-2">
                        <option value="">Automatic — only if the product is unique</option>
                        {revBindings.map(binding => <option key={siteKey(binding)} value={siteKey(binding)}>Position {binding.start + 1} · {binding.annealLength} matching bases · {binding.overhangLength} tail bases</option>)}
                    </select>
                </label>}
                {reversePrimer && (
                    <div className="space-y-1 text-xs text-slate-400">
                        <div className="flex items-center gap-3 flex-wrap">
                            <span>{reversePrimer.length} {unitLabel}</span>
                            <span className="text-emerald-300">
                                Annealing Tm: {tmLoading ? 'Calculating...' : formatTm(tmResults.reverse)}
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

            {previewLoading && <p className="text-xs text-slate-400">Calculating product and annealing temperatures…</p>}
            {previewError && <p role="alert" className="text-sm text-amber-300">{previewError}</p>}
            {preview?.warnings?.map(warning => <p key={warning} className="text-xs text-amber-200">{warning}</p>)}
            {preview?.forward_primer && preview?.reverse_primer && (
                <details className="text-xs text-slate-400"><summary>Primer temperature details</summary>
                    <p>Forward full-oligo Tm: {formatTm(preview.forward_primer.full_oligo_tm)}; reverse full-oligo Tm: {formatTm(preview.reverse_primer.full_oligo_tm)}.</p>
                    <p>Annealing Tm uses the selected template-matching suffix. Full-oligo Tm includes the tail and is recorded separately.</p>
                </details>
            )}
            {predictedSize !== null && (
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
                {loading ? 'Saving prediction...' : persistImmutableRevision && sequenceId ? 'Save PCR prediction' : 'Calculate PCR product'}
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
                        <span className="text-sm text-emerald-300">Predicted PCR product</span>
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
