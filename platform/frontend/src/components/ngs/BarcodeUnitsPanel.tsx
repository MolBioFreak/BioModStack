import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    fetchMolBioSequenceRevisions,
    fetchNucleotideSequences,
    fetchOntBarcodeUnits,
    issueMolBioNgsReceipt,
    submitOntBarcodeBatch,
    type MolBioSequenceRevision,
    type NucleotideSequenceListItem,
    type OntBarcodeUnit,
} from '../../lib/api';

type TargetWorkflow = 'ont_plasmid_qc' | 'ont_construct_screening';

interface BarcodeUnitsPanelProps {
    jobId: string;
    enabled: boolean;
}

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
}

function BarcodeMappingCells({ unitId, sequences, mapping, onChange }: BarcodeMappingCellsProps) {
    const revisionsQuery = useQuery<MolBioSequenceRevision[]>({
        queryKey: ['ont-barcode-revisions', unitId, mapping.sequenceId],
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
                        disabled={!mapping.sequenceId || revisionsQuery.isLoading}
                        className="w-full min-w-[15rem] rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1.5 text-sm text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        <option value="">Select exact revision…</option>
                        {revisions.map((revision) => (
                            <option key={revision.id} value={revision.id}>
                                r{revision.revision_number} · {revision.is_current ? 'current' : 'historical'}
                            </option>
                        ))}
                    </select>
                    {revisionsQuery.isError && <div className="text-xs text-rose-400">Unable to load revisions.</div>}
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

export function BarcodeUnitsPanel({ jobId, enabled }: BarcodeUnitsPanelProps) {
    const queryClient = useQueryClient();
    const [targetWorkflow, setTargetWorkflow] = useState<TargetWorkflow>('ont_plasmid_qc');
    const [namePrefix, setNamePrefix] = useState('');
    const [pinnedGpu, setPinnedGpu] = useState('');
    const [message, setMessage] = useState('');
    const [mappingByUnit, setMappingByUnit] = useState<Record<string, BarcodeMappingDraft>>({});

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
        enabled,
        staleTime: 30_000,
        retry: false,
    });

    const allUnits = unitsQuery.data || [];
    const units = allUnits.filter((unit) => unit.unit_id !== 'unclassified');
    const unclassifiedUnit = allUnits.find((unit) => unit.unit_id === 'unclassified');
    const sequences = sequencesQuery.data || [];

    const submitMutation = useMutation({
        mutationFn: async () => {
            const drafts = units.map((unit) => ({
                unit,
                draft: mappingByUnit[unit.unit_id] || {
                    ...emptyMapping(),
                    sampleAlias: unit.sample_alias || '',
                },
            }));
            if (drafts.length === 0 || drafts.some(({ draft }) => !draft.sequenceId || !draft.revisionId)) {
                throw new Error('Every canonical barcode unit requires a saved sequence and exact revision before batch submission.');
            }
            const selected = drafts;
            const parsedPinnedGpu = pinnedGpu.trim() ? Number.parseInt(pinnedGpu, 10) : null;
            if (parsedPinnedGpu !== null && (!Number.isInteger(parsedPinnedGpu) || parsedPinnedGpu < 0)) {
                throw new Error('Pinned GPU must be a non-negative integer.');
            }

            const mappings = await Promise.all(selected.map(async ({ unit, draft }) => {
                const receiptResponse = await issueMolBioNgsReceipt(draft.sequenceId, { revision_id: draft.revisionId });
                const receiptId = receiptResponse.data.receipt_id.trim();
                if (!receiptId) throw new Error(`No receipt was returned for ${unit.unit_id}.`);
                return {
                    unit_id: unit.unit_id,
                    sample_alias: unit.sample_alias || draft.sampleAlias.trim() || null,
                    molbio_ngs_receipt_id: receiptId,
                };
            }));

            return submitOntBarcodeBatch(jobId, {
                idempotency_key: newIdempotencyKey('ont-barcode-batch'),
                target_workflow: targetWorkflow,
                ...(namePrefix.trim() ? { name_prefix: namePrefix.trim() } : {}),
                ...(parsedPinnedGpu !== null ? { pinned_gpu: parsedPinnedGpu } : {}),
                mappings,
            });
        },
        onSuccess: (response) => {
            setMessage(`Submitted ${response.data.child_job_ids.length} barcode children in reference set ${response.data.reference_set_id}.`);
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
        },
        onError: (error: unknown) => setMessage(error instanceof Error ? error.message : 'Barcode batch submission failed'),
    });

    if (!enabled) return null;

    return (
        <section className="w-full space-y-3 rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4" data-testid="ont-barcode-units-panel">
            <div>
                <h4 className="text-sm font-semibold text-[var(--text-primary)]">Demultiplexed barcode units</h4>
                <p className="text-xs text-[var(--text-secondary)]">Map every canonical barcodeNN unit to an immutable MolBio revision. The batch submits all mappings together or creates no children.</p>
            </div>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
                <select value={targetWorkflow} onChange={(event) => setTargetWorkflow(event.target.value as TargetWorkflow)} className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 text-sm text-[var(--text-primary)]">
                    <option value="ont_plasmid_qc">Plasmid QC</option>
                    <option value="ont_construct_screening">Construct screening</option>
                </select>
                <input value={namePrefix} onChange={(event) => setNamePrefix(event.target.value)} placeholder="Optional name prefix" className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 text-sm text-[var(--text-primary)]" />
                <input value={pinnedGpu} onChange={(event) => setPinnedGpu(event.target.value)} inputMode="numeric" placeholder="Optional pinned GPU" className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 text-sm text-[var(--text-primary)]" />
                <button type="button" disabled={submitMutation.isPending || units.length === 0} onClick={() => submitMutation.mutate()} className="rounded bg-blue-600 px-3 py-2 text-sm text-white disabled:cursor-not-allowed disabled:opacity-40">
                    {submitMutation.isPending ? 'Submitting batch…' : 'Submit mapped batch'}
                </button>
            </div>
            {unitsQuery.isLoading && <div className="text-xs text-[var(--text-secondary)]">Loading canonical units…</div>}
            {unitsQuery.isError && <div className="text-xs text-rose-400">No verified barcode manifest is available.</div>}
            {sequencesQuery.isError && <div className="text-xs text-rose-400">Saved MolBio sequences could not be loaded.</div>}
            <div className="overflow-x-auto rounded border border-[var(--border-primary)]">
                <table className="w-full min-w-[1080px] text-left text-sm">
                    <thead className="bg-[var(--bg-tertiary)] text-xs uppercase tracking-wide text-[var(--text-secondary)]">
                        <tr>
                            <th className="px-3 py-2">Canonical unit</th>
                            <th className="px-3 py-2">Sample alias</th>
                            <th className="px-3 py-2">Exact sequence / revision</th>
                            <th className="px-3 py-2">Digest</th>
                            <th className="px-3 py-2">Reads</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--border-primary)]">
                        {units.map((unit) => {
                            const mapping = mappingByUnit[unit.unit_id] || {
                                ...emptyMapping(),
                                sampleAlias: unit.sample_alias || '',
                            };
                            return (
                                <tr key={unit.unit_id} data-testid={`barcode-mapping-row-${unit.unit_id}`}>
                                    <td className="px-3 py-2 align-top font-mono text-[var(--text-primary)]">{unit.unit_id}</td>
                                    <td className="px-3 py-2 align-top">
                                        <input
                                            aria-label={`Sample alias for ${unit.unit_id}`}
                                            value={mapping.sampleAlias}
                                            onChange={(event) => setMappingByUnit((previous) => ({ ...previous, [unit.unit_id]: { ...mapping, sampleAlias: event.target.value } }))}
                                            placeholder="Optional"
                                            disabled={Boolean(unit.sample_alias)}
                                            title={unit.sample_alias ? 'Authoritative alias from the Dorado sample sheet' : 'Optional operator alias when no sample sheet alias exists'}
                                            className="w-full min-w-[10rem] rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2 py-1.5 text-sm text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-70"
                                        />
                                    </td>
                                    <BarcodeMappingCells
                                        unitId={unit.unit_id}
                                        sequences={sequences}
                                        mapping={mapping}
                                        onChange={(next) => setMappingByUnit((previous) => ({ ...previous, [unit.unit_id]: next }))}
                                    />
                                    <td className="px-3 py-2 align-top font-mono text-[var(--text-secondary)]">{unit.read_count}</td>
                                </tr>
                            );
                        })}
                        <tr data-testid="barcode-unclassified-row" className="bg-[var(--bg-tertiary)]/60">
                            <td className="px-3 py-2 font-mono font-semibold text-[var(--text-primary)]">unclassified</td>
                            <td className="px-3 py-2 text-xs text-[var(--text-secondary)]">Locked literal row</td>
                            <td className="px-3 py-2 text-xs text-[var(--text-secondary)]" colSpan={2}>Not assignable and never sent in mappings.</td>
                            <td className="px-3 py-2 font-mono text-[var(--text-secondary)]">{unclassifiedUnit?.read_count ?? '—'}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
            {unitsQuery.isSuccess && units.length === 0 && <div className="text-xs text-[var(--text-secondary)]">The manifest contains no assignable barcode units. The unclassified row remains locked.</div>}
            {message && <div className="text-xs text-[var(--text-secondary)]">{message}</div>}
        </section>
    );
}
