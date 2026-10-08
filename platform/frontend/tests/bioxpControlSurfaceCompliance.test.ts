import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpitSource = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const protocolSource = readFileSync(resolve('src/components/BioXpProtocolRunner.tsx'), 'utf8');
const layoutSource = readFileSync(resolve('src/components/Layout.tsx'), 'utf8');
const appSource = readFileSync(resolve('src/App.tsx'), 'utf8');
const clientSource = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');

function sourceBetween(source: string, startNeedle: string, endNeedle: string): string {
    const start = source.indexOf(startNeedle);
    assert.notEqual(start, -1, `missing start marker ${startNeedle}`);
    const end = source.indexOf(endNeedle, start + startNeedle.length);
    assert.notEqual(end, -1, `missing end marker ${endNeedle}`);
    return source.slice(start, end);
}

test('BioXP shell and page labels point to Handler Controls, not legacy harness/interface wording', () => {
    assert.match(layoutSource, /title="BioXP Handler Controls"/);
    assert.match(layoutSource, />\s*BioXP Handler\s*</);
    assert.match(appSource, /BioXP Handler Controls/);
    assert.match(cockpitSource, />BioXP Handler Controls</);
    assert.match(cockpitSource, /Runtime link, motion, jobs, recipes, camera/);
    assert.match(cockpitSource, /label: 'Runtime Linkage'/);
    assert.doesNotMatch(layoutSource, /BioXP Cockpit/);
    assert.doesNotMatch(layoutSource, /BioXP Control Surface/);
});

test('default Handler Controls tab contains readback and live motion/grabber surfaces, not commissioning action panels', () => {
    const controlsTab = sourceBetween(cockpitSource, "{activeTab === 'controls'", "{activeTab === 'camera'");
    const liveMotionPanel = sourceBetween(cockpitSource, 'const liveXyzMotionPanel = (', 'const liquidPanel = (');
    const axisControls = sourceBetween(cockpitSource, 'const AxisControls = ({', 'const CameraAxisQuickControls = ({');

    assert.match(controlsTab, /\{liveXyzMotionPanel\}/);
    assert.match(liveMotionPanel, /Live X\/Y\/Z Motion/);
    assert.match(liveMotionPanel, /Gripper uses the OEM Gripper Contract below/);
    assert.match(cockpitSource, /Supervised OEM homing modes/);
    assert.match(cockpitSource, /OEM HomeXY/);
    assert.match(cockpitSource, />\s*Rehome Diagnostic \/ No Homing\s*</);
    assert.match(cockpitSource, /OEM Rehome ACK/);
    assert.match(cockpitSource, />\s*InitializeMotion \/ No Homing\s*</);
    assert.match(cockpitSource, /InitializeMotion ACK/);
    assert.equal(cockpitSource.match(/Arm Motors No Homing/g)?.length ?? 0, 1);
    assert.equal(cockpitSource.match(/OEM HomeXY/g)?.length ?? 0, 1);
    assert.equal(cockpitSource.match(/OEM Rehome ACK/g)?.length ?? 0, 1);
    assert.equal(cockpitSource.match(/InitializeMotion ACK/g)?.length ?? 0, 1);
    assert.doesNotMatch(liveMotionPanel, /<AxisControls axis="g" label="Grabber \/ Gripper"/);
    assert.match(controlsTab, /\{gripperPanel\}/);
    assert.match(axisControls, /Speed/);
    assert.match(axisControls, /Acc/);
    assert.match(axisControls, /SliderNumberControl/);
    assert.match(cockpitSource, /aria-label=\{`\$\{label\} value`\}/);
    assert.match(cockpitSource, /inputMode="numeric"/);
    assert.match(axisControls, /Absolute target/);
    assert.match(axisControls, /boundedAbsolutePosition/);
    assert.match(axisControls, /boundedStepMagnitude/);
    assert.match(axisControls, /clampRelativeStepsForDirection/);
    assert.match(cockpitSource, /AXIS_MOTION_SLIDER_PROFILES/);
    assert.match(cockpitSource, /stepMax: 91919/);
    assert.match(cockpitSource, /stepMax: 95247/);
    assert.match(cockpitSource, /stepMax: 160000/);
    assert.match(cockpitSource, /stepMax: 15000/);
    assert.doesNotMatch(axisControls, /Capture validation bundle/);
    assert.doesNotMatch(axisControls, /Dry-run bundle only/);
    assert.doesNotMatch(axisControls, /Operator note for supervised validation/);
    assert.doesNotMatch(axisControls, /Snapshot refs or image paths/);
    assert.doesNotMatch(liveMotionPanel, /These controls can physically move the robot/);
    assert.match(controlsTab, /\{oemReadbackPanel\}/);
    assert.match(controlsTab, /\{liquidPanel\}/);
    assert.match(controlsTab, /\{referencePanel\}/);
    assert.match(controlsTab, /Thermal Cycler/);
    assert.match(controlsTab, /Chiller System/);
    assert.match(controlsTab, /Commissioning Access/);
    assert.doesNotMatch(controlsTab, /\{motionPowerPanel\}/);
    assert.doesNotMatch(controlsTab, /\{liquidCommissioningPanel\}/);
});

