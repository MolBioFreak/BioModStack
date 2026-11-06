import type { ReactNode } from 'react';
import type { BioXpOperatorDashboard } from '../lib/bioxpClient';

export interface BioXpStatusStripProps {
    connected: boolean;
    data: BioXpOperatorDashboard | undefined;
    stale?: boolean;
    connectedLabel?: string;
    motionControlsAvailable?: boolean;
    motionLabel?: string;
    observedAt?: string | null;
    hardwareFresh?: boolean | null;
    /** Time of the robot's last hardware reading; it is only refreshed by moves and explicit reads. */
    hardwareObservedAt?: string | null;
    ownershipLabel?: string;
    connectionControls?: ReactNode;
    connectionMessages?: ReactNode;
    controllerControls?: ReactNode;
    restart?: ReactNode;
}

const ageText = (iso: string) => { const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000)); return s < 90 ? `${s} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`; };

/** A projection of existing observations, never an admission or polling owner. */
export function BioXpStatusStrip({ connected, data, stale = false, connectedLabel, motionControlsAvailable = data?.motion.enabled, motionLabel, observedAt, hardwareFresh, hardwareObservedAt, ownershipLabel = 'Unavailable', connectionControls, connectionMessages, controllerControls, restart }: BioXpStatusStripProps) {
    const enclosureLabel = (value: boolean | null | undefined) => value === true ? 'Closed' : value === false ? 'Open' : '—';
    const references = data?.axes ?? [];
    const unreferenced = references.filter(axis => !['referenced', 'homed'].includes(axis.reference));
    const homing = !references.length ? 'not reported' : (unreferenced.length ? unreferenced : references).map(axis => `${axis.axis.toUpperCase()} ${axis.reference}`).join(' · ');
    return <section className="bx-ui bx-status" aria-label="Robot state and recovery">
        <div className="bx-status-cell bx-connection"><div><small>Robot</small><strong>{connectedLabel ?? (connected ? 'Connected' : 'Disconnected')}</strong></div><div className="bx-status-actions">{connectionControls}</div>{connectionMessages}</div>
        <div className="bx-status-cell" aria-label="Controller preparation and recovery"><div><small>Controllers</small><strong title={motionLabel}>{motionControlsAvailable === true ? 'Enabled' : motionControlsAvailable === false ? `Blocked${data?.motion.reason ? ` · ${data.motion.reason}` : ''}` : 'Unknown'}</strong></div><div className="bx-status-actions">{controllerControls}</div></div>
        <div className="bx-status-cell"><small>Homing</small><strong>{homing}</strong></div>
        <div className="bx-status-cell bx-enclosure"><small>Door · Latch</small><strong>{enclosureLabel(data?.enclosure.door_closed)} · {enclosureLabel(data?.enclosure.latch_closed)}</strong></div>
        <div className={`bx-status-cell bx-report ${stale ? 'bx-warning' : ''}`} title={`${ownershipLabel} · Snapshot ${data?.snapshot.freshness?.state ?? 'missing'} · Age ${data?.snapshot.freshness?.age_s ?? 'unknown'} s`}><small>Last report</small><strong>{observedAt ? new Date(observedAt).toLocaleTimeString() : '—'}</strong><small className="bx-hardware-age">{hardwareFresh === true ? 'Hardware reading current' : hardwareObservedAt ? `Hardware read ${ageText(hardwareObservedAt)} ago · refreshed on the next move or deck-readiness refresh` : 'Hardware not read since connect · refreshed on the next move or deck-readiness refresh'}</small>{restart}</div>
    </section>;
}
