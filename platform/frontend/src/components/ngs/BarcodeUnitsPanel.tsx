import { Fragment, useState } from 'react';
import { ExecutionTargetPicker } from '../ExecutionTargetPicker';
import { ExecutionPolicyControl } from '../ExecutionPolicyControl';
import { useOntDomainSubmission } from './useOntDomainSubmission';
import type { ExecutionPolicy } from '../../lib/executionPolicy';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    fetchMolBioSequenceRevisions,
    fetchNucleotideSequences,
    fetchOntBarcodeUnits,
    issueMolBioNgsReceipt,
    submitOntBarcodeBatch,
    prepareOntBarcodeBatch,
    prepareExecutionPlacement,
    type OntBarcodeBatchSubmitRequest,

    type MolBioSequenceRevision,
    type NucleotideSequenceListItem,
    type OntBarcodeUnit,
} from '../../lib/api';

type TargetWorkflow = 'ont_plasmid_qc' | 'ont_construct_screening';

interface BarcodeUnitsPanelProps {
    jobId: string;
    enabled: boolean;
    /** Modification models the barcoded run was called with; methylation tables need them. */
    modifiedBases?: string | null;
}

const MODKIT_THRESHOLDS: Array<{ key: string; label: string; value: number | null }> = [
    { key: 'auto', label: 'Auto (Recommended)', value: null },
    { key: 'balanced', label: 'Balanced 0.50', value: 0.5 },
    { key: 'strict', label: 'Strict 0.70', value: 0.7 },
    { key: 'very-strict', label: 'Very strict 0.85', value: 0.85 },
];

interface BarcodeMappingDraft {
    sampleAlias: string;
    sequenceId: string;
    revisionId: string;
}

function newIdempotencyKey(prefix: string): string {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return `${prefix}-${crypto.randomUUID()}`;
    }
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function emptyMapping(): BarcodeMappingDraft {
    return { sampleAlias: '', sequenceId: '', revisionId: '' };
}

interface BarcodeMappingCellsProps {
    unitId: string;
    sequences: NucleotideSequenceListItem[];
    mapping: BarcodeMappingDraft;
    onChange: (next: BarcodeMappingDraft) => void;
    disabled?: boolean;
}

function BarcodeMappingCells({ unitId, sequences, mapping, onChange, disabled }: BarcodeMappingCellsProps) {
    const revisionsQuery = useQuery<MolBioSequenceRevision[]>({
        queryKey: ['ont-barcode-revisions', mapping.sequenceId],
        queryFn: async () => (await fetchMolBioSequenceRevisions(mapping.sequenceId)).data,
        enabled: Boolean(mapping.sequenceId),
        retry: false,
    });
    const revisions = revisionsQuery.data || [];
    const selectedRevision = revisions.find((revision) => revision.id === mapping.revisionId);

    return (
        <>
            <td className="px-3 py-2 align-top">
                <div className="space-y-2">
                    <select
                        aria-label={`Saved sequence for ${unitId}`}
                        value={mapping.sequenceId}
                        disabled={disabled}
                        onChange={(event) => onChange({ ...mapping, sequenceId: event.target.value, revisionId: '' })}
                        className="w-full min-w-[15rem] rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1.5 text-sm text-[var(--text-primary)]"
                    >
                        <option value="">Select saved sequence…</option>
                        {sequences.map((sequence) => (
                            <option key={sequence.id} value={sequence.id}>{sequence.name}</option>
                        ))}
                    </select>
                    <select
                        aria-label={`Exact revision for ${unitId}`}
                        value={mapping.revisionId}
                        onChange={(event) => onChange({ ...mapping, revisionId: event.target.value })}
                        disabled={disabled || !mapping.sequenceId || revisionsQuery.isLoading}
                        className="w-full min-w-[15rem] rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1.5 text-sm text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        <option value="">Select exact revision…</option>
                        {revisions.map((revision) => (
                            <option key={revision.id} value={revision.id}>
                                r{revision.revision_number} · {revision.is_current ? 'current' : 'historical'}
                            </option>
                        ))}
                    </select>
                    {revisionsQuery.isError && <div className="text-xs text-[var(--error)]">Unable to load revisions.</div>}
                </div>
            </td>
            <td className="px-3 py-2 align-top font-mono text-xs text-[var(--text-secondary)] break-all">
                {selectedRevision ? (
                    <>
                        <div>{selectedRevision.content_sha256}</div>
                        <div className="mt-1 font-sans">r{selectedRevision.revision_number} · {selectedRevision.topology} · {selectedRevision.is_current ? 'current' : 'historical'}</div>
                    </>
                ) : '—'}
            </td>
        </>
    );
}

