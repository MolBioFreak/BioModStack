import { useState, type ReactNode } from 'react';
import type { BioXpOperatorDashboard } from '../lib/bioxpClient';

type AxisTelemetry = BioXpOperatorDashboard['axes'][number];

export function BioXpReference({ reference, dotOnly = false }: { reference: string | undefined; dotOnly?: boolean }) {
    const label = reference ?? 'not reported';
    const state = label === 'referenced' || label === 'homed' ? 'referenced' : label === 'unknown' || label === 'not reported' ? 'unknown' : 'unreferenced';
    return <span className={dotOnly ? 'bx-reference-dot' : 'bx-reference'} title={label} data-reference={state}>
        <i aria-hidden="true" />{!dotOnly && label}
    </span>;
}

export function BioXpAxisTelemetry({ axis }: { axis: AxisTelemetry | undefined }) {
    if (!axis) return <p>Axis telemetry not reported.</p>;
    const switchLabel = (value: boolean | null | undefined) => value === true ? 'Yes' : value === false ? 'No' : 'Not reported';
    return <dl className="bx-telemetry">
        <div><dt>Speed</dt><dd>{axis.speed_steps_s ?? '—'} steps/s</dd></div>
        <div><dt>Run / standby current</dt><dd>{axis.run_current ?? '—'} / {axis.standby_current ?? '—'}</dd></div>
        <div><dt>Limits L / R</dt><dd>{switchLabel(axis.left_switch_active)} / {switchLabel(axis.right_switch_active)}</dd></div>
        <div><dt>Motor temperature</dt><dd>{axis.motor_temperature_available ? `${axis.motor_temperature_c ?? '—'} °C` : 'Not reported'}</dd></div>
    </dl>;
}

/** Presentation only: all action elements and evidence retain their cockpit owner. */
export function BioXpAxisRow({ axis, label, testId, selected, reference, position, controls, relative, presets, absolute, stop, extras, details, notices }: {
    axis: string; label: string; testId?: string; selected: boolean;
    reference: string | undefined; position: string | number | null | undefined;
    controls: ReactNode; relative?: ReactNode; presets?: ReactNode; absolute?: ReactNode;
    stop: ReactNode; extras?: ReactNode; details: ReactNode; notices: ReactNode;
}) {
    const [detailsOpen, setDetailsOpen] = useState(false);
    return <article className="bx-axis" data-axis={axis} data-testid={testId}>
        <div className={`bx-axis-main ${selected ? 'is-selected' : ''}`}>
            <h3>{label}</h3>
            <div className="bx-reference-cell"><BioXpReference reference={reference} /></div>
            <div className="bx-position">{position === 'unknown' ? '—' : position ?? '—'}<small> steps</small></div>
            {controls}
            <div className="bx-relative-cell">{relative}</div>
            <div className="bx-preset-cell">{presets}</div>
            <div className="bx-absolute-cell">{absolute}</div>
            <div className="bx-row-stop">{stop}</div>
            <button type="button" className="bx-details-toggle" aria-label={`${label} status and evidence`} title="Axis status and evidence" aria-expanded={detailsOpen} aria-controls={`bioxp-axis-${axis}-details`} onClick={() => setDetailsOpen(open => !open)}>⋯</button>
            <div className="bx-row-extras">{extras}</div>
            <div id={`bioxp-axis-${axis}-details`} className="bx-axis-details" hidden={!detailsOpen}>{details}</div>
        </div>
        {/* Phone selection never hides an outcome from a different axis. */}
        <div className="bx-notices">{notices}</div>
    </article>;
}
