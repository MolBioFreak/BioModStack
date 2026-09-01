import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { fetchExecutionTargets } from '../../lib/api';
import { InfraLiveTelemetry } from '../InfraLiveTelemetry';
import { RemoteGpuTelemetry } from '../RemoteGpuTelemetry';

const DASHBOARD_TELEMETRY_COMPACT_KEY = 'bms_dashboard_telemetry_compact_v1';
type TelemetryPanelSize = 'micro' | 'compact' | 'standard' | 'large' | 'xlarge';
type TelemetryScope = 'local' | 'vast' | 'combined';
const TELEMETRY_SIZE_OPTIONS: ReadonlyArray<{ value: TelemetryPanelSize; label: string; title: string }> = [
    { value: 'micro', label: 'XS', title: 'Very compact telemetry' },
    { value: 'compact', label: 'S', title: 'Compact telemetry' },
    { value: 'standard', label: 'M', title: 'Standard telemetry' },
    { value: 'large', label: 'L', title: 'Large telemetry' },
    { value: 'xlarge', label: 'XL', title: 'Maximum telemetry size' },
];

const readTelemetrySizePreference = (): TelemetryPanelSize => {
    try {
        const stored = localStorage.getItem(DASHBOARD_TELEMETRY_COMPACT_KEY);
        if (stored === 'true') return 'compact';
        if (stored === 'false' || stored == null) return 'standard';
        if (TELEMETRY_SIZE_OPTIONS.some((option) => option.value === stored)) {
            return stored as TelemetryPanelSize;
        }
    } catch {
        // Fall back to the default below.
    }
    return 'standard';
};

export function DashboardTelemetry() {
    const [telemetrySize, setTelemetrySize] = useState<TelemetryPanelSize>('standard');
    const [scope, setScope] = useState<TelemetryScope>('local');
    const targetsQuery = useQuery({
        queryKey: ['execution-targets'],
        queryFn: fetchExecutionTargets,
        refetchInterval: 5_000,
        retry: false,
    });
    const activeVastTarget = targetsQuery.isError
        ? undefined
        : targetsQuery.data?.data.find((target) => target.active && target.state === 'ready');
    const activeVastLabel = activeVastTarget?.name?.trim() || (
        activeVastTarget ? `Instance ${activeVastTarget.provider_instance_id}` : 'Active Vast'
    );

    useEffect(() => {
        setTelemetrySize(readTelemetrySizePreference());
    }, []);

    useEffect(() => {
        if (!activeVastTarget && scope !== 'local') setScope('local');
    }, [activeVastTarget, scope]);

    const setTelemetrySizePreference = (nextSize: TelemetryPanelSize) => {
        setTelemetrySize(nextSize);
        try {
            localStorage.setItem(DASHBOARD_TELEMETRY_COMPACT_KEY, nextSize);
        } catch {
            // Ignore localStorage write failures and keep in-memory state.
        }
    };

    const localTelemetry = (
        <InfraLiveTelemetry
            showXAxisLabels={false}
            defaultPollIntervalMs={1000}
            defaultWindowMinutes={3}
            variant="dashboard"
            dashboardSize={telemetrySize}
        />
    );

    return (
        <section className="mb-6 rounded-3xl border border-[var(--border-primary)] bg-[var(--bg-secondary)]/78 p-4 shadow-2xl shadow-black/10">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <div
                    className="inline-flex flex-wrap items-center gap-1 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-1"
                    role="tablist"
                    aria-label="Telemetry source"
                >
                    <button
                        type="button"
                        role="tab"
                        aria-selected={scope === 'local'}
                        onClick={() => setScope('local')}
                        className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors ${scope === 'local'
                            ? 'border-[var(--accent-primary)] text-[var(--text-primary)]'
                            : 'border-transparent text-[var(--text-secondary)] hover:bg-[var(--card-hover)]'}`}
                    >
                        Local
                    </button>
                    {activeVastTarget && (
                        <button
                            type="button"
                            role="tab"
                            aria-selected={scope === 'vast'}
                            onClick={() => setScope('vast')}
                            className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors ${scope === 'vast'
                                ? 'border-[var(--success)] text-[var(--text-primary)]'
                                : 'border-transparent text-[var(--text-secondary)] hover:bg-[var(--card-hover)]'}`}
                        >
                            Vast · {activeVastLabel}
                        </button>
                    )}
                    {activeVastTarget && (
                        <button
                            type="button"
                            role="tab"
                            aria-selected={scope === 'combined'}
                            onClick={() => setScope('combined')}
                            className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors ${scope === 'combined'
                                ? 'border-[var(--accent-secondary)] text-[var(--text-primary)]'
                                : 'border-transparent text-[var(--text-secondary)] hover:bg-[var(--card-hover)]'}`}
                        >
                            Combined
                        </button>
                    )}
                </div>

                {scope !== 'vast' && (
                    <div className="flex items-center gap-2">
                        <span className="text-[11px] uppercase tracking-[0.16em] text-[var(--text-muted)]">
                            Panel Size
                        </span>
                        <div className="inline-flex flex-wrap items-center gap-1 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-1">
                            {TELEMETRY_SIZE_OPTIONS.map((option) => {
                                const active = telemetrySize === option.value;
                                return (
                                    <button
                                        key={option.value}
                                        type="button"
                                        onClick={() => setTelemetrySizePreference(option.value)}
                                        className={`min-w-9 rounded-lg border px-2.5 py-1.5 text-[11px] font-semibold transition-colors ${
                                            active
                                                ? 'border-[var(--accent-primary)] text-[var(--text-primary)]'
                                                : 'border-[var(--border-primary)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:bg-[var(--card-hover)]'
                                        }`}
                                        style={active ? {
                                            backgroundColor: 'color-mix(in srgb, var(--accent-primary) 18%, var(--bg-tertiary))',
                                        } : undefined}
                                        title={option.title}
                                    >
                                        {option.label}
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                )}
            </div>

            {scope === 'local' && localTelemetry}
            {scope === 'vast' && <RemoteGpuTelemetry />}
            {scope === 'combined' && activeVastTarget && (
                <div className="space-y-5" data-bms-telemetry-combined="true">
                    <section className="space-y-3">
                        <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--text-muted)]">
                            Local workstation
                        </h2>
                        {localTelemetry}
                    </section>
                    <section className="space-y-3 border-t border-[var(--border-primary)] pt-5">
                        <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--text-muted)]">
                            Vast · {activeVastLabel}
                        </h2>
                        <RemoteGpuTelemetry />
                    </section>
                </div>
            )}
        </section>
    );
}

export default DashboardTelemetry;
