import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const cockpitSource = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');

function sourceBetween(startNeedle: string, endNeedle: string): string {
    const start = cockpitSource.indexOf(startNeedle);
    assert.notEqual(start, -1, `missing start marker ${startNeedle}`);
    const end = cockpitSource.indexOf(endNeedle, start + startNeedle.length);
    assert.notEqual(end, -1, `missing end marker ${endNeedle}`);
    return cockpitSource.slice(start, end);
}

test('default connection tab does not expose old motor interlock/lock-clear buttons', () => {
    const connectionTab = sourceBetween("{activeTab === 'connection'", "{activeTab === 'operator'");

    assert.doesNotMatch(connectionTab, />\s*Prepare Motion Interlock\s*</);
    assert.doesNotMatch(connectionTab, />\s*Clear Head Lock\s*</);
    assert.match(connectionTab, /Motion interlock and lock-clear actions are commissioning-only/);
});

test('motion power actions are gated behind the commissioning toggle', () => {
    const motionPowerPanel = sourceBetween('const motionPowerPanel = (', 'return (');

    assert.match(motionPowerPanel, /showCommissioningControls \? \(/);
    assert.match(motionPowerPanel, /Enable 24V \/ Prep Axes/);
    assert.match(motionPowerPanel, /Prepare Interlock/);
    assert.match(motionPowerPanel, /Hard Reset/);
    assert.match(motionPowerPanel, /Actuating power, interlock, lock-clear, and reset buttons are hidden/);
});

test('raw axis movement is demoted to commissioning motion instead of the default operator path', () => {
    const manualTab = sourceBetween("{activeTab === 'manual'", "{activeTab === 'controls'");
    const tabList = sourceBetween('<div className="flex gap-1 border-b border-border-secondary flex-wrap">', "{activeTab === 'connection'");

    assert.match(tabList, /showCommissioningControls \? \[\{ key: 'manual', label: 'Commissioning Motion' \}\]/);
    assert.match(manualTab, /Commissioning Motion — Raw Axis Proxy/);
    assert.match(manualTab, /Hidden from the default handler path/);
    assert.match(manualTab, /BMS → robot-local BioXP API proxy only for live commissioning/);
    assert.match(manualTab, /<AxisControls axis="x" label="Gantry X"/);
    assert.match(manualTab, /<AxisControls axis="y" label="Gantry Y"/);
    assert.match(manualTab, /<AxisControls axis="z" label="Pipette Z"/);
    assert.match(manualTab, /<AxisControls axis="g" label="Gripper"/);
    assert.match(manualTab, /\{liquidCommissioningPanel\}/);
    assert.doesNotMatch(manualTab, /Reconnect USB Runtime/);
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

    assert.match(controlsTab, /\{oemReadbackPanel\}/);
    assert.match(controlsTab, /\{liquidPanel\}/);
    assert.match(controlsTab, /Thermal Cycler/);
    assert.match(controlsTab, /Chiller System/);
    assert.match(controlsTab, /Commissioning Access/);
    assert.doesNotMatch(controlsTab, /\{motionPowerPanel\}/);
    assert.doesNotMatch(controlsTab, /\{liquidCommissioningPanel\}/);
    assert.match(cockpitSource, /OEM Runtime & Startup/);
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
