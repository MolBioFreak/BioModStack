export type BioXpMotionToggleProps = {
    /** Telemetry motion.enabled: true on, false off, undefined unknown. */
    enabled: boolean | null | undefined;
    reason?: string | null;
    connected: boolean;
    /** Robot catalog reason Motion On is unavailable. Never applied to Off. */
    onDisabledReason: string | null;
    pending?: boolean;
    onOn: () => void;
    onOff: () => void;
};

/** Top-bar Motion On/Off. Off is only unavailable without a robot link. */
export function BioXpMotionToggle({ enabled, reason, connected, onDisabledReason, pending = false, onOn, onOff }: BioXpMotionToggleProps) {
    const state = enabled === true ? 'On' : enabled === false ? 'Off' : 'Unknown';
    return <div role="group" aria-label="Motion" className="bx-motion" data-testid="motion-toggle" data-motion={state.toLowerCase()}
        title={enabled === false && reason ? reason : undefined}>
        <span className="bx-motion-state">Motion {state}</span>
        <button type="button" aria-pressed={enabled === true} disabled={!connected || pending || onDisabledReason !== null}
            title={onDisabledReason ?? 'Turn motion on (no homing).'} onClick={onOn}>On</button>
        <button type="button" aria-pressed={enabled === false} disabled={!connected}
            title="Turn motion off: new moves are refused until motion is turned on again. Does not stop a move in progress; use Stop for that."
            onClick={onOff}>Off</button>
    </div>;
}
