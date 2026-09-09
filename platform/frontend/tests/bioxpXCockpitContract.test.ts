import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

const source = readFileSync('src/components/BioXpCockpit.tsx', 'utf8');

const sourceBetween = (startNeedle: string, endNeedle: string): string => {
    const start = source.indexOf(startNeedle);
    assert.notEqual(start, -1, `missing source boundary: ${startNeedle}`);
    const end = source.indexOf(endNeedle, start + startNeedle.length);
    assert.notEqual(end, -1, `missing source boundary: ${endNeedle}`);
    return source.slice(start, end);
};

test('X manual controls route only through stable provider-owned action IDs', () => {
    const runControl = sourceBetween('const runControl =', 'const runAbsolute =');
    const runAbsolute = sourceBetween('const runAbsolute =', 'const stopAxis =');
    const stopAxis = sourceBetween('const stopAxis =', 'const abortXAggregate =');

    assert.match(runControl, /axis === 'x'/);
    assert.match(runControl, /oem\.x\.move_steps/);
    assert.match(runControl, /oem\.x\.manual_panel_home/);
    assert.match(runControl, /if \(axis === 'door'\) return/);
    assert.doesNotMatch(runControl, /invokeOperatorPath\([^\n]+\{ axis: 'x'/);

    assert.match(runAbsolute, /axis === 'x'/);
    assert.match(runAbsolute, /oem\.x\.move_absolute/);
    assert.doesNotMatch(runAbsolute, /invokeOperatorPath\([^\n]+\{ axis: 'x'/);

    assert.match(stopAxis, /axis === 'x'/);
    assert.match(stopAxis, /oem\.x\.stop/);
    assert.doesNotMatch(stopAxis, /invokeOperatorPath\([^\n]+\{ axis: 'x'/);
    // R2: independent typed interrupt, not ordinary V1 invocation. This checks
    // routing only; aggregate logical-vs-physical disposition remains unresolved.
    assert.match(source, /const abortXAggregate = \(\) => invokeInterrupt\('oem\.abort_all'/);
    assert.match(source, /const interruptXStop = useInterruptBioXpOperatorActionV1\(\)/);
    assert.match(source, /const interruptAggregateAbort = useInterruptBioXpOperatorActionV1\(\)/);
    assert.match(runControl, /submitV2\(\{ \.\.\.envelope, action_id: 'oem\.x\.move_steps', inputs: xNegativeInputs/);
    assert.match(runAbsolute, /if \(!xAbsoluteEnabled\) return/);
});

test('X authority panel is a normal read-only truth surface with governed movement actions', () => {
    const panel = sourceBetween('<h4 className="mt-2 font-semibold text-sky-50">X OEM authority</h4>', "{axis === 'z' && (");

    for (const actionId of ['oem.x.stop', 'oem.abort_all']) {
        assert.ok(panel.includes(actionId), `X authority panel must use ${actionId}`);
    }
    for (const hiddenActionId of ['oem.x.prepare', 'oem.x.reconcile_switch_masks', 'oem.x.set_max_speed', 'oem.x.set_max_acc', 'oem.x.restore_original_speed', 'oem.x.set_stall_guard', 'oem.x.diagnostic_home_axis', 'oem.x.set_home']) {
        assert.ok(!panel.includes(hiddenActionId), `normal X card must not expose ${hiddenActionId}`);
    }

    // R3/R5: numeric metadata and coherent V2 admission, not retired V1
    // per-click hooks or a second local physical-limit policy. OEM relative
    // beyondLimit/current-position reconciliation remains on the board/provider
    // (ClassHeadBoard.moveSteps:241–250), not stale browser coordinates.
    assert.match(source, /const xAbsoluteMinimum = integerMinimum\(xAbsoluteInput\)/);
    assert.match(source, /const xAbsoluteMaximum = integerMaximum\(xAbsoluteInput\)/);
    assert.match(source, /const xRelativeMaximum = relativeMagnitudeMaximum\(xMoveInput\)/);
    assert.match(source, /integerInputError\(xNegativeInputs.steps, xMoveInput/);
    assert.match(source, /integerInputError\(xPositiveInputs.steps, xMoveInput/);
    for (const id of ['move_steps', 'move_absolute', 'manual_panel_home']) {
        assert.ok(source.includes(`v2ActionDisabledReason('oem.x.${id}')`));
    }
    assert.match(source, /if \(!v2AuthorityCoherent\) return 'Current robot control state is unavailable.'/);
    assert.match(source, /return action.enabled === true \? null : action.disabled_reason/);
    assert.doesNotMatch(source, /xMotionConfirmation/);
    assert.doesNotMatch(source, /Confirm one exact next X action/);
    assert.doesNotMatch(source, /Confirm this exact X action first/);
    assert.match(panel, /Catalog absolute bounds/);
    assert.match(panel, /xAbsoluteMinimum \?\? 'unbounded'/);
    assert.match(panel, /xAbsoluteMaximum \?\? 'unbounded'/);
    assert.match(panel, /Catalog relative magnitude/);
    assert.match(panel, /GAP9\/10/);
    assert.match(panel, /GAP13\/12/);
    assert.match(panel, /Configured GAP4\/5\/6\/205/);
    assert.match(panel, /Board lifecycle generation/);
    assert.match(panel, /Last X failure/);
    assert.match(panel, /Latest X authority receipt/);
    assert.match(panel, /SAP12\/13 observed/);
    assert.match(panel, /Recovered OEM X initialization writes neither register/);
    assert.match(panel, /Software reference state \(not physical proof\)/);
    assert.match(panel, /Software Abort \(cancel waiters\)/);
    assert.match(panel, /motors may continue/);
    assert.doesNotMatch(panel, /Physical reference/);
    assert.doesNotMatch(panel, /<input type="number"/);
});

test('receipt history keeps terminal proof and nested robot evidence visible', () => {
    assert.match(source, /Terminal proof verified/);
    assert.match(source, /Nested robot evidence/);
    assert.match(source, /stage_receipts/);
});

test('X absolute input and action gate use the effective 60 through 90263 envelope', () => {
    assert.match(source, /min=\{axis === 'x' \? xAbsoluteMinimum : axis === 'z' \? zAbsoluteMinimum : undefined\}/);
    assert.match(source, /max=\{axis === 'x' \? xAbsoluteMaximum : axis === 'z' \? zAbsoluteMaximum : undefined\}/);
    assert.match(source, /axis === 'x' \? !xAbsoluteEnabled/);
    // Execute the actual validator, independently encode Serial-206 effective
    // 60..90263 vectors (CCI.moveX:4233–4243; locked X max 90263). Mounted tests
    // additionally assert these exact DOM bounds and dispatch/no-dispatch.
    const validators = ts.transpile(sourceBetween('const integerMinimum =', 'const relativeMagnitudeMaximum ='));
    const validate = new Function(`${validators}; return integerInputError;`)() as (value: number, input: object, label: string) => string | null;
    const schema = { minimum: 60, maximum: 90263 };
    for (const target of [60, 61, 90262, 90263]) assert.equal(validate(target, schema, 'X'), null);
    for (const target of [0, 59, 90264, 100000]) assert.equal(validate(target, schema, 'X'), 'X must be an integer from 60 through 90263.');
    for (const target of [60.5, 90263.5, NaN, Infinity]) assert.equal(validate(target, schema, 'X'), 'X must be an integer.');
});

test('normal X controls use robot-owned exact admissions without a second UI confirmation gate', () => {
    assert.doesNotMatch(source, /XMotionConfirmation/);
    assert.doesNotMatch(source, /xConfirmationFingerprint/);
    assert.doesNotMatch(source, /xConfirmationAccepted/);
    assert.doesNotMatch(source, /setXMotionConfirmation/);
});