export function BarcodeUnitsPanel({ jobId, enabled, modifiedBases }: BarcodeUnitsPanelProps) {
    const queryClient = useQueryClient();
    const hasModificationCalls = Boolean(modifiedBases && modifiedBases !== 'none');
    const [runModkit, setRunModkit] = useState(false);
    const [modkitThreshold, setModkitThreshold] = useState<number | null>(null);
    const [targetWorkflow, setTargetWorkflow] = useState<TargetWorkflow>('ont_plasmid_qc');
    const [namePrefix, setNamePrefix] = useState('');
    const [pinnedGpu, setPinnedGpu] = useState('');
    const [message, setMessage] = useState('');
    const [bulkMapping, setBulkMapping] = useState('');
    const [mappingByUnit, setMappingByUnit] = useState<Record<string, BarcodeMappingDraft>>({});
    // Further sequences on a barcode that holds more than one.
    const [extraByUnit, setExtraByUnit] = useState<Record<string, BarcodeMappingDraft[]>>({});

    const unitsQuery = useQuery<OntBarcodeUnit[]>({
        queryKey: ['ont-barcode-units', jobId],
        queryFn: async () => (await fetchOntBarcodeUnits(jobId)).data.units,
        enabled,
        retry: false,
    });
    const sequencesQuery = useQuery<NucleotideSequenceListItem[]>({
        queryKey: ['molbio-ngs-sequences'],
        queryFn: async () => (await fetchNucleotideSequences({
            limit: 100,
            sequence_type: 'dna',
            sort_by: 'name',
            sort_desc: false,
        })).data,
        enabled: enabled && Boolean(unitsQuery.data?.some(unit => unit.unit_id !== 'unclassified')),
        staleTime: 30_000,
        retry: false,
    });

    const allUnits = unitsQuery.data || [];
    const units = allUnits.filter((unit) => unit.unit_id !== 'unclassified');
    const unclassifiedUnit = allUnits.find((unit) => unit.unit_id === 'unclassified');
    const sequences = sequencesQuery.data || [];

    const applyBulkMapping = useMutation({
        mutationFn: async () => {
            const rows = bulkMapping.trim().split(/\r?\n/).map(line => line.split(line.includes('\t') ? '\t' : ',').map(value => value.trim()));
            if (rows[0]?.[0]?.toLowerCase() === 'barcode') rows.shift();
            if (!bulkMapping.trim() || rows.length === 0 || rows.length > 384) throw new Error('Paste 1–384 barcode-to-plasmid rows.');
            const next: Record<string, BarcodeMappingDraft> = {};
            const extra: Record<string, BarcodeMappingDraft[]> = {};
            for (const fields of rows) {
                if (fields.length < 2 || fields.length > 3) throw new Error('Use barcode, plasmid name or ID, and optional saved version.');
                const [barcode, name, exactVersion] = fields;
                const unit = units.find(unit => unit.unit_id === barcode);
                if (!unit || barcode === 'unclassified') throw new Error(`Unknown or repeated assignable barcode: ${barcode}`);
                let matches = sequences.filter(sequence => sequence.id === name);
                if (!matches.length) {
                    const results = (await fetchNucleotideSequences({search:name,limit:500,sequence_type:'dna'})).data;
                    if (results.length === 500) throw new Error(`Too many matches for ${name}. Use an exact saved sequence ID.`);
                    matches = results.filter(sequence => sequence.id === name || sequence.name === name);
                }
                if (matches.length !== 1) throw new Error(`Plasmid ${name} is missing or ambiguous. Use its exact sequence ID.`);
                const sequence = matches[0];
                const versions = (await fetchMolBioSequenceRevisions(sequence.id)).data;
                const version = exactVersion ? versions.find(version => version.id === exactVersion || String(version.revision_number) === exactVersion)
                    : versions.find(version => version.is_current);
                if (!version) throw new Error(`The requested version of ${name} is unavailable.`);
                const draft = {sampleAlias:unit.sample_alias || mappingByUnit[barcode]?.sampleAlias || '',sequenceId:sequence.id,revisionId:version.id};
                if (!next[barcode]) { next[barcode] = draft; continue; }
                // A repeated barcode row gives that barcode a further plasmid.
                if ([next[barcode], ...(extra[barcode] || [])].some(item => item.sequenceId === draft.sequenceId && item.revisionId === draft.revisionId)) throw new Error(`${barcode} lists ${name} twice.`);
                (extra[barcode] ||= []).push(draft);
            }
            return { next, extra };
        },
        onSuccess: ({ next, extra }) => {
            setMappingByUnit(previous => ({...previous,...next}));
            setExtraByUnit(previous => ({...previous,...Object.fromEntries(Object.keys(next).map(barcode => [barcode, extra[barcode] || []]))}));
            const applied = Object.keys(next).length + Object.values(extra).reduce((total, items) => total + items.length, 0);
            setMessage(`Applied ${applied} exact barcode-to-plasmid mappings. Review the table before submitting.`);
        },
        onError: error => setMessage(error instanceof Error ? error.message : 'Mapping could not be applied.'),
    });

    const [executionTargetId, setExecutionTargetId] = useState<string | null>(null);
    const [executionPolicy, setExecutionPolicy] = useState<ExecutionPolicy | undefined>();
    const submission = useOntDomainSubmission<OntBarcodeBatchSubmitRequest>(JSON.stringify([jobId, targetWorkflow, namePrefix, pinnedGpu, mappingByUnit, extraByUnit, runModkit, modkitThreshold, executionTargetId, executionPolicy]));
    const submitMutation = useMutation({
        mutationFn: () => submission.submit(async () => {
            const drafts = units.map((unit) => ({
                unit,
                draft: mappingByUnit[unit.unit_id] || {
                    ...emptyMapping(),
                    sampleAlias: unit.sample_alias || '',
                },
            }));
            // A barcode left without a sequence is skipped; the others still run.
            const half = drafts.filter(({ draft }) => Boolean(draft.sequenceId) !== Boolean(draft.revisionId));
            if (half.length) throw new Error(`Choose a revision for ${half.map(({ unit }) => unit.unit_id).join(', ')}, or clear its sequence.`);
            const chosen = drafts.filter(({ draft }) => draft.sequenceId && draft.revisionId);
            if (chosen.length === 0) throw new Error('Choose a saved sequence for at least one barcode before submitting.');
            const selected = chosen.flatMap(({ unit, draft }) => {
                const all = [draft, ...(extraByUnit[unit.unit_id] || [])];
                return all.map(item => ({ unit, draft: { ...item, sampleAlias: draft.sampleAlias }, several: all.length > 1 }));
            });
            if (selected.some(({ draft }) => !draft.sequenceId || !draft.revisionId)) {
                throw new Error('Choose a saved sequence and revision for every added sequence, or remove it.');
            }
            const parsedPinnedGpu = !executionTargetId && pinnedGpu.trim() ? Number(pinnedGpu) : null;
            if (parsedPinnedGpu !== null && (!Number.isInteger(parsedPinnedGpu) || parsedPinnedGpu < 0)) {
                throw new Error('Pinned GPU must be a non-negative integer.');
            }

            const mappings = await Promise.all(selected.map(async ({ unit, draft, several }) => {
                const receiptResponse = await issueMolBioNgsReceipt(draft.sequenceId, { revision_id: draft.revisionId });
                const receiptId = receiptResponse.data.receipt_id.trim();
                if (!receiptId) throw new Error(`No receipt was returned for ${unit.unit_id}.`);
                return {
                    unit_id: unit.unit_id,
                    sample_alias: unit.sample_alias || draft.sampleAlias.trim() || null,
                    molbio_ngs_receipt_id: receiptId,
                    ...(several ? { label: sequences.find(sequence => sequence.id === draft.sequenceId)?.name || draft.sequenceId } : {}),
                };
            }));

            return {
                idempotency_key: newIdempotencyKey('ont-barcode-batch'),
                target_workflow: targetWorkflow,
                ...(namePrefix.trim() ? { name_prefix: namePrefix.trim() } : {}),
                ...(parsedPinnedGpu !== null ? { pinned_gpu: parsedPinnedGpu } : {}),
                ...(runModkit && hasModificationCalls ? { run_modkit: true, ...(modkitThreshold !== null ? { modkit_filter_threshold: modkitThreshold } : {}) } : {}),
                mappings,
                ...prepareExecutionPlacement({ execution_target_id: executionTargetId, execution_policy: executionPolicy }),
            };
        }, async request => (await prepareOntBarcodeBatch(jobId, request)).data,
        (request, approvals) => submitOntBarcodeBatch(jobId, { ...request, ...(request.execution_target_id ? { execution_plan_approvals: approvals } : {}) })),
        onSuccess: (response) => {
            if (!response) return;
            const assigning = Object.keys(response.data.assignment_job_ids || {});
            const skipped = response.data.skipped_unit_ids || [];
            const skippedNote = skipped.length ? ` Skipped, no sequence chosen: ${skipped.join(', ')}. Their reads are kept.` : '';
            setMessage((assigning.length
                ? `Submitted ${response.data.child_job_ids.length} barcode analyses. ${assigning.join(', ')} hold several sequences: their reads are being assigned to a sequence first. Open that run when it finishes and release it to analyse each sequence.`
                : `Submitted ${response.data.child_job_ids.length} barcode children in reference set ${response.data.reference_set_id}.`) + skippedNote);
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
        },
        onError: (error: unknown) => setMessage(error instanceof Error ? error.message : 'Barcode batch submission failed'),
    });

    if (!enabled) return null;

    return (
        <section className="w-full space-y-3 rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4" data-testid="ont-barcode-units-panel">
            {submission.review}
            <ExecutionTargetPicker value={executionTargetId} onChange={setExecutionTargetId} />
            <ExecutionPolicyControl onChange={setExecutionPolicy} />
            <div>
                <h4 className="text-sm font-semibold text-[var(--text-primary)]">Demultiplexed barcode units</h4>
                <p className="text-xs text-[var(--text-secondary)]">Choose one or more saved sequences for each barcode. Barcodes with a sequence are submitted together. A barcode left without one is skipped and listed, and its reads are kept.</p>
            </div>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
                <select value={targetWorkflow} onChange={(event) => setTargetWorkflow(event.target.value as TargetWorkflow)} className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 text-sm text-[var(--text-primary)]">
                    <option value="ont_plasmid_qc">Plasmid QC</option>
                    <option value="ont_construct_screening">Construct screening</option>
                </select>
                <input value={namePrefix} onChange={(event) => setNamePrefix(event.target.value)} placeholder="Optional name prefix" className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 text-sm text-[var(--text-primary)]" />
                <input disabled={Boolean(executionTargetId)} value={pinnedGpu} onChange={(event) => setPinnedGpu(event.target.value)} inputMode="numeric" placeholder="Optional pinned GPU" className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 text-sm text-[var(--text-primary)]" />
                <button type="button" disabled={submitMutation.isPending || applyBulkMapping.isPending || units.length === 0} onClick={() => submitMutation.mutate()} className="rounded bg-[var(--accent-primary)] px-3 py-2 text-sm text-white disabled:cursor-not-allowed disabled:opacity-40">
                    {submitMutation.isPending ? 'Submitting batch…' : 'Submit mapped batch'}
                </button>
            </div>
            {hasModificationCalls && (
                <div className="space-y-2 rounded border border-[var(--border-primary)] p-3" data-testid="ont-barcode-methylation">
                    <label className="flex cursor-pointer items-center gap-3">
                        <input type="checkbox" aria-label="Generate methylation tables" checked={runModkit} onChange={event => setRunModkit(event.target.checked)} className="h-4 w-4 rounded border-[var(--border-primary)]" />
                        <div>
                            <span className="text-sm text-[var(--text-primary)]">Generate methylation tables</span>
                            <p className="text-xs text-[var(--text-secondary)]">From the modification calls already in each barcode's reads.</p>
                        </div>
                    </label>
                    {runModkit && (
                        <div className="space-y-1">
                            <div className="text-xs text-[var(--text-secondary)]">modkit filter threshold ({modkitThreshold !== null ? modkitThreshold.toFixed(2) : 'auto'})</div>
                            <div className="flex flex-wrap gap-2">
                                {MODKIT_THRESHOLDS.map(option => (
                                    <button key={option.key} type="button" aria-pressed={modkitThreshold === option.value} onClick={() => setModkitThreshold(option.value)}
                                        className={`rounded border px-2 py-1 text-xs ${modkitThreshold === option.value ? 'border-[var(--accent-secondary)] bg-[var(--bg-tertiary)] text-[var(--text-primary)]' : 'border-[var(--border-primary)] text-[var(--text-secondary)]'}`}>
                                        {option.label}
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}
            {unitsQuery.isLoading && <div className="text-xs text-[var(--text-secondary)]">Loading barcodes…</div>}
            {unitsQuery.isError && <div className="text-xs text-[var(--error)]">No verified barcode manifest is available.</div>}
            {sequencesQuery.isError && <div className="text-xs text-[var(--error)]">Saved MolBio sequences could not be loaded.</div>}
            <details className="rounded border border-[var(--border-primary)] p-3">
                <summary className="cursor-pointer text-sm font-semibold">Add barcode-to-plasmid mappings in bulk</summary>
                <p className="mt-2 text-xs text-[var(--text-secondary)]">Paste two or three columns: barcode number, saved plasmid name or ID, optional version number or revision ID. Current versions are pinned when you apply the rows. Existing mappings stay unchanged if any row fails.</p>
                <p className="mt-1 text-xs text-[var(--text-secondary)]">Repeat a barcode on several rows to give it several plasmids.</p>
                <textarea aria-label="Bulk barcode plasmid mappings" rows={5} value={bulkMapping} onChange={event=>setBulkMapping(event.target.value)} disabled={applyBulkMapping.isPending || submitMutation.isPending} placeholder={'barcode01\tplasmid_A\nbarcode02\tplasmid_B'} className="mt-2 w-full rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2 font-mono text-sm" />
                <button type="button" disabled={applyBulkMapping.isPending || submitMutation.isPending || !bulkMapping.trim()} onClick={()=>applyBulkMapping.mutate()} className="mt-2 rounded border border-[var(--border-primary)] px-3 py-2 text-xs">{applyBulkMapping.isPending?'Checking mappings…':'Apply barcode mappings'}</button>
                <label className="ml-2 mt-2 inline-block cursor-pointer rounded border border-[var(--border-primary)] px-3 py-2 text-xs">Upload CSV
                    <input type="file" accept=".csv,.tsv,.txt,text/csv" aria-label="Upload barcode mapping CSV" className="hidden" disabled={applyBulkMapping.isPending || submitMutation.isPending}
                        onChange={async event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) setBulkMapping((await file.text()).trim()); }} />
                </label>
            </details>
            <div className="overflow-x-auto rounded border border-[var(--border-primary)]">
                <table className="w-full min-w-[1080px] text-left text-sm">
                    <thead className="bg-[var(--bg-tertiary)] text-xs uppercase tracking-wide text-[var(--text-secondary)]">
                        <tr>
                            <th className="px-3 py-2">Barcode</th>
                            <th className="px-3 py-2">Sample alias</th>
                            <th className="px-3 py-2">Exact sequences / revisions</th>
                            <th className="px-3 py-2">Digest</th>
                            <th className="px-3 py-2">Reads</th>
                            <th className="px-3 py-2">Status</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--border-primary)]">
                        {units.map((unit) => {
                            const mapping = mappingByUnit[unit.unit_id] || {
                                ...emptyMapping(),
                                sampleAlias: unit.sample_alias || '',
                            };
                            const extras = extraByUnit[unit.unit_id] || [];
                            const busy = applyBulkMapping.isPending || submitMutation.isPending;
                            const chosen = [mapping, ...extras].filter(item => item.sequenceId && item.revisionId).length;
                            const setExtras = (items: BarcodeMappingDraft[]) => setExtraByUnit(previous => ({ ...previous, [unit.unit_id]: items }));
                            return (
                                <Fragment key={unit.unit_id}>
                                <tr data-testid={`barcode-mapping-row-${unit.unit_id}`}>
                                    <td className="px-3 py-2 align-top font-mono text-[var(--text-primary)]">{unit.unit_id}</td>
                                    <td className="px-3 py-2 align-top">
                                        <input
                                            aria-label={`Sample alias for ${unit.unit_id}`}
                                            value={mapping.sampleAlias}
                                            onChange={(event) => setMappingByUnit((previous) => ({ ...previous, [unit.unit_id]: { ...mapping, sampleAlias: event.target.value } }))}
                                            placeholder="Optional"
                                            disabled={applyBulkMapping.isPending || submitMutation.isPending || Boolean(unit.sample_alias)}
                                            title={unit.sample_alias ? 'Authoritative alias from the Dorado sample sheet' : 'Optional operator alias when no sample sheet alias exists'}
                                            className="w-full min-w-[10rem] rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1.5 text-sm text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-70"
                                        />
                                    </td>
                                    <BarcodeMappingCells
                                        unitId={unit.unit_id}
                                        sequences={sequences}
                                        mapping={mapping}
                                        disabled={applyBulkMapping.isPending || submitMutation.isPending}
                                        onChange={(next) => setMappingByUnit((previous) => ({ ...previous, [unit.unit_id]: next }))}
                                    />
                                    <td className="px-3 py-2 align-top font-mono text-[var(--text-secondary)]">{unit.read_count}</td>
                                    <td className="px-3 py-2 align-top text-xs text-[var(--text-secondary)]" data-testid={`barcode-mapping-status-${unit.unit_id}`}>
                                        {chosen === 0 || chosen < 1 + extras.length ? 'Needs a sequence' : chosen === 1 ? 'Mapped' : `Mapped · ${chosen} sequences`}
                                    </td>
                                </tr>
                                {extras.map((extra, index) => (
                                    <tr key={`${unit.unit_id}-extra-${index}`} data-testid={`barcode-mapping-row-${unit.unit_id}-extra-${index + 1}`}>
                                        <td className="px-3 py-2" />
                                        <td className="px-3 py-2 text-right align-top">
                                            <button type="button" aria-label={`Remove sequence ${index + 2} from ${unit.unit_id}`} disabled={busy} onClick={() => setExtras(extras.filter((_, position) => position !== index))} className="rounded border border-[var(--border-primary)] px-2 py-1 text-xs text-[var(--text-secondary)]">×</button>
                                        </td>
                                        <BarcodeMappingCells
                                            unitId={`${unit.unit_id} sequence ${index + 2}`}
                                            sequences={sequences}
                                            mapping={extra}
                                            disabled={busy}
                                            onChange={(next) => setExtras(extras.map((item, position) => position === index ? next : item))}
                                        />
                                        <td className="px-3 py-2" colSpan={2} />
                                    </tr>
                                ))}
                                <tr>
                                    <td className="px-3 pb-2" colSpan={2} />
                                    <td className="px-3 pb-2" colSpan={4}>
                                        <button type="button" data-testid={`barcode-add-sequence-${unit.unit_id}`} disabled={busy} onClick={() => setExtras([...extras, emptyMapping()])} className="text-xs text-[var(--text-action)] hover:underline disabled:opacity-50">+ Add sequence</button>
                                    </td>
                                </tr>
                                </Fragment>
                            );
                        })}
                        <tr data-testid="barcode-unclassified-row" className="bg-[var(--bg-tertiary)]/60">
                            <td className="px-3 py-2 font-mono font-semibold text-[var(--text-primary)]">unclassified</td>
                            <td className="px-3 py-2 text-xs text-[var(--text-secondary)]">Locked literal row</td>
                            <td className="px-3 py-2 text-xs text-[var(--text-secondary)]" colSpan={2}>Not assignable and never sent in mappings.</td>
                            <td className="px-3 py-2 font-mono text-[var(--text-secondary)]">{unclassifiedUnit?.read_count ?? '—'}</td>
                            <td className="px-3 py-2" />
                        </tr>
                    </tbody>
                </table>
            </div>
            <p className="text-xs text-[var(--text-secondary)]">Several sequences on one barcode: each read goes to its best-matching sequence, then each sequence is analysed on its own reads.</p>
            {unitsQuery.isSuccess && units.length === 0 && <div className="text-xs text-[var(--text-secondary)]">The manifest contains no assignable barcode units. The unclassified row remains locked.</div>}
            {message && <div className="text-xs text-[var(--text-secondary)]">{message}</div>}
        </section>
    );
}
