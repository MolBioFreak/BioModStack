import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
    createOntRunIntent,
    fetchOntDeviceStatus,
    fetchOntProtocolOptions,
    startOntRunIntent,
    type OntInstrumentRun,
    type OntLiveDevice,
    type OntProtocolOption,
} from '../../lib/api';
import { jobPollingInterval } from '../../lib/queryPolling';

interface OntInstrumentPanelProps {
    onAnalyzeExistingData: () => void;
}

function statusLabel(status?: string): string {
    switch (status) {
        case 'configured': return 'Mk1D discovery available';
        case 'not_configured': return 'MinKNOW not configured';
        case 'client_missing': return 'MinKNOW client missing';
        case 'unreachable': return 'MinKNOW unreachable';
        default: return status || 'unknown';
    }
}

function deviceStateLabel(device: OntLiveDevice): string {
    if (device.running) return 'position running';
    if (!device.flow_cell.present) return 'flow cell absent';
    return device.available_for_run ? 'available for run' : (device.state || 'not available');
}

function responseDetail(error: unknown): string | undefined {
    if (!error || typeof error !== 'object') return undefined;
    const response = (error as { response?: unknown }).response;
    if (!response || typeof response !== 'object') return undefined;
    const data = (response as { data?: unknown }).data;
    if (!data || typeof data !== 'object') return undefined;
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object') {
        const message = (detail as { message?: unknown }).message;
        if (typeof message === 'string') return message;
    }
    return undefined;
}

function isPhysicalStartDisabledAfterRevalidation(error: unknown): boolean {
    if (!error || typeof error !== 'object') return false;
    const response = (error as { response?: unknown }).response;
    if (!response || typeof response !== 'object') return false;
    const status = (response as { status?: unknown }).status;
    const detail = responseDetail(error)?.replace(/[.]+$/, '');
    return status === 501
        && detail === 'MinKNOW protocol start remains disabled pending separately authorized supervised commissioning';
}

