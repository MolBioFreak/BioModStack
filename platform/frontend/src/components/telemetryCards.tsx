import type { ReactNode } from 'react';

/** Shared look for local and remote hardware cards: one card per device, metric strips inside. */
export function PanelFrame({
    title,
    subtitle,
    aside,
    children,
    compact = false,
}: {
    title: string;
    subtitle?: string;
    aside?: ReactNode;
    children: ReactNode;
    compact?: boolean;
}) {
    return (
        <section className={`min-w-0 rounded-2xl border border-[var(--border-primary)] bg-[var(--surface-card,var(--bg-secondary))] shadow-[var(--shadow-card)] ${compact ? 'p-3' : 'p-4'}`}>
            <div className={`flex items-start justify-between gap-3 ${compact ? 'mb-2' : 'mb-4'}`}>
                <div className="min-w-0">
                    <h2 className={`truncate font-semibold text-[var(--text-primary)] ${compact ? 'text-base' : 'text-lg'}`}>{title}</h2>
                    {subtitle ? <p className="mt-0.5 text-xs text-[var(--text-muted)]">{subtitle}</p> : null}
                </div>
                {aside}
            </div>
            {children}
        </section>
    );
}

export const PLOT_TICK = 'var(--chart-axis, var(--text-muted))';
// One colour per metric family, on every telemetry card.
export const METRIC_COLORS = {
    utilization: 'var(--success)',
    memory: 'var(--link)',
    power: 'var(--warning)',
    temperature: '#f472b6',
    secondary: 'var(--accent-primary)',
} as const;

export function legendName(label: string, value: string): string {
    return `${label} ${value}`;
}

export function formatBinaryBytes(bytes: number | null | undefined): string {
    if (bytes == null || !Number.isFinite(bytes)) return 'n/a';
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let value = Math.max(0, bytes);
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }
    return `${value >= 100 || unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

export function formatDuration(ms: number): string {
    const minutes = Math.max(0, Math.floor(ms / 60_000));
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours}h ${minutes % 60}m`;
    return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

export function SegmentedControl<T extends string | number>({
    label,
    value,
    options,
    onChange,
    compact = false,
}: {
    label: string;
    value: T;
    options: ReadonlyArray<{ value: T; label: string }>;
    onChange: (value: T) => void;
    compact?: boolean;
}) {
    return (
        <div>
            <div
                className={`font-semibold uppercase text-[var(--text-muted)] ${compact ? 'mb-1 text-[9px]' : 'mb-2 text-xs'}`}
                style={{ letterSpacing: '0.16em' }}
            >
                {label}
            </div>
            <div className={`inline-flex border border-[var(--border-primary)] bg-[var(--surface-control,var(--bg-primary))] ${compact ? 'rounded-lg p-0.5' : 'rounded-xl p-1'}`}>
                {options.map((option) => {
                    const active = option.value === value;
                    return (
                        <button
                            key={String(option.value)}
                            type="button"
                            onClick={() => onChange(option.value)}
                            className={`border font-medium transition-colors ${
                                compact ? 'rounded-md px-2 py-1 text-[10px]' : 'rounded-lg px-3 py-2 text-sm'
                            } ${
                                active
                                    ? 'text-[var(--accent-primary)]'
                                    : 'border-transparent text-[var(--text-secondary)] hover:bg-[var(--surface-control-strong,var(--bg-tertiary))] hover:text-[var(--text-primary)]'
                            }`}
                            style={active ? {
                                backgroundColor: 'color-mix(in srgb, var(--accent-primary) 14%, var(--bg-tertiary))',
                                borderColor: 'color-mix(in srgb, var(--accent-primary) 34%, var(--border-primary))',
                            } : undefined}
                        >
                            {option.label}
                        </button>
                    );
                })}
            </div>
        </div>
    );
}