test('commissioning-only actions live behind Commissioning Motion', () => {
    const manualTab = sourceBetween(cockpitSource, "{activeTab === 'manual'", "{activeTab === 'controls'");
    const tabList = sourceBetween(cockpitSource, '<div className="flex gap-1 border-b border-border-secondary flex-wrap">', "{activeTab === 'connection'");
    const liquidCommissioningPanel = sourceBetween(cockpitSource, 'const liquidCommissioningPanel = (', 'const oemReadbackPanel = (');

    assert.match(tabList, /showCommissioningControls \? \[\{ key: 'manual', label: 'Commissioning Motion' \}\]/);
    assert.match(manualTab, /\{motionPowerPanel\}/);
    assert.match(manualTab, /\{liquidCommissioningPanel\}/);
    assert.match(manualTab, /<AxisControls axis="x" label="Gantry X"/);
    assert.match(manualTab, /<AxisControls axis="door" label="Thermal Door"/);
    assert.match(liquidCommissioningPanel, /Commissioning Liquid Commands/);
});

test('protocol operator defaults to dry-run and requires explicit live execution arming', () => {
    assert.match(protocolSource, /const \[dryRun, setDryRun\] = useState\(true\)/);
    assert.match(protocolSource, /const \[liveExecutionArmed, setLiveExecutionArmed\] = useState\(false\)/);
    assert.match(protocolSource, /!dryRun && !liveExecutionArmed/);
    assert.match(protocolSource, /Arm live protocol execution/);
    assert.match(protocolSource, /Run Armed Live Protocol/);
    assert.match(protocolSource, /Run Dry-Run Protocol/);
});

