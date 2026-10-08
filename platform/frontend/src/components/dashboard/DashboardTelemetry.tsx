import { useEffect, useState } from 'react';
import { InfraLiveTelemetry } from '../InfraLiveTelemetry';

const DASHBOARD_TELEMETRY_COMPACT_KEY = 'bms_dashboard_telemetry_compact_v1';
type TelemetryPanelSize = 'micro' | 'compact' | 'standard' | 'large' | 'xlarge';
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

    useEffect(() => {
        setTelemetrySize(readTelemetrySizePreference());
    }, []);

    const setTelemetrySizePreference = (nextSize: TelemetryPanelSize) => {
        setTelemetrySize(nextSize);
        try {
            localStorage.setItem(DASHBOARD_TELEMETRY_COMPACT_KEY, nextSize);
        } catch {
            // Ignore localStorage write failures and keep in-memory state.
        }
    };

    return (
        <section className="mb-6 rounded-3xl border border-[var(--border-primary)] bg-[var(--bg-secondary)]/78 p-4 shadow-2xl shadow-black/10">
            <div className="mb-3 flex items-center justify-end gap-2">
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
            <InfraLiveTelemetry
                showXAxisLabels={false}
                defaultPollIntervalMs={1000}
                defaultWindowMinutes={3}
                variant="dashboard"
                dashboardSize={telemetrySize}
            />
        </section>
    );
}

export default DashboardTelemetry;
