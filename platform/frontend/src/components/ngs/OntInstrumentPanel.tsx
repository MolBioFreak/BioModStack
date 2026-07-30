import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
    fetchOntDeviceStatus,
    fetchOntProtocolOptions,
    beginOntHardwareCheck,
    refreshOntPosition,
    startOntInstrumentRun,
    stopOntInstrumentRun,
    type OntInstrumentRun,
    type OntLiveDevice,
} from '../../lib/api';
import { jobPollingInterval } from '../../lib/queryPolling';

interface OntInstrumentPanelProps {
    onAnalyzeExistingData: () => void;
}

type OutputKey = 'pod5' | 'fastq' | 'bam';

const DEFAULT_OUTPUTS: Record<OutputKey, boolean> = { pod5: true, fastq: true, bam: false };
const OUTPUT_LABELS: Array<[OutputKey, string]> = [
    ['pod5', 'POD5 raw signal'],
    ['fastq', 'FASTQ reads'],
    ['bam', 'BAM alignments'],
];

function statusLabel(status?: string): string {
    switch (status) {
        case 'not_configured':
            return 'MinKNOW not configured';
        case 'client_missing':
            return 'MinKNOW client missing';
        case 'unreachable':
            return 'MinKNOW unreachable';
        case 'auth_error':
            return 'MinKNOW auth error';
        case 'host_agent_unavailable':
            return 'BMS host-agent unavailable';
        case 'configured':
            return 'MinKNOW configured';
        default:
            return status || 'unknown';
    }
}

function deviceStateLabel(device: OntLiveDevice): string {
    if (device.fake_or_demo_device) return 'test mode Mk1D';
    if (device.running) return 'position running';
    if (!device.flow_cell?.present) return 'flowcell absent';
    if (device.available_for_run) return 'available for run';
    return device.state || 'not available';
}

function statusTone(status?: string): string {
    if (status === 'configured') return 'border-emerald-500/40 bg-emerald-500/10 text-emerald-100';
    if (status === 'client_missing' || status === 'not_configured') return 'border-slate-600 bg-slate-900/50 text-slate-200';
    return 'border-amber-500/40 bg-amber-500/10 text-amber-100';
}

