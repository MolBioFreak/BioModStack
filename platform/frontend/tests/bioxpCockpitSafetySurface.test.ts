import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpitSource = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const clientSource = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');

function sourceBetween(startNeedle: string, endNeedle: string): string {
    const start = cockpitSource.indexOf(startNeedle);
    assert.notEqual(start, -1, `missing start marker ${startNeedle}`);
    const end = cockpitSource.indexOf(endNeedle, start + startNeedle.length);
    assert.notEqual(end, -1, `missing end marker ${endNeedle}`);
    return cockpitSource.slice(start, end);
}

test('saved-but-inactive BioXP link is not mislabeled as hardware offline', () => {
    assert.match(cockpitSource, /const linkInactive = linkageConfigured && !interlinkActive/);
    assert.match(cockpitSource, /const hardwareBadgeLabel = linkInactive\s*\? 'LINK INACTIVE'/);
    assert.match(cockpitSource, /BIOXP LINK INACTIVE/);
    assert.match(cockpitSource, /Saved robot profile is present but inactive/);
});

test('default connection tab does not expose old motor interlock/lock-clear buttons', () => {
    const connectionTab = sourceBetween("{activeTab === 'connection'", "{activeTab === 'operator'");

    assert.doesNotMatch(connectionTab, />\s*Prepare Motion Interlock\s*</);
    assert.doesNotMatch(connectionTab, />\s*Clear Head Lock\s*</);
    assert.match(connectionTab, /Motion interlock and lock-clear actions are commissioning-only/);
});

