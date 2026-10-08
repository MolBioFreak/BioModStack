import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
    fetchOntDeviceStatus,
    startOntInstrumentRun,
    stopOntInstrumentRun,
    type OntInstrumentRun,
    type OntLiveDevice,
} from '../../lib/api';

interface OntInstrumentPanelProps {
    onAnalyzeExistingData: () => void;
}

type OutputKey = 'pod5' | 'fastq' | 'bam';

const TEST_MODE_MK1D_DEVICE: OntLiveDevice = {
    position: 'TEST-MK1D',
    device_type: 'mk1d',
    state: 'test_mode_connected',
    running: false,
    available_for_run: true,
    fake_or_demo_device: true,
    flow_cell: {
        present: true,
        flow_cell_id: 'FAKE-FLOWCELL',
        product_code: 'FLO-MIN114',
        sample_rate: 5000,
    },
};

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
    const [testModeEnabled, setTestModeEnabled] = useState<boolean>(false);
    const [sampleId, setSampleId] = useState<string>('plasmid-qc-test');
    const [experimentGroup, setExperimentGroup] = useState<string>('bms_plasmid_verification');
    const [kit, setKit] = useState<string>('SQK-LSK114');
    const [qualityMode, setQualityMode] = useState<string>('sup');
    const [outputs, setOutputs] = useState<Record<OutputKey, boolean>>(DEFAULT_OUTPUTS);
    const { data, isLoading, refetch } = useQuery({
        queryKey: ['ont-device-status'],
        queryFn: async () => (await fetchOntDeviceStatus()).data,
        refetchInterval: 10000,
    });
    const liveDevices = data?.live_devices ?? [];
    const devices = testModeEnabled ? [TEST_MODE_MK1D_DEVICE, ...liveDevices] : liveDevices;
    const availableDevices = useMemo(
        () => devices.filter((device) => device.available_for_run && device.position),
        [devices],
    );
    const selectedDevice = availableDevices.find((device) => device.position === selectedPosition) ?? availableDevices[0];
    const selectedIsTestMode = Boolean(selectedDevice?.fake_or_demo_device);
    const minKnowStatus = isLoading ? 'checking' : statusLabel(data?.implementation_status);
    const visibleOutputLabels = OUTPUT_LABELS.filter(([key]) => outputs[key]).map(([, label]) => label);

    const startRun = useMutation({
        mutationFn: async () => {
            if (!selectedDevice?.position) {
                throw new Error('No real available ONT position selected');
            }
            if (selectedDevice.fake_or_demo_device) {
                return {
                    id: `test-ont-run-${Date.now()}`,
                    minknow_run_id: 'fake-minknow-run-test-mode',
                    position: selectedDevice.position,
                    status: 'test_mode_running',
                    handoff_ready: false,
                    output_files: { fastq: [], pod5: [], bam: [] },
                    fake_or_demo_devices: true,
                } satisfies OntInstrumentRun;
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

    return (
        <section className="space-y-5 rounded-2xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-5 shadow-lg shadow-black/10">
            <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-xl font-semibold text-[var(--text-primary)]">ONT instrument control</h2>
                        <span className="rounded-full border border-cyan-500/40 bg-cyan-500/10 px-2 py-1 text-xs font-semibold uppercase tracking-wide text-cyan-100">
                            Mk1D / MinKNOW
                        </span>
                        {testModeEnabled ? (
                            <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs font-semibold uppercase tracking-wide text-amber-100">
                                test mode
                            </span>
                        ) : null}
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
                    <button
                        type="button"
                        onClick={() => {
                            setTestModeEnabled((value) => !value);
                            setSelectedPosition('TEST-MK1D');
                        }}
                        className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm font-semibold text-amber-100 hover:bg-amber-500/20"
                    >
                        {testModeEnabled ? 'Hide fake Mk1D' : 'Test mode: fake Mk1D'}
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

            {testModeEnabled ? (
                <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
                    Test mode is local UI simulation only; it does not prove MinKNOW connectivity or start a real instrument run.
                </div>
            ) : null}

            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
                <div className="space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-4">
                    <div className="flex items-center justify-between gap-3">
                        <div>
                            <h3 className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Instrument positions</h3>
                            <p className="text-xs text-[var(--text-secondary)]">Live cards populate from MinKNOW; fake Mk1D is visibly labeled.</p>
                        </div>
                        <button type="button" onClick={() => void refetch()} className="text-sm text-[var(--accent-secondary)]">
                            Refresh
                        </button>
                    </div>
                    {data?.message ? <p className="rounded-lg bg-slate-900/40 p-2 text-xs text-[var(--text-secondary)]">{data.message}</p> : null}
                    <div className="grid gap-3 md:grid-cols-2">
                        {devices.length === 0 ? (
                            <div className="rounded-lg border border-dashed border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4 text-sm text-[var(--text-secondary)]">
                                No MinKNOW positions reported. Use test mode to exercise the Mk1D UI without hardware.
                            </div>
                        ) : devices.map((device) => {
                            const isSelected = selectedDevice?.position === device.position;
                            return (
                                <button
                                    key={device.position}
                                    type="button"
                                    disabled={!device.available_for_run}
                                    onClick={() => setSelectedPosition(device.position)}
                                    className={`rounded-xl border p-4 text-left transition disabled:opacity-60 ${isSelected ? 'border-cyan-400 bg-cyan-500/10' : 'border-[var(--border-primary)] bg-[var(--bg-secondary)] hover:border-cyan-500/50'}`}
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
                                        <div>Product: {device.flow_cell?.product_code || 'unknown'}</div>
                                        <div>Sample rate: {device.flow_cell?.sample_rate ?? 'unknown'}</div>
                                        <div>Running: {device.running ? 'yes' : 'no'}</div>
                                    </div>
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