export function OntInstrumentPanel({ onAnalyzeExistingData }: OntInstrumentPanelProps) {
    const [selectedPosition, setSelectedPosition] = useState<string>('');
    const [lastRun, setLastRun] = useState<OntInstrumentRun | null>(null);
    const [hardwareCheckMessage, setHardwareCheckMessage] = useState<string>('');
    const [sampleId, setSampleId] = useState<string>('plasmid-qc-test');
    const [experimentGroup, setExperimentGroup] = useState<string>('bms_plasmid_verification');
    const [kit, setKit] = useState<string>('SQK-LSK114');
    const [qualityMode, setQualityMode] = useState<string>('sup');
    const [outputs, setOutputs] = useState<Record<OutputKey, boolean>>(DEFAULT_OUTPUTS);
    const { data, isLoading, refetch } = useQuery({
        queryKey: ['ont-device-status'],
        queryFn: async () => (await fetchOntDeviceStatus()).data,
        refetchInterval: (query) => jobPollingInterval(10000, query),
    });
    const liveDevices = data?.live_devices ?? [];
    const devices = liveDevices.filter((device) => device.device_type === 'mk1d');
    const availableDevices = useMemo(
        () => devices.filter((device) => device.available_for_run && device.position),
        [devices],
    );
    const selectedDevice = devices.find((device) => device.position === selectedPosition) ?? availableDevices[0] ?? devices[0];
    const selectedIsTestMode = Boolean(selectedDevice?.fake_or_demo_device);
    const minKnowStatus = isLoading ? 'checking' : statusLabel(data?.implementation_status);
    const visibleOutputLabels = OUTPUT_LABELS.filter(([key]) => outputs[key]).map(([, label]) => label);
    const selectedPositionForQuery = selectedDevice?.fake_or_demo_device ? '' : selectedDevice?.position || '';
    const protocolOptions = useQuery({
        queryKey: ['ont-protocol-options', selectedPositionForQuery, kit],
        queryFn: async () => (await fetchOntProtocolOptions(selectedPositionForQuery, kit)).data,
        enabled: Boolean(selectedPositionForQuery),
        refetchInterval: (query) => jobPollingInterval(10000, query),
    });

    const refreshPosition = useMutation({
        mutationFn: async () => {
            if (!selectedDevice?.position) {
                throw new Error('No real ONT position selected for refresh');
            }
            const response = await refreshOntPosition(selectedDevice.position);
            return response.data;
        },
        onSuccess: () => void refetch(),
    });

    const beginHardwareCheck = useMutation({
        mutationFn: async () => {
            if (!selectedDevice?.position) {
                throw new Error('No real ONT position selected for hardware check');
            }
            const ok = window.confirm('Start a MinKNOW hardware check on this position? This is diagnostic, not sequencing, but it will start a MinKNOW check protocol.');
            if (!ok) {
                throw new Error('Hardware check cancelled');
            }
            const response = await beginOntHardwareCheck(selectedDevice.position);
            return response.data;
        },
        onSuccess: (payload) => {
            setHardwareCheckMessage(`${payload.detail}${payload.hardware_check_run_id ? ` · run ${payload.hardware_check_run_id}` : ''}`);
            void refetch();
        },
        onError: (error) => setHardwareCheckMessage(error instanceof Error ? error.message : String(error)),
    });

    const startRun = useMutation({
        mutationFn: async () => {
            if (!selectedDevice?.position) {
                throw new Error('No real available ONT position selected');
            }
            const response = await startOntInstrumentRun(selectedDevice.position, {
                sample_id: sampleId.trim(),
                kit: kit.trim(),
                experiment_group: experimentGroup.trim(),
                outputs,
                basecalling: { enabled: true, quality_mode: qualityMode, modified_bases: 'none' },
                confirm_start: true,
            });
            return response.data;
        },
        onSuccess: (run) => setLastRun(run),
    });

    const stopRun = useMutation({
        mutationFn: async () => {
            if (!lastRun?.id) {
                throw new Error('No ONT instrument run selected');
            }
            if (lastRun.fake_or_demo_devices) {
                return { ...lastRun, status: 'test_mode_stopped' } satisfies OntInstrumentRun;
            }
            const response = await stopOntInstrumentRun(lastRun.id, { confirm_stop: true });
            return response.data;
        },
        onSuccess: (run) => setLastRun(run),
    });

    const canStart = Boolean(selectedDevice?.available_for_run && kit.trim() && sampleId.trim() && !startRun.isPending);
    const blockers = protocolOptions.data?.blockers ?? [];
    const selectedOutputDirs = selectedDevice?.output_directories ?? protocolOptions.data?.output_directories ?? {};

    return (
        <section className="space-y-5 rounded-2xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-5 shadow-lg shadow-black/10">
            <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-xl font-semibold text-[var(--text-primary)]">ONT instrument control</h2>
                        <span className="rounded-full border border-cyan-500/40 bg-cyan-500/10 px-2 py-1 text-xs font-semibold uppercase tracking-wide text-cyan-100">
                            Mk1D / MinKNOW
                        </span>

                    </div>
                    <p className="max-w-2xl text-sm text-[var(--text-secondary)]">
                        Select a MinKNOW position, confirm run metadata, then start acquisition. Real starts remain disabled until a real available position is present.
                    </p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <button
                        type="button"
                        onClick={onAnalyzeExistingData}
                        className="rounded-lg border border-[var(--border-primary)] px-3 py-2 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]"
                    >
                        Analyze existing data
                    </button>

                </div>
            </div>

            <div className="grid gap-3 md:grid-cols-4">
                <div className={`rounded-xl border p-3 ${statusTone(data?.implementation_status)}`}>
                    <div className="text-xs uppercase tracking-wide opacity-70">MinKNOW link</div>
                    <div className="mt-1 text-base font-semibold">{isLoading ? 'Checking MinKNOW…' : minKnowStatus}</div>
                </div>
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3">
                    <div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Positions</div>
                    <div className="mt-1 text-base font-semibold text-[var(--text-primary)]">{devices.length}</div>
                </div>
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3">
                    <div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Available</div>
                    <div className="mt-1 text-base font-semibold text-[var(--text-primary)]">{availableDevices.length}</div>
                </div>
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3">
                    <div className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Selected</div>
                    <div className="mt-1 truncate text-base font-semibold text-[var(--text-primary)]">{selectedDevice?.position ?? 'none'}</div>
                </div>
            </div>


            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
                <div className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                    <div className="flex items-center justify-between gap-3">
                        <div>
                            <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Instrument positions</h3>
                            <p className="text-xs text-[var(--text-secondary)]">Live Mk1D cards populate directly from MinKNOW.</p>
                        </div>
                        <button type="button" onClick={() => void refetch()} className="text-sm text-[var(--accent-secondary)]">
                            Refresh
                        </button>
                    </div>
                    {data?.message ? <p className="rounded-lg bg-slate-900/40 p-2 text-xs text-[var(--text-secondary)]">{data.message}</p> : null}
                    <div className="grid gap-3 md:grid-cols-2">
                        {devices.length === 0 ? (
                            <div className="rounded-lg border border-dashed border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 text-sm text-[var(--text-secondary)]">
                                No live Mk1D positions reported by MinKNOW.
                            </div>
                        ) : devices.map((device) => {
                            const isSelected = selectedDevice?.position === device.position;
                            return (
                                <button
                                    key={device.position}
                                    type="button"
                                    onClick={() => setSelectedPosition(device.position)}
                                    className={`rounded-xl border p-4 text-left transition ${isSelected ? 'border-cyan-400 bg-cyan-500/10' : 'border-[var(--border-primary)] bg-[var(--bg-secondary)] hover:border-cyan-500/50'}`}
                                >
                                    <div className="flex items-start justify-between gap-3">
                                        <div>
                                            <div className="text-base font-semibold text-[var(--text-primary)]">{device.position}</div>
                                            <div className="text-xs text-[var(--text-secondary)]">{device.device_type || 'unknown device'} · {deviceStateLabel(device)}</div>
                                        </div>
                                        {device.fake_or_demo_device ? (
                                            <span className="rounded-full border border-amber-500/50 bg-amber-500/10 px-2 py-1 text-[10px] font-bold text-amber-100">FAKE TEST CONNECTION</span>
                                        ) : null}
                                    </div>
                                    <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-[var(--text-secondary)]">
                                        <div>Flow cell: {device.flow_cell?.present ? 'present' : 'absent'}</div>
                                        <div>Config cell: {device.flow_cell?.is_ctc ? 'yes' : 'no/unknown'}</div>
                                        <div>Product: {device.flow_cell?.product_code || device.flow_cell?.user_specified_product_code || 'unknown'}</div>
                                        <div>Sample rate: {device.flow_cell?.sample_rate ?? 'unknown'}</div>
                                        <div>Channels: {device.flow_cell?.channel_count ?? device.device_info?.max_channel_count ?? 'unknown'}</div>
                                        <div>Device state: {String(device.device_state?.device_state ?? device.state ?? 'unknown')}</div>
                                        <div>Running: {device.running ? 'yes' : 'no'}</div>
                                        <div>Connector: {String(device.device_state?.flow_cell_connector ?? 'unknown')}</div>
                                    </div>
                                    {device.connection_error ? <div className="mt-2 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-100">{device.connection_error}</div> : null}
                                </button>
                            );
                        })}
                    </div>
                </div>

                <div className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                    <div>
                        <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Run setup</h3>
                        <p className="text-xs text-[var(--text-secondary)]">These values are sent with real starts and mirrored by fake starts.</p>
                    </div>
                    {selectedDevice ? (
                        <div className="space-y-2 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs text-[var(--text-secondary)]">
                            <div className="font-semibold uppercase tracking-wide text-[var(--text-primary)]">Selected position truth</div>
                            <div>Position: {selectedDevice.position}</div>
                            <div>Flow-cell present: {selectedDevice.flow_cell?.present ? 'yes' : 'no'}</div>
                            <div>Configuration test cell flag: {selectedDevice.flow_cell?.is_ctc ? 'yes' : 'no/unknown'}</div>
                            <div>Acquisition: {String(selectedDevice.acquisition_status?.status ?? 'unknown')}</div>
                            <div>Current protocol: {selectedDevice.current_protocol ? JSON.stringify(selectedDevice.current_protocol) : 'none reported'}</div>
                            <div>Hardware checks in API history: {selectedDevice.hardware_check_runs?.length ?? 0}</div>
                            <div>Protocol runs in API history: {selectedDevice.protocol_runs?.length ?? 0}</div>
                            <div>Output reads dir: {selectedOutputDirs.reads || 'not reported'}</div>
                            {hardwareCheckMessage ? <div className="text-cyan-100">Hardware check: {hardwareCheckMessage}</div> : null}
                            {!selectedDevice.flow_cell?.present ? <div className="text-amber-100">Hardware check requires MinKNOW to report a present flow cell/test cell.</div> : null}
                            {blockers.length ? <div className="text-amber-100">Preflight blockers: {blockers.join(', ')}</div> : null}
                            <div className="flex flex-wrap gap-2 pt-1">
                                <button
                                    type="button"
                                    disabled={!selectedPositionForQuery || !selectedDevice?.flow_cell?.present || beginHardwareCheck.isPending}
                                    onClick={() => beginHardwareCheck.mutate()}
                                    className="rounded border border-emerald-500/40 px-2 py-1 text-emerald-100 disabled:opacity-50"
                                >
                                    Run hardware check
                                </button>
                                <button
                                    type="button"
                                    disabled={!selectedPositionForQuery || refreshPosition.isPending}
                                    onClick={() => refreshPosition.mutate()}
                                    className="rounded border border-cyan-500/40 px-2 py-1 text-cyan-100 disabled:opacity-50"
                                >
                                    Refresh/reconnect position
                                </button>
                                <button
                                    type="button"
                                    disabled
                                    title="True Mk1D restart/power-cycle is not enabled until MinKNOW restart semantics are live-validated."
                                    className="rounded border border-[var(--border-primary)] px-2 py-1 opacity-50"
                                >
                                    Restart instrument unavailable
                                </button>
                            </div>
                        </div>
                    ) : null}
                    <label className="block text-xs font-semibold text-[var(--text-secondary)]">
                        Sample ID
                        <input value={sampleId} onChange={(event) => setSampleId(event.target.value)} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)]" />
                    </label>
                    <label className="block text-xs font-semibold text-[var(--text-secondary)]">
                        Experiment group
                        <input value={experimentGroup} onChange={(event) => setExperimentGroup(event.target.value)} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)]" />
                    </label>
                    <div className="grid grid-cols-2 gap-3">
                        <label className="block text-xs font-semibold text-[var(--text-secondary)]">
                            Kit
                            <select value={kit} onChange={(event) => setKit(event.target.value)} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)]">
                                <option value="SQK-LSK114">SQK-LSK114</option>
                                <option value="SQK-RBK114.24">SQK-RBK114.24</option>
                                <option value="SQK-NBD114.24">SQK-NBD114.24</option>
                            </select>
                        </label>
                        <label className="block text-xs font-semibold text-[var(--text-secondary)]">
                            Basecaller
                            <select value={qualityMode} onChange={(event) => setQualityMode(event.target.value)} className="mt-1 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-primary)]">
                                <option value="sup">SUP</option>
                                <option value="hac">HAC</option>
                                <option value="fast">FAST</option>
                            </select>
                        </label>
                    </div>
                    <div className="space-y-2 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3">
                        <div className="text-xs font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Outputs</div>
                        {OUTPUT_LABELS.map(([key, label]) => (
                            <label key={key} className="flex items-center justify-between gap-3 text-sm text-[var(--text-primary)]">
                                <span>{label}</span>
                                <input
                                    type="checkbox"
                                    checked={outputs[key]}
                                    onChange={(event) => setOutputs((current) => ({ ...current, [key]: event.target.checked }))}
                                />
                            </label>
                        ))}
                    </div>
                    <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs text-[var(--text-secondary)]">
                        Start packet: {selectedDevice?.position ?? 'no position'} · {kit || 'kit missing'} · {visibleOutputLabels.length ? visibleOutputLabels.join(', ') : 'no outputs selected'}
                        {!canStart && !selectedIsTestMode ? <div className="mt-2 text-amber-100">Real start disabled until MinKNOW reports a present sequencing flow cell, idle position, kit/model, and output directory.</div> : null}
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <button
                            type="button"
                            disabled={!canStart}
                            onClick={() => startRun.mutate()}
                            className="rounded-lg bg-[var(--accent-secondary)] px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                            {selectedIsTestMode ? 'Start fake test run' : 'Start instrument run'}
                        </button>
                        <button
                            type="button"
                            disabled={!lastRun || stopRun.isPending}
                            onClick={() => stopRun.mutate()}
                            className="rounded-lg border border-[var(--border-primary)] px-4 py-2 text-sm text-[var(--text-primary)] disabled:opacity-50"
                        >
                            Stop run
                        </button>
                    </div>
                </div>
            </div>

            {lastRun ? (
                <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4 text-sm text-[var(--text-secondary)]">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                            <div className="font-semibold text-[var(--text-primary)]">Run {lastRun.id}</div>
                            <div>{lastRun.position} · {lastRun.status} · MinKNOW run {lastRun.minknow_run_id}</div>
                        </div>
                        {lastRun.fake_or_demo_devices ? <span className="rounded-full bg-amber-500/10 px-2 py-1 text-xs font-semibold text-amber-100">fake/test run</span> : null}
                    </div>
                </div>
            ) : null}
        </section>
    );
}
