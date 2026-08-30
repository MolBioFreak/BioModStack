import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import {
    assertBioXpOperatorActionV2Request,
    assertBioXpOperatorMethodV1Request,
    bioXpPostDispatchCommandIdentity,
    bioXpOperatorGenerationPayload,
    bioXpReceiptV2IsNonTerminal,
} from '../../src/lib/bioxpClient';
import type { BioXpOperatorReceiptV2Status } from '../../src/lib/bioxpClient';

describe('BioXP operator generation payload', () => {
    it('keeps the BMS connection epoch distinct from the robot ownership epoch', () => {
        expect(bioXpOperatorGenerationPayload(2637337272774657, 2)).toEqual({
            expected_connection_generation: 2637337272774657,
            expected_ownership_generation: 2,
        });
    });

    it('rejects absent or non-positive generation identities', () => {
        expect(() => bioXpOperatorGenerationPayload(0, 2)).toThrow('connection generation');
        expect(() => bioXpOperatorGenerationPayload(7, 0)).toThrow('ownership generation');
    });
});

describe('BioXP OEM XY method input bounds', () => {
    const request = (x_steps: number, y_steps: number) => ({
        expected_connection_generation: 1,
        schema_version: 'bioxp.operator_method_request.v1' as const,
        idempotency_key: 'xy-method-bounds',
        method_action_id: 'oem.xy.move_absolute' as const,
        expected_ownership_generation: 1,
        expected_board_epoch_by_board: {},
        inputs: { x_steps, y_steps },
    });

    it('requires signed-int32 integer X and Y inputs', () => {
        expect(() => assertBioXpOperatorMethodV1Request(request(10, 2 ** 31))).toThrow('signed int32');
        expect(() => assertBioXpOperatorMethodV1Request(request(1.5, 10))).toThrow('signed int32');
        expect(() => assertBioXpOperatorMethodV1Request(request(-(2 ** 31), 2 ** 31 - 1))).not.toThrow();
    });
});

describe('BioXP OEM deck movement request', () => {
    it('accepts only semantic inputs and exact board 4/5 fences', () => {
        const request = {
            expected_connection_generation: 7,
            schema_version: 'bioxp.operator_action_request.v2' as const,
            idempotency_key: 'deck-move-1',
            expected_ownership_generation: 3,
            expected_board_epoch_by_board: { '4': 11, '5': 12 },
            action_id: 'oem.deck.move_to_location' as const,
            inputs: { target: 'LOC_OC', camera_offset: false },
        };
        expect(() => assertBioXpOperatorActionV2Request(request)).not.toThrow();
        expect(() => assertBioXpOperatorActionV2Request({ ...request, expected_board_epoch_by_board: { '4': 11 } })).toThrow('boards 4 and 5');
        expect(() => assertBioXpOperatorActionV2Request({ ...request, inputs: { ...request.inputs, x: 1 } } as never)).toThrow('target and camera_offset');
    });
});

describe('BioXP post-dispatch command reconciliation', () => {
    it('recovers only bounded command identity and status evidence from structured errors', () => {
        const error = { response: { data: { detail: {
            error: 'post_dispatch_receipt_validation_failed',
            command_id: 'deck-command-uncertain-1',
            status_path: '/api/bioxp/operator-controls/v2/receipts/deck-command-uncertain-1',
            retry_guidance: 'do_not_resubmit_reconcile_by_command_id',
        } } } };
        expect(bioXpPostDispatchCommandIdentity(error)).toEqual({
            commandId: 'deck-command-uncertain-1',
            statusPath: '/api/bioxp/operator-controls/v2/receipts/deck-command-uncertain-1',
            retryGuidance: 'do_not_resubmit_reconcile_by_command_id',
        });
        expect(bioXpPostDispatchCommandIdentity({ response: { data: { detail: { command_id: 'x'.repeat(161) } } } })).toBeNull();
    });

    it('polls pending receipts and stops for every terminal truth state', () => {
        expect(bioXpReceiptV2IsNonTerminal({ terminal: false } as never)).toBe(true);
        const terminalStatuses: BioXpOperatorReceiptV2Status[] = [
            'completed', 'failed', 'rejected', 'ambiguous', 'stopped', 'aborted', 'cancelled',
        ];
        for (const status of terminalStatuses) {
            expect(bioXpReceiptV2IsNonTerminal({ status, terminal: true } as never)).toBe(false);
        }
        const clientSource = readFileSync(`${process.cwd()}/src/lib/bioxpClient.ts`, 'utf8');
        for (const status of ['stopped', 'aborted', 'cancelled']) {
            expect(clientSource).toContain(`| '${status}'`);
        }
    });
});

describe('BioXP interrupt identity and reachability', () => {
    it('routes every OEM axis stop and aggregate abort through v2 interrupt controls', () => {
        const source = readFileSync(`${process.cwd()}/src/components/BioXpCockpit.tsx`, 'utf8');
        expect(source).toContain("invokeInterrupt('oem.y.stop', 'BMS operator requested recovered-OEM addressed Y STOP')");
        expect(source).toContain("v2InterruptActionById('oem.abort_all')");
        expect(source).not.toContain("operatorActionById('oem.abort_all')");
        expect(source).not.toContain("axis === 'y' ? '/motion/diagnostics/stop'");
        expect(source).toMatch(/useBioXpOperatorControlCatalogV2\(\s*generation,\s*linkConnected,/);
        expect(source).not.toMatch(/disabled=\{[^}\n]*v2InterruptActionById/);
        for (const hook of [
            'interruptXStop',
            'interruptYStop',
            'interruptZStop',
            'interruptZAbort',
            'interruptAggregateAbort',
        ]) {
            expect(source).toContain(`const ${hook} = useInterruptBioXpOperatorActionV1();`);
        }
        for (const actionId of ['oem.x.stop', 'oem.y.stop', 'oem.z.stop', 'oem.z.abort', 'oem.abort_all']) {
            expect(source).toContain(`interruptPending('${actionId}')`);
        }
        expect(source).not.toContain('interruptYAction');
    });
});
