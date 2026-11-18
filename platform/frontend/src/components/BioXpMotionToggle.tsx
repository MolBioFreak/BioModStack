export type BioXpMotionToggleProps = {
    /** Telemetry motion.enabled: true on, false off, undefined unknown. */
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
export function BioXpMotionToggle({ enabled, reason, connected, onDisabledReason, pending = false, onOn, onOff }: BioXpMotionToggleProps) {
    const state = enabled === true ? 'On' : enabled === false ? 'Off' : 'Unknown';
    return <div role="group" aria-label="Motion" className="bx-status-cell bx-motion" data-testid="motion-toggle" data-motion={state.toLowerCase()}
        title={enabled === false && reason ? reason : undefined}>
        <i className="bx-led" data-led={enabled === true ? 'ok' : 'off'} aria-hidden="true" />
        <div><small>Motion</small><strong className="bx-motion-state">{state}</strong></div>
        <div className="bx-status-actions">
            <button type="button" className={enabled === true ? undefined : 'bx-go'} aria-pressed={enabled === true} disabled={!connected || pending || onDisabledReason !== null}
                title={onDisabledReason ?? 'Turn motion on (no homing).'} onClick={onOn}>Enable motion</button>
            <button type="button" aria-pressed={enabled === false} disabled={!connected}
                title="Turn motion off: new moves are refused until motion is turned on again. Does not stop a move in progress; use Stop for that."
                onClick={onOff}>Disable motion</button>
        </div>
    </div>;
}
