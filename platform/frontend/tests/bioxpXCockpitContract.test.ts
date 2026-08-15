import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

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
    assert.match(source, /const abortXAggregate = \(\) => invokeAction\('oem\.abort_all'/);
});

test('X authority panel is a normal read-only truth surface with governed movement actions', () => {
    const panel = sourceBetween('<h4 className="font-semibold text-sky-50">X OEM authority</h4>', "{axis === 'z' && (");

    for (const actionId of ['oem.x.stop', 'oem.abort_all']) {
        assert.ok(panel.includes(actionId), `X authority panel must use ${actionId}`);
    }
    for (const hiddenActionId of ['oem.x.prepare', 'oem.x.reconcile_switch_masks', 'oem.x.set_max_speed', 'oem.x.set_max_acc', 'oem.x.restore_original_speed', 'oem.x.set_stall_guard', 'oem.x.diagnostic_home_axis', 'oem.x.set_home']) {
        assert.ok(!panel.includes(hiddenActionId), `normal X card must not expose ${hiddenActionId}`);
    }

    assert.match(source, /const xAbsoluteMinimum = Math\.max\(60,/);
    assert.match(source, /const xAbsoluteMaximum = Math\.min\(90263,/);
    assert.match(source, /const xRelativeLimitMargin = 20/);
    assert.match(source, /useBioXpOperatorActionAdmission\('oem\.x\.move_steps'.*xNegativeInputs/);
    assert.match(source, /useBioXpOperatorActionAdmission\('oem\.x\.move_steps'.*xPositiveInputs/);
    assert.match(source, /useBioXpOperatorActionAdmission\('oem\.x\.move_absolute'.*xAbsoluteInputs/);
    assert.match(source, /useBioXpOperatorActionAdmission\('oem\.x\.manual_panel_home'.*xHomeInputs/);
    assert.doesNotMatch(source, /xMotionConfirmation/);
    assert.doesNotMatch(source, /Confirm one exact next X action/);
    assert.doesNotMatch(source, /Confirm this exact X action first/);
    assert.match(panel, /Source range/);
    assert.match(panel, /0\.\.90263/);
    assert.match(panel, /Effective absolute minimum/);
    assert.match(panel, /20-step inner margin/);
    assert.match(panel, /GAP9\/10/);
    assert.match(panel, /GAP13\/12/);
    assert.match(panel, /Configured GAP4\/5\/6\/205/);
    assert.match(panel, /Board lifecycle generation/);
    assert.match(panel, /Last X failure/);
    assert.match(panel, /Latest X authority receipt/);
    assert.match(panel, /Serial-206 D1 adaptation/);
    assert.match(panel, /Software reference state \(not physical proof\)/);
    assert.match(panel, /Aggregate Abort \(all OEM boards\)/);
    assert.doesNotMatch(panel, /Physical reference/);
    assert.doesNotMatch(panel, /<input type="number"/);
});

test('receipt history keeps terminal proof and nested robot evidence visible', () => {
    assert.match(source, /Terminal proof verified/);
    assert.match(source, /Nested robot evidence/);
    assert.match(source, /stage_receipts/);
});

test('X absolute input and action gate use the effective 60 through 90263 envelope', () => {
    assert.match(source, /min=\{axis === 'x' \? xAbsoluteMinimum : undefined\}/);
    assert.match(source, /max=\{axis === 'x' \? xAbsoluteMaximum : undefined\}/);
    assert.match(source, /axis === 'x' \? !xAbsoluteEnabled/);
});

test('normal X controls use robot-owned exact admissions without a second UI confirmation gate', () => {
    assert.doesNotMatch(source, /XMotionConfirmation/);
    assert.doesNotMatch(source, /xConfirmationFingerprint/);
    assert.doesNotMatch(source, /xConfirmationAccepted/);
    assert.doesNotMatch(source, /setXMotionConfirmation/);
});
