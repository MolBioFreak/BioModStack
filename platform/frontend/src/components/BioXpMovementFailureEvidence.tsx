import type { BioXpOperatorReceiptDetailV2 } from '../lib/bioxpClient';

const object = (value: unknown): Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const number = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null;

/** Display retained native evidence only. Never derive admission or replay a command. */
export function BioXpMovementFailureEvidence({ receipt }: { receipt: Partial<BioXpOperatorReceiptDetailV2> | undefined }) {
    const stages = receipt?.deck_movement?.stages?.filter(stage => ['failed', 'ambiguous', 'stopped', 'aborted'].includes(stage.terminal_state)) ?? [];
    if (!stages.length) return null;
    return <section aria-label="Movement failure evidence" className="space-y-2 rounded border p-3 text-sm" style={{ borderColor: 'var(--border-primary)', color: 'var(--text-primary)' }}>
        <h3 className="font-semibold">Why this move did not finish</h3>
        {stages.map(stage => {
            const provider = object(object(stage.terminal_evidence).provider_evidence);
            const wait = object(provider.wait);
            const before = object(provider.before);
            const after = object(provider.timeout_position);
            const ack = object(provider.ack);
            const board = number(provider.board), motor = number(provider.motor);
            const axis = board === 4 && motor === 1 ? 'Z' : board === 4 && motor === 0 ? 'Y' : board === 5 && motor === 0 ? 'X' : null;
            const target = number(provider.requested_position);
            const beforePosition = before.position_reply_valid === true ? number(before.position) : null;
            const afterPosition = after.position_reply_valid === true ? number(after.position) : null;
            return <div key={stage.order}>
                <p>Stage {stage.order + 1}: {stage.operation} · {stage.terminal_state}{axis ? ` · ${axis} axis` : ''}{board !== null && motor !== null ? ` · board ${board}, motor ${motor}` : ''}.</p>
                {target !== null && <p>Requested position: {target} steps.</p>}
                {number(ack.status) !== null && <p>Controller response status: {number(ack.status)}. A response does not establish arrival.</p>}
                {(beforePosition !== null || afterPosition !== null) && <p>Controller position before: {beforePosition ?? 'unavailable'}; at timeout: {afterPosition ?? 'unavailable'} steps.</p>}
                {wait.target_reached === false && <p>No target-reached event was confirmed{number(wait.elapsed_ms) !== null ? ` during the ${number(wait.elapsed_ms)} ms wait` : ''}.</p>}
                {provider.command_sent === true && <p>The command was sent.</p>}
                {provider.physical_effect_verified === false && <p>Physical arrival was not verified.</p>}
            </div>;
        })}
        <p>This is the earlier command's evidence, not a block on a new request. Do not automatically retry it; the robot evaluates each new operator request.</p>
    </section>;
}
