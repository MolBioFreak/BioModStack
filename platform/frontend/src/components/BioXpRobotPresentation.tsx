import { useState, type ReactNode } from 'react';
import type { BioXpOperatorDashboard } from '../lib/bioxpClient';

type AxisTelemetry = BioXpOperatorDashboard['axes'][number];

export function BioXpReference({ reference }: { reference: string | undefined }) {
    const label = reference ?? 'not reported';
    const state = label === 'referenced' || label === 'homed' ? 'referenced' : label === 'unknown' || label === 'not reported' ? 'unknown' : 'unreferenced';
    return <span className="bx-reference" title={label} data-reference={state}>
        <i aria-hidden="true" />{label}
    </span>;
}

/** A draft editor only. Unknown display bounds never change native action availability. */
export function BioXpStepSlider({ label, value, min, max, onChange }: {
    label: string; value: number; min: number | null | undefined; max: number | null | undefined;
    onChange: (value: number) => void;
}) {
    if (typeof min !== 'number' || typeof max !== 'number' || !Number.isFinite(min) || !Number.isFinite(max) || max <= min) return null;
    // Only the thumb is bounded. Exact numeric drafts (including a deliberate clear)
    // remain with the cockpit; rendering/refetching never rewrites or submits them.
    const thumb = Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : min;
    return <input className="bx-step-slider" type="range" aria-label={label}
        min={min} max={max} step={1} value={thumb}
        aria-valuetext={Number.isFinite(value) ? `${value} steps` : 'Not set'}
        title={`${min.toLocaleString()}–${max.toLocaleString()} steps`}
        onChange={event => onChange(event.target.valueAsNumber)} />;
}

/** Relative ranges are hints from OEM inner margins, never request validation. */
export function repairManualJogDistances(position: unknown, min: unknown, max: unknown) {
    if (![position, min, max].every(v => typeof v === 'number' && Number.isFinite(v))) return null;
    return { negative: Math.max(0, (position as number) - ((min as number) + 20)),
        positive: Math.max(0, (max as number) - 20 - (position as number)) };
}

export function BioXpStepPresets({ axis, onChange, distances }: {
    axis: string; onChange: (value: number) => void;
    distances: ReturnType<typeof repairManualJogDistances>;
}) {
    return <div className="bx-step-presets"><span>Jog by · steps</span>
        {[1000, 5000, 10000, 25000].map(value => <button key={value} type="button"
            aria-label={`${axis} jog ${value} steps`} onClick={() => onChange(value)}>{value / 1000}k</button>)}
        <small>Useful − / +: {distances ? `${distances.negative} / ${distances.positive} steps` : 'Not reported'} (display hint)</small>
    </div>;
}

export function BioXpAxisTelemetry({ axis }: { axis: AxisTelemetry | undefined }) {
    if (!axis) return <p>Axis telemetry not reported.</p>;
    const switchLabel = (value: boolean | null | undefined) => value === true ? 'Yes' : value === false ? 'No' : 'Not reported';
    return <dl className="bx-telemetry">
        <div><dt>Position reply</dt><dd>{axis.position_reply_valid === true ? 'Valid' : axis.position_reply_valid === false ? 'Invalid' : 'Unknown'}</dd></div>
        <div><dt>Position source / sample time</dt><dd>{axis.position_source ?? 'Not reported'} / {axis.position_observed_at == null ? 'Not reported' : new Date(axis.position_observed_at * 1000).toISOString()}</dd></div>
        <div><dt>Published limits (steps)</dt><dd>{axis.min_steps ?? 'Not reported'} … {axis.max_steps ?? 'Not reported'}</dd></div>
        <div><dt>Speed</dt><dd>{axis.speed_steps_s ?? '—'} steps/s</dd></div>
        <div><dt>Run / standby current</dt><dd>{axis.run_current ?? '—'} / {axis.standby_current ?? '—'}</dd></div>
        <div><dt>Limits L / R</dt><dd>{switchLabel(axis.left_switch_active)} / {switchLabel(axis.right_switch_active)}</dd></div>
        <div><dt>Motor temperature</dt><dd>{axis.motor_temperature_available ? `${axis.motor_temperature_c ?? '—'} °C` : 'Not reported'}</dd></div>
    </dl>;
}

/** Presentation only: all action elements and evidence retain their cockpit owner. */
export function BioXpAxisRow({ axis, label, testId, reference, position, controls, relative, absolute, stop, extras, details, notices }: {
    axis: string; label: string; testId?: string;
    reference: string | undefined; position: string | number | null | undefined;
    controls: ReactNode; relative?: ReactNode; absolute?: ReactNode;
    stop: ReactNode; extras?: ReactNode; details: ReactNode; notices: ReactNode;
}) {
    const [detailsOpen, setDetailsOpen] = useState(false);
    return <article className="bx-axis" data-axis={axis} data-testid={testId}>
        <div className="bx-axis-main">
            <div className="bx-axis-meta">
                <h3>{label}</h3>
                <div className="bx-reference-cell"><BioXpReference reference={reference} /></div>
                <div className="bx-position">{position === 'unknown' ? '—' : position ?? '—'}<small> steps</small></div>
            </div>
            {controls}
            {relative && <div className="bx-relative-cell" data-label="Move by">{relative}</div>}
            {absolute && <div className="bx-absolute-cell" data-label="Go to">{absolute}</div>}
            <div className="bx-row-stop">{stop}</div>
            <button type="button" className="bx-details-toggle" aria-label={`${label} status and evidence`} title="Axis status and evidence" aria-expanded={detailsOpen} aria-controls={`bioxp-axis-${axis}-details`} onClick={() => setDetailsOpen(open => !open)}>⋯</button>
            <div className="bx-row-extras">{extras}</div>
            <div id={`bioxp-axis-${axis}-details`} className="bx-axis-details" hidden={!detailsOpen}>{details}</div>
        </div>
        <div className="bx-notices">{notices}</div>
    </article>;
}
