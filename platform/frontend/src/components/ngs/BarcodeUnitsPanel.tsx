import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
    fetchOntBarcodeUnits,
    submitOntBarcodeUnit,
    type OntBarcodeUnit,
} from '../../lib/api';

type TargetWorkflow = 'ont_plasmid_qc' | 'ont_construct_screening';

interface BarcodeUnitsPanelProps {
    jobId: string;
    enabled: boolean;
    defaultReference: string;
}

export function BarcodeUnitsPanel({ jobId, enabled, defaultReference }: BarcodeUnitsPanelProps) {
    const queryClient = useQueryClient();
    const [reference, setReference] = useState(defaultReference);
    const [targetWorkflow, setTargetWorkflow] = useState<TargetWorkflow>('ont_plasmid_qc');
    const [message, setMessage] = useState('');
    const unitsQuery = useQuery<OntBarcodeUnit[]>({
        queryKey: ['ont-barcode-units', jobId],
        queryFn: async () => (await fetchOntBarcodeUnits(jobId)).data.units,
        enabled,
        retry: false,
    });
    const submitMutation = useMutation({
        mutationFn: (unit: OntBarcodeUnit) => submitOntBarcodeUnit(jobId, unit.unit_id, {
            target_workflow: targetWorkflow,
            reference_fasta: reference.trim(),
            name: `${unit.unit_id} ${targetWorkflow}`,
        }),
        onSuccess: (response) => {
            setMessage(`Submitted ${response.data.id}`);
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
        },
        onError: (error: unknown) => setMessage(error instanceof Error ? error.message : 'Barcode-unit submission failed'),
    });

    if (!enabled) return null;
    return (
        <section className="space-y-3 rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4" data-testid="ont-barcode-units-panel">
            <div>
                <h4 className="text-sm font-semibold text-[var(--text-primary)]">Demultiplexed barcode units</h4>
                <p className="text-xs text-[var(--text-secondary)]">Each unit and its BAM are independently digest-verified before submission.</p>
            </div>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                <select value={targetWorkflow} onChange={(event) => setTargetWorkflow(event.target.value as TargetWorkflow)} className="rounded bg-[var(--bg-tertiary)] p-2 text-sm">
                    <option value="ont_plasmid_qc">Plasmid QC</option>
                    <option value="ont_construct_screening">Construct screening</option>
                </select>
                <input value={reference} onChange={(event) => setReference(event.target.value)} placeholder="Allowed reference FASTA path" className="rounded bg-[var(--bg-tertiary)] p-2 text-sm" />
            </div>
            {unitsQuery.isLoading && <div className="text-xs text-[var(--text-secondary)]">Loading verified units…</div>}
            {unitsQuery.isError && <div className="text-xs text-rose-400">No verified barcode manifest is available.</div>}
            <div className="space-y-2">
                {(unitsQuery.data || []).map((unit) => (
                    <div key={unit.unit_id} className="flex flex-wrap items-center justify-between gap-3 rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-3">
                        <div>
                            <div className="text-sm font-medium text-[var(--text-primary)]">{unit.unit_id}</div>
                            <div className="text-xs text-[var(--text-secondary)]">{unit.read_count} reads · BAM {unit.bam_sha256.slice(0, 12)}… · manifest {unit.unit_manifest_sha256.slice(0, 12)}…</div>
                        </div>
                        <button type="button" disabled={!reference.trim() || submitMutation.isPending} onClick={() => submitMutation.mutate(unit)} className="rounded bg-blue-600 px-3 py-1.5 text-xs text-white disabled:cursor-not-allowed disabled:opacity-40">
                            Submit unit
                        </button>
                    </div>
                ))}
                {unitsQuery.isSuccess && unitsQuery.data.length === 0 && <div className="text-xs text-[var(--text-secondary)]">The manifest contains no barcode units.</div>}
            </div>
            {message && <div className="text-xs text-[var(--text-secondary)]">{message}</div>}
        </section>
    );
}
