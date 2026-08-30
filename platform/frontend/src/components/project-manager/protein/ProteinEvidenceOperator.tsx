import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
    createResearchRecord,
    listDomainResearchRecords,
    projectManagerErrorMessage,
    type RecordKind,
} from '../../../lib/projectManager';

interface ProteinEvidenceOperatorProps {
    projectId: string;
    globalExperimentId: string;
    domainExperimentId: string;
}

const RECORD_KINDS: Array<{ value: RecordKind; label: string }> = [
    { value: 'note', label: 'Note' },
    { value: 'observation', label: 'Observation' },
    { value: 'decision', label: 'Decision' },
    { value: 'conclusion', label: 'Conclusion' },
];

const INPUT = 'w-full rounded-md border border-border-primary bg-surface px-3 py-2 text-sm text-content-primary disabled:cursor-not-allowed disabled:opacity-40';
const BUTTON = 'rounded-md bg-primary px-3 py-2 text-xs font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40';

function normalizedReceiptIds(value: string): string[] {
    return Array.from(new Set(value.split(',').map((item) => item.trim()).filter(Boolean)));
}

export function ProteinEvidenceOperator({ projectId, globalExperimentId, domainExperimentId }: ProteinEvidenceOperatorProps) {
    const queryClient = useQueryClient();
    const scope = [projectId, globalExperimentId, domainExperimentId] as const;
    const [kind, setKind] = useState<RecordKind>('note');
    const [body, setBody] = useState('');
    const [receiptIds, setReceiptIds] = useState('');

    const records = useQuery({
        queryKey: ['protein-project', ...scope, 'research-records'],
        queryFn: ({ signal }) => listDomainResearchRecords(...scope, signal),
        retry: false,
    });
    const createRecord = useMutation({
        mutationFn: () => createResearchRecord(
            { projectId, globalExperimentId, domainExperimentId },
            {
                record_kind: kind,
                body: body.trim(),
                source_receipt_ids: normalizedReceiptIds(receiptIds),
            },
        ),
        onSuccess: async () => {
            setBody('');
            setReceiptIds('');
            await queryClient.invalidateQueries({ queryKey: ['protein-project', ...scope, 'research-records'] });
        },
    });

    const error = records.error ?? createRecord.error;
    return (
        <div className="space-y-4">
            {error && (
                <p role="alert" className="rounded-lg border border-error/50 bg-error/10 p-3 text-xs text-error">
                    {projectManagerErrorMessage(error)}
                </p>
            )}
            <section className="rounded-xl border border-border-primary bg-surface-secondary p-4">
                <h2 className="text-sm font-semibold text-content">Add a Project research record</h2>
                <p className="mt-1 text-xs text-content-muted">
                    Notes, observations, decisions, and conclusions stay attached to this exact Protein Domain. Optional receipt IDs bind the statement to immutable evidence.
                </p>
                <div className="mt-3 grid gap-3 lg:grid-cols-[12rem_1fr]">
                    <label className="text-xs text-content-secondary">Record type
                        <select className={`${INPUT} mt-1`} value={kind} onChange={(event) => setKind(event.target.value as RecordKind)}>
                            {RECORD_KINDS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                        </select>
                    </label>
                    <label className="text-xs text-content-secondary">Evidence receipt IDs
                        <input className={`${INPUT} mt-1`} value={receiptIds} onChange={(event) => setReceiptIds(event.target.value)} placeholder="Optional, comma separated" />
                    </label>
                </div>
                <label className="mt-3 block text-xs text-content-secondary">Record
                    <textarea className={`${INPUT} mt-1 min-h-28`} value={body} onChange={(event) => setBody(event.target.value)} placeholder="Record the scientific statement and its basis." />
                </label>
                <button type="button" className={`${BUTTON} mt-3`} disabled={!body.trim() || createRecord.isPending} onClick={() => createRecord.mutate()}>
                    {createRecord.isPending ? 'Saving…' : `Save ${kind}`}
                </button>
            </section>

            <section className="rounded-xl border border-border-primary bg-surface-secondary p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-sm font-semibold text-content">Domain research record</h2>
                    <button type="button" className="text-xs font-semibold text-accent disabled:opacity-40" disabled={records.isFetching} onClick={() => records.refetch()}>Refresh</button>
                </div>
                <div className="mt-3 space-y-3">
                    {(records.data?.items ?? []).map((record) => (
                        <article key={record.id} className="rounded-lg border border-border-primary bg-surface p-3">
                            <div className="flex flex-wrap items-start justify-between gap-2">
                                <span className="rounded-full border border-border-primary px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-content-secondary">{record.record_kind}</span>
                                <time className="text-xs text-content-muted">{new Date(record.created_at).toLocaleString()}</time>
                            </div>
                            <p className="mt-3 whitespace-pre-wrap text-sm text-content-secondary">{record.body}</p>
                            {record.source_receipt_ids.length > 0 && (
                                <p className="mt-3 break-all font-mono text-xs text-content-muted">Evidence: {record.source_receipt_ids.join(', ')}</p>
                            )}
                        </article>
                    ))}
                    {!records.isLoading && (records.data?.items ?? []).length === 0 && (
                        <p className="rounded-lg border border-dashed border-border-primary p-4 text-sm text-content-muted">No research records are attached to this exact Protein Domain.</p>
                    )}
                </div>
                {records.data?.next_cursor && (
                    <p className="mt-3 text-xs text-warning">The bounded record page has additional server rows. Use Project Manager history for the complete record.</p>
                )}
            </section>
        </div>
    );
}