export function OntInstrumentPanel({ onAnalyzeExistingData }: OntInstrumentPanelProps) {
    const [selectedPosition, setSelectedPosition] = useState('');
    const [sampleId, setSampleId] = useState('');
    const [experimentGroup, setExperimentGroup] = useState('');
    const [lastRun, setLastRun] = useState<OntInstrumentRun | null>(null);
    const [message, setMessage] = useState('');
    const deviceStatus = useQuery({
        queryKey: ['ont-device-status'],
        queryFn: async () => (await fetchOntDeviceStatus()).data,
        refetchInterval: (query) => jobPollingInterval(10000, query),
    });
    const data = deviceStatus.isError ? undefined : deviceStatus.data;
    // The API/host already enforce Mk1D-only discovery; the UI never filters eligibility.
    const devices = useMemo(() => data?.live_devices ?? [], [data?.live_devices]);
    const selectedDevice = devices.find((device) => device.position === selectedPosition) ?? devices[0];
    const selectedPositionForQuery = selectedDevice?.position ?? '';
    const protocolOptions = useQuery({
        queryKey: ['ont-protocol-options', selectedPositionForQuery],
        queryFn: async () => (await fetchOntProtocolOptions(selectedPositionForQuery)).data,
        enabled: Boolean(selectedPositionForQuery),
        refetchInterval: (query) => jobPollingInterval(10000, query),
    });
    const instrumentEvidenceError = deviceStatus.isError || protocolOptions.isError;
    const effectiveProtocolOptions = instrumentEvidenceError ? undefined : protocolOptions.data;
    const selectedOption: OntProtocolOption | undefined = effectiveProtocolOptions?.options[0];

    const startRun = useMutation({
        mutationFn: async () => {
            if (!selectedDevice?.position || !selectedOption) throw new Error('No server-issued protocol option is available');
            const intent = await createOntRunIntent(selectedDevice.position, {
                option_id: selectedOption.option_id,
                option_receipt_id: selectedOption.option_receipt_id,
                sample_id: sampleId.trim() || undefined,
                experiment_group: experimentGroup.trim() || undefined,
            });
            // Persist and render the durable BMS intent before the separate
            // revalidation request. A disabled physical start must not erase a
            // successfully created intent from the operator surface.
            setLastRun(intent.data);
            setMessage(`BMS run ${intent.data.id} is armed; revalidating before physical start.`);
            try {
                const started = await startOntRunIntent(intent.data.id, {
                    confirm_start: true,
                    intent_generation: intent.data.observed_generation,
                });
                return { run: started.data, revalidated: false };
            } catch (error) {
                if (isPhysicalStartDisabledAfterRevalidation(error)) {
                    return { run: intent.data, revalidated: true };
                }
                throw error;
            }
        },
        onSuccess: ({ run, revalidated }) => {
            setLastRun(run);
            setMessage(revalidated
                ? `BMS run ${run.id} remains armed after fresh revalidation; physical MinKNOW start remains disabled.`
                : 'Intent was freshly checked; physical MinKNOW start remains disabled.');
            void deviceStatus.refetch();
        },
        onError: (error) => setMessage(
            responseDetail(error) ?? (error instanceof Error ? error.message : 'ONT intent validation failed')
        ),
    });

    const blockers = effectiveProtocolOptions?.blockers ?? [];
    const canStart = Boolean(
        !instrumentEvidenceError
        && selectedDevice?.available_for_run
        && selectedOption
        && effectiveProtocolOptions?.can_start
        && !startRun.isPending
    );
    const availableCount = useMemo(() => devices.filter((device) => device.available_for_run).length, [devices]);

    return (
        <section className="space-y-5 rounded-2xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-5 shadow-lg shadow-black/10">
            <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-xl font-semibold text-[var(--text-primary)]">ONT instrument control</h2>
                        <span className="rounded-full border border-cyan-500/40 bg-cyan-500/10 px-2 py-1 text-xs font-semibold uppercase tracking-wide text-cyan-100">Mk1D / MinKNOW</span>
                    </div>
                    <p className="max-w-2xl text-sm text-[var(--text-secondary)]">Select a server-discovered Mk1D position and submit its opaque protocol intent. Real starts remain disabled until a separately authorized commissioning path exists.</p>
                </div>
                <button type="button" onClick={onAnalyzeExistingData} className="rounded-lg border border-[var(--border-primary)] px-3 py-2 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]">Analyze existing data</button>
            </div>

            <div className="grid gap-3 md:grid-cols-3">
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3"><div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Mk1D link</div><div className="mt-1 text-base font-semibold text-[var(--text-primary)]">{deviceStatus.isLoading ? 'Checking…' : statusLabel(data?.implementation_status)}</div></div>
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3"><div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Positions</div><div className="mt-1 text-base font-semibold text-[var(--text-primary)]">{devices.length}</div></div>
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3"><div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Available</div><div className="mt-1 text-base font-semibold text-[var(--text-primary)]">{availableCount}</div></div>
            </div>

            {instrumentEvidenceError ? <p role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">Current instrument evidence is unavailable after a refresh failure. Intent validation is disabled until a fresh device and protocol response succeeds.</p> : null}

            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
                <div className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                    <div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Instrument positions</h3><p className="text-xs text-[var(--text-secondary)]">Only host-approved Mk1D cards are returned.</p></div><button type="button" onClick={() => void deviceStatus.refetch()} className="text-sm text-[var(--accent-secondary)]">Refresh</button></div>
                    {data?.message ? <p className="rounded-lg bg-slate-900/40 p-2 text-xs text-[var(--text-secondary)]">{data.message}</p> : null}
                    <div className="grid gap-3 md:grid-cols-2">
                        {devices.length === 0 ? <div className="rounded-lg border border-dashed border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 text-sm text-[var(--text-secondary)]">No live Mk1D positions reported by MinKNOW.</div> : devices.map((device) => <button key={device.position} type="button" onClick={() => setSelectedPosition(device.position)} className={`rounded-xl border p-4 text-left transition ${selectedDevice?.position === device.position ? 'border-cyan-400 bg-cyan-500/10' : 'border-[var(--border-primary)] bg-[var(--bg-secondary)] hover:border-cyan-500/50'}`}><div className="text-base font-semibold text-[var(--text-primary)]">{device.position}</div><div className="text-xs text-[var(--text-secondary)]">Mk1D · {deviceStateLabel(device)}</div><div className="mt-3 text-xs text-[var(--text-secondary)]">Flow cell: {device.flow_cell.present ? 'present' : 'absent'} · Running: {device.running ? 'yes' : 'no'}</div></button>)}
                    </div>
                </div>

                <div className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                    <div><h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Run intent</h3><p className="text-xs text-[var(--text-secondary)]">Protocol and output policy are server-issued opaque handles.</p></div>
                    <label className="block text-xs font-semibold text-[var(--text-secondary)]">Sample ID<input value={sampleId} onChange={(event) => setSampleId(event.target.value)} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)]" /></label>
                    <label className="block text-xs font-semibold text-[var(--text-secondary)]">Experiment group<input value={experimentGroup} onChange={(event) => setExperimentGroup(event.target.value)} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)]" /></label>
                    <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs text-[var(--text-secondary)]">{selectedOption ? `${selectedOption.protocol_label} · ${selectedOption.output_policy_label}` : 'No protocol option is currently available.'}{blockers.length ? <div className="mt-2 text-amber-100">Preflight blockers: {blockers.join(', ')}</div> : null}</div>
                    <button type="button" disabled={!canStart} onClick={() => startRun.mutate()} className="rounded-lg bg-[var(--accent-secondary)] px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">Validate run intent</button>
                    {message ? <p className="text-xs text-[var(--text-secondary)]">{message}</p> : null}
                    {lastRun ? <div className="rounded-lg border border-[var(--border-primary)] p-3 text-xs text-[var(--text-secondary)]">Intent {lastRun.id} · {lastRun.status}</div> : null}
                </div>
            </div>
        </section>
    );
}