test('frontend client still exposes all required proxy families while UI gates risky actions', () => {
    for (const marker of [
        '/api/bioxp/oem/startup/request',
        '/api/bioxp/oem/runtime/status',
        '/api/bioxp/oem/runtime/readiness/prepare-to-run-job/dry-run',
        '/api/bioxp/oem/runtime/commands/',
        '/api/bioxp/liquid/status',
        '/api/bioxp/motion/axis/relative',
        '/api/bioxp/motion/axis/zero',
        '/api/bioxp/motion/axis/home',
        '/api/bioxp/motion/oem/home_xy',
        '/api/bioxp/motion/oem/rehome',
        '/api/bioxp/motion/oem/initialize_motion',
        '/api/bioxp/motion/power/enable',
        '/api/bioxp/protocol/execute',
    ]) {
        assert.match(clientSource, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});

test('PrepareToRunJob UI uses the named no-motion readiness route, not the raw runtime command', () => {
    const oemPanel = sourceBetween(cockpitSource, 'const oemReadbackPanel = (', 'const visionPanel = (');

    assert.match(clientSource, /export const usePrepareToRunJobReadiness/);
    assert.match(oemPanel, /PrepareToRunJob Readiness \/ No Motion/);
    assert.match(oemPanel, /Startup\/readiness checks report no-motion flags/);
    assert.match(oemPanel, /No-motion readiness route/);
    assert.match(oemPanel, /recordOemAction\('prepare_to_run_job_readiness_no_motion'/);
    assert.doesNotMatch(oemPanel, /useOemRuntimeCommand\('PrepareToRunJob'\)/);
    assert.doesNotMatch(oemPanel, /recordOemAction\('PrepareToRunJob'/);
});

test('Zero and Switch Home controls are separate while live guardrails block risky raw motion', () => {
    const axisControls = sourceBetween(cockpitSource, 'const AxisControls = ({', 'const CameraAxisQuickControls = ({');
    const cameraAxisControls = sourceBetween(cockpitSource, 'const CameraAxisQuickControls = ({', 'type CameraHoldJogCommand = {');
    const cameraHoldJogPad = sourceBetween(cockpitSource, 'const CameraHoldJogPad = ({', 'const CameraSettingControl = ({');
    const axisDirectionHelper = sourceBetween(cockpitSource, 'const getAxisDirectionState = (', 'const hasMutationKeyPrefix =');

    assert.match(clientSource, /\/api\/bioxp\/motion\/axis\/zero/);
    assert.match(clientSource, /api\.post\('\/api\/bioxp\/motion\/axis\/home'/);
    assert.match(clientSource, /export const useZeroAxis/);
    assert.match(clientSource, /export const useHomeAxis/);
    assert.match(clientSource, /export const useOemHomeXY/);
    assert.match(clientSource, /export const useOemRehome/);
    assert.match(clientSource, /export const useOemInitializeMotion/);
    assert.match(axisDirectionHelper, /negativeBlocked = conflictingSwitches \|\| \(leftActive && !leftMasked\)/);
    assert.match(axisDirectionHelper, /positiveBlocked = conflictingSwitches \|\| \(rightActive && !rightMasked\)/);
    assert.match(axisDirectionHelper, /AXIS_REFERENCE_OK_STATES = new Set\(\['referenced', 'synced', 'known'\]\)/);
    assert.match(axisDirectionHelper, /isZPositiveDownReferenceGuardBlocked/);
    assert.match(axisDirectionHelper, /position == null \|\| position < 0/);
    assert.match(axisControls, /const negativeMoveBlocked = directionGuard\.negativeBlocked/);
    assert.match(axisControls, /const positiveMoveBlocked = directionGuard\.positiveBlocked \|\| zPositiveDownBlocked/);
    assert.match(axisControls, /const zPositiveDownBlocked = isZPositiveDownReferenceGuardBlocked\(axis, referenceState, displayPosition\)/);
    assert.match(axisControls, /const negativeButtonLabel = axis === 'z' \? 'UP\/-Z' : '◄'/);
    assert.match(axisControls, /const positiveButtonLabel = axis === 'z' \? 'DN\/\+Z' : '►'/);
    assert.match(axisControls, /const zeroToControllerBlocked = !axisReferenced \|\| !axisRangeAvailable/);
    assert.match(axisControls, /const switchHomeBlocked = true/);
    assert.match(axisControls, /Zero → 0/);
    assert.match(axisControls, /Switch Home/);
    assert.match(axisControls, /disabled=\{!enabled \|\| zeroAxis\.isPending \|\| zeroToControllerBlocked\}/);
    assert.match(axisControls, /disabled=\{!enabled \|\| homeAxis\.isPending \|\| switchHomeBlocked\}/);
    assert.match(axisControls, /Switch Home disabled here; use supervised OEM recipe/);
    assert.match(cameraAxisControls, /const negativeMoveBlocked = directionGuard\.negativeBlocked/);
    assert.match(cameraAxisControls, /const positiveMoveBlocked = directionGuard\.positiveBlocked \|\| zPositiveDownBlocked/);
    assert.match(cameraAxisControls, /useMotionReferenceStatus\(enabled, \[axis\]/);
    assert.match(cameraAxisControls, /Z DN\/\+Z blocked until reference\/position is trusted/);
    assert.match(cameraAxisControls, /disabled=\{!enabled \|\| moveRelative\.isPending \|\| negativeMoveBlocked\}/);
    assert.match(cameraAxisControls, /disabled=\{!enabled \|\| moveRelative\.isPending \|\| positiveMoveBlocked\}/);
    assert.match(cameraHoldJogPad, /useMotionReferenceStatus\(enabled, \[\.\.\.CAMERA_HOLD_JOG_AXES\]/);
    assert.match(cameraHoldJogPad, /command\.axis === 'z' && command\.steps > 0 && zPositiveDownBlocked/);
    assert.match(cameraHoldJogPad, /zPositive\.blocked \|\| zPositiveDownBlocked/);
    assert.match(cameraAxisControls, /motion buttons blocked until telemetry clears/);
    assert.match(cockpitSource, /UP\/-Z/);
    assert.match(cockpitSource, /DN\/\+Z/);
    assert.doesNotMatch(axisControls, /limitConflictBlocked/);
    assert.doesNotMatch(axisControls, /Home → 0 blocked/);
    assert.doesNotMatch(axisControls, /Raw switch conflict fault/);
});

test('stale BioXP control-surface labels are absent from active frontend surfaces', () => {
    const combined = [cockpitSource, protocolSource, layoutSource, appSource].join('\n');
    for (const stale of [
        'OEM + Liquid Handler',
        'OEM Startup / Runtime Controls',
        'Motion Control System',
        'Manual Movement',
        'API Manual Movement',
        'OEM Controls & Thermals',
        'Commissioning Tools',
        'BioXP Cockpit',
        'BioXP Control Surface',
        'BioXP Hardware Interface',
        'BioXP Harness',
        'BioxP Harness',
        'Linkage & Status',
        'Thermal Controller',
        'Terminal Console',
        'Recovery Controls',
        'Protocol Executor',
        'Protocol Controller',
        'Realtime Message',
        'Carriage Panel',
        'Payload Queue',
        'Hostile Console',
        'Device Feed',
        'IOXP Handler Controls',
    ]) {
        assert.doesNotMatch(combined, new RegExp(stale.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});
