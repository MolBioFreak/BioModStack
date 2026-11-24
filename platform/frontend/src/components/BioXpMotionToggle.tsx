import type { BioXpOperatorDashboard } from '../lib/bioxpClient';

export type BioXpMotionToggleProps = {
    reportedState?: BioXpOperatorDashboard['motion']['state'];
    enabled: boolean | null | undefined;
    reason?: string | null;
    connected: boolean;
    /** Robot catalog reason Enable is unavailable. Never applied to Disable. */
    onDisabledReason: string | null;
    pending?: boolean;
    onOn: () => void;
    onOff: () => void;
};

/** Motion cell of the status strip. Disable is only unavailable without a robot link. */
export function BioXpMotionToggle({ enabled, reportedState, reason, connected, onDisabledReason, pending = false, onOn, onOff }: BioXpMotionToggleProps) {
    const state = reportedState ?? (enabled === true ? 'on' : enabled === false ? 'blocked' : 'unknown');
    return <div role="group" aria-label="Motion" className="bx-status-cell bx-motion" data-testid="motion-toggle" data-motion={state}
        title={enabled === false && reason ? reason : undefined}>
        <i className="bx-led" data-led={enabled === true ? 'ok' : 'off'} aria-hidden="true" />
        <div><small>Motion</small><strong className="bx-motion-state">{{ on: 'On', off: 'Off', blocked: 'Blocked', unknown: 'Unknown' }[state]}</strong></div>
        <div className="bx-status-actions">
            <button type="button" className={enabled === true ? undefined : 'bx-go'} aria-pressed={state === 'on'} disabled={!connected || pending || onDisabledReason !== null}
                title={onDisabledReason ?? 'Turn motion on (no homing).'} onClick={onOn}>Enable motion</button>
            <button type="button" aria-pressed={state === 'off'} disabled={!connected}
                title="Turn motion off: new moves are refused until motion is turned on again. Does not stop a move in progress; use Stop for that."
                onClick={onOff}>Disable motion</button>
        </div>
    </div>;
}