test('motion power actions are gated behind the commissioning toggle without motor reset controls', () => {
    const motionPowerPanel = sourceBetween('const motionPowerPanel = (', 'return (');

    assert.match(motionPowerPanel, /showCommissioningControls \? \(/);
    assert.match(motionPowerPanel, /Enable 24V \/ Prep Axes/);
    assert.match(motionPowerPanel, /Prepare Interlock/);
    assert.doesNotMatch(motionPowerPanel, /Hard Reset/);
    assert.doesNotMatch(motionPowerPanel, /motionHardReset/);
    assert.match(motionPowerPanel, /Actuating power, interlock, and lock-clear buttons are hidden/);
});

test('commissioning motion keeps axis controls while default handler restores gripper', () => {
    const manualTab = sourceBetween("{activeTab === 'manual'", "{activeTab === 'controls'");
    const tabList = sourceBetween('<div className="flex gap-1 border-b border-border-secondary flex-wrap">', "{activeTab === 'connection'");

    assert.match(tabList, /showCommissioningControls \? \[\{ key: 'manual', label: 'Commissioning Motion' \}\]/);
    assert.match(manualTab, /Commissioning Motion — Axis Controls/);
    assert.match(manualTab, /Commissioning-only axis surface for raw\/recovery contexts/);
    assert.doesNotMatch(manualTab, /Direct OEM homing modes/);
    assert.match(manualTab, /<AxisControls axis="x" label="Gantry X"/);
    assert.match(manualTab, /<AxisControls axis="y" label="Gantry Y"/);
    assert.match(manualTab, /<AxisControls axis="z" label="Pipette Z"/);
    assert.match(manualTab, /<AxisControls axis="g" label="Gripper"/);
    assert.match(manualTab, /\{liquidCommissioningPanel\}/);
    assert.doesNotMatch(manualTab, /Reconnect USB Runtime/);
    assert.match(cockpitSource, /Live X\/Y\/Z \+ Grabber Motion/);
    assert.match(cockpitSource, /<AxisControls axis="g" label="Grabber \/ Gripper"/);
    assert.match(cockpitSource, /AXIS_MOTION_SLIDER_PROFILES/);
    assert.match(cockpitSource, /type="range"/);
    assert.match(cockpitSource, /type="number"/);
    assert.match(cockpitSource, /inputMode="numeric"/);
    assert.match(cockpitSource, /Absolute target/);
    assert.match(cockpitSource, /Payloads are clamped before send/);
    assert.match(cockpitSource, /stepMax: 15000/);
    assert.doesNotMatch(cockpitSource, /Capture validation bundle/);
    assert.doesNotMatch(cockpitSource, /Dry-run bundle only/);
    assert.doesNotMatch(cockpitSource, /Operator note for supervised validation/);
    assert.doesNotMatch(cockpitSource, /Snapshot refs or image paths/);
    assert.doesNotMatch(cockpitSource, /These controls can physically move the robot/);
    assert.match(cockpitSource, />\s*Switch Home\s*</);
    assert.match(cockpitSource, /Direct OEM homing modes for supervised testing/);
    assert.match(cockpitSource, /OEM HomeXY/);
    assert.match(cockpitSource, /InitializeMotion ACK/);
    assert.equal(cockpitSource.match(/Arm Motors No Homing/g)?.length ?? 0, 1);
    assert.equal(cockpitSource.match(/OEM HomeXY/g)?.length ?? 0, 1);
    assert.equal(cockpitSource.match(/InitializeMotion ACK/g)?.length ?? 0, 1);
    assert.doesNotMatch(cockpitSource, /Switch-home disabled/);
});

test('camera overlaid jog controls are commissioning-only', () => {
    const cameraMotionPanel = sourceBetween('const cameraMotionPanel = (', 'const motionPowerPanel = (');

    assert.match(cameraMotionPanel, /showCommissioningControls \? \(/);
    assert.match(cameraMotionPanel, /<CameraHoldJogPad enabled=\{isConnected\} \/>/);
    assert.match(cameraMotionPanel, /Camera-overlaid jog controls are commissioning-only/);
});

test('default handler controls preserve readback and thermal surfaces while direct liquid commands are commissioning-only', () => {
    const controlsTab = sourceBetween("{activeTab === 'controls'", "{activeTab === 'camera'");
    const liquidPanel = sourceBetween('const liquidPanel = (', 'const liquidCommissioningPanel = (');
    const liquidCommissioningPanel = sourceBetween('const liquidCommissioningPanel = (', 'const oemReadbackPanel = (');

    assert.match(controlsTab, /\{liveXyzMotionPanel\}/);
    assert.match(controlsTab, /\{oemReadbackPanel\}/);
    assert.match(controlsTab, /\{liquidPanel\}/);
    assert.match(controlsTab, /Thermal Cycler/);
    assert.match(controlsTab, /Chiller System/);
    assert.match(controlsTab, /Commissioning Access/);
    assert.doesNotMatch(controlsTab, /\{motionPowerPanel\}/);
    assert.doesNotMatch(controlsTab, /\{liquidCommissioningPanel\}/);
    assert.match(cockpitSource, /OEM Runtime Readback & No-Motion Checks/);
    assert.match(cockpitSource, /Handler Controls/);
    assert.match(cockpitSource, /Startup Preflight \/ No Motion/);
    assert.match(cockpitSource, /EMERGENCY STOP/);
    assert.doesNotMatch(cockpitSource, /label: 'Manual Movement'/);
    assert.doesNotMatch(liquidPanel, />\s*Aspirate\s*</);
    assert.doesNotMatch(liquidPanel, />\s*Dispense\s*</);
    assert.doesNotMatch(liquidPanel, />\s*Mix\s*</);
    assert.match(liquidCommissioningPanel, />\s*Aspirate\s*</);
    assert.match(liquidCommissioningPanel, />\s*Dispense\s*</);
    assert.match(liquidCommissioningPanel, />\s*Mix\s*</);
});

test('OEM readback panel keeps new supervised OEM mode controls in one place', () => {
    const oemReadbackPanel = sourceBetween('const oemReadbackPanel = (', 'const visionPanel = (');

    assert.match(oemReadbackPanel, /No-motion startup\/readiness/);
    assert.match(oemReadbackPanel, /Startup Preflight \/ No Motion/);
    assert.match(oemReadbackPanel, /PrepareToRunJob Readiness \/ No Motion/);
    assert.match(oemReadbackPanel, /EMERGENCY STOP/);
    assert.doesNotMatch(oemReadbackPanel, /Arm Motors No Homing/);
    assert.match(oemReadbackPanel, /Direct OEM homing modes for supervised testing/);
    assert.match(oemReadbackPanel, /route success is not physical proof/);
    assert.match(oemReadbackPanel, /OEM HomeXY/);
    assert.match(oemReadbackPanel, />\s*Rehome Diagnostic \/ No Homing\s*</);
    assert.match(oemReadbackPanel, /OEM Rehome ACK/);
    assert.match(oemReadbackPanel, />\s*InitializeMotion \/ No Homing\s*</);
    assert.match(oemReadbackPanel, /InitializeMotion ACK/);
    assert.match(oemReadbackPanel, /recordOemAction\('oem_home_xy'/);
    assert.match(oemReadbackPanel, /recordOemAction\('oem_initialize_motion_ack'/);
    assert.match(oemReadbackPanel, /operator_ack: 'HOMEXY'/);
    assert.match(oemReadbackPanel, /operator_ack: 'REHOME'/);
    assert.match(oemReadbackPanel, /operator_ack: 'INITIALIZE'/);
    assert.match(oemReadbackPanel, /operator_ack: 'INITIALIZE_WITH_HOMING'/);
    assert.doesNotMatch(oemReadbackPanel, /supervised_oem_home_xy|diagnostic_rehome_no_homing|supervised_oem_rehome_ack|diagnostic_initialize_motion_no_homing|supervised_initialize_motion_ack/);
});

test('service operations tab exposes named operation wrappers without acknowledgement form clutter', () => {
    const tabList = sourceBetween('<div className="flex gap-1 border-b border-border-secondary flex-wrap">', "{activeTab === 'connection'");
    const servicePanel = sourceBetween('const serviceOperationsPanel = (', 'const referencePanel = (');
    const serviceTab = sourceBetween("{activeTab === 'service'", "{activeTab === 'manual'");

    assert.match(tabList, /label: 'Gated Service Recipes'/);
    assert.match(servicePanel, /Ready-Made Robot Recipes/);
    assert.doesNotMatch(servicePanel, /Operator acknowledgement required/);
    assert.doesNotMatch(servicePanel, /controller readback is not being treated as physical proof/);
    assert.doesNotMatch(servicePanel, /operation note \/ physical observation/);
    assert.match(servicePanel, /Prepare Motion Safely/);
    assert.match(servicePanel, /Lock Latch/);
    assert.match(servicePanel, /Unlock Latch/);
    assert.match(servicePanel, /Clear Head Lock/);
    assert.match(servicePanel, /Lift Z Up/);
    assert.match(servicePanel, /Micro-move Proof/);
    assert.match(servicePanel, /Readiness Bundle/);
    assert.match(servicePanel, /Operation Capabilities/);
    assert.match(servicePanel, /Latest Operation Report/);
    assert.match(serviceTab, /\{serviceOperationsPanel\}/);
    assert.match(serviceTab, /\{motionPowerPanel\}/);
    assert.match(serviceTab, /\{referencePanel\}/);
});


test('raw motion current defaults stay OEM-safe and manual axis controls use live guardrails', () => {
    const axisControls = sourceBetween('const AxisControls = ({', 'const CameraAxisQuickControls = ({');

    assert.match(clientSource, /const runCurrent = payload\.run_current \?\? 10/);
    assert.match(clientSource, /standby_current: payload\.standby_current \?\? 10/);
    assert.match(axisControls, /useMotionReferenceStatus\(enabled, \[axis\]/);
    assert.match(axisControls, /useMotionRangeStatus\(enabled/);
    assert.match(axisControls, /const axisReferenced = referenceState === 'referenced'/);
    assert.match(axisControls, /const absoluteMoveBlocked = !axisReferenced \|\| !axisRangeAvailable/);
    assert.match(axisControls, /const switchHomeBlocked = true/);
    assert.match(axisControls, /Switch Home requires capture_bundle=true/);
    assert.match(axisControls, /negativeMoveBlocked = directionGuard\.negativeBlocked/);
    assert.match(axisControls, /positiveMoveBlocked = directionGuard\.positiveBlocked/);
});


test('raw telemetry payloads are debug disclosures rather than default operator panels', () => {
    assert.match(cockpitSource, /<details className="rounded border border-border-primary bg-surface\/40 p-2">/);
    assert.match(cockpitSource, /Debug payload/);
    assert.doesNotMatch(cockpitSource, /<pre className="text-\[10px\] font-mono text-content-muted p-3 bg-\[#000000\]/);
});
