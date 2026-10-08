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

export function OntInstrumentPanel({ onAnalyzeExistingData }: OntInstrumentPanelProps) {
    const [selectedPosition, setSelectedPosition] = useState<string>('');
    const [lastRun, setLastRun] = useState<OntInstrumentRun | null>(null);
    const [testModeEnabled, setTestModeEnabled] = useState<boolean>(false);
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
                kit: 'SQK-LSK114',
                experiment_group: 'bms_plasmid_verification',
                outputs: { pod5: true, fastq: true, bam: false },
                basecalling: { enabled: true, quality_mode: 'sup', modified_bases: 'none' },
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

    const canStart = Boolean(selectedDevice?.available_for_run && !startRun.isPending);
    const selectedIsTestMode = Boolean(selectedDevice?.fake_or_demo_device);

    return (
        <section className="space-y-4 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h2 className="text-xl font-semibold text-[var(--text-primary)]">Start instrument run</h2>
                    <p className="text-sm text-[var(--text-secondary)]">
                        No instrument run button is enabled without a real available position.
                    </p>
                </div>
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

            {testModeEnabled ? (
                <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
                    Test mode is local UI simulation only; it does not prove MinKNOW connectivity or start a real instrument run.
                </div>
            ) : null}

            <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3">
                <div className="flex items-center justify-between gap-3">
                    <span className="text-sm text-[var(--text-secondary)]">Device-control status</span>
                    <button type="button" onClick={() => void refetch()} className="text-sm text-[var(--accent-secondary)]">
                        Refresh
                    </button>
                </div>
                <p className="mt-1 text-lg font-semibold text-[var(--text-primary)]">
                    {isLoading ? 'Checking MinKNOW…' : statusLabel(data?.implementation_status)}
                </p>
                {data?.message ? <p className="mt-1 text-xs text-[var(--text-secondary)]">{data.message}</p> : null}
            </div>

            <div className="grid gap-3 md:grid-cols-2">
                {devices.length === 0 ? (
                    <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3 text-sm text-[var(--text-secondary)]">
                        no devices
                    </div>
                ) : devices.map((device) => (
                    <button
                        key={device.position}
                        type="button"
                        disabled={!device.available_for_run}
                        onClick={() => setSelectedPosition(device.position)}
                        className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3 text-left disabled:opacity-60"
                    >
                        <div className="text-sm font-semibold text-[var(--text-primary)]">{device.position}</div>
                        <div className="text-xs text-[var(--text-secondary)]">{device.device_type || 'unknown device'} · {deviceStateLabel(device)}</div>
                        {device.fake_or_demo_device ? (
                            <div className="mt-2 text-xs font-semibold text-amber-100">FAKE TEST CONNECTION</div>
                        ) : null}
                    </button>
                ))}
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

            {lastRun ? (
                <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3 text-sm text-[var(--text-secondary)]">
                    Run {lastRun.id}: {lastRun.status}
                </div>
            ) : null}
        </section>
    );
}
