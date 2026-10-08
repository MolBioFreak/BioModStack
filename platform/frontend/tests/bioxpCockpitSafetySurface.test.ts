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

test('camera overlaid jog controls are commissioning-only', () => {
    const cameraMotionPanel = sourceBetween('const cameraMotionPanel = (', 'const motionPowerPanel = (');

    assert.match(cameraMotionPanel, /showCommissioningControls \? \(/);
    assert.match(cameraMotionPanel, /<CameraHoldJogPad enabled=\{isConnected\} \/>/);
    assert.match(cameraMotionPanel, /Camera-overlaid jog controls are commissioning-only/);
});

test('default operator controls preserve readback and thermal surfaces while direct liquid commands are gated', () => {
    const controlsTab = sourceBetween("{activeTab === 'controls'", "{activeTab === 'camera'");
    const liquidPanel = sourceBetween('const liquidPanel = (', 'const oemReadbackPanel = (');

    assert.match(controlsTab, /\{oemReadbackPanel\}/);
    assert.match(controlsTab, /\{liquidPanel\}/);
    assert.match(controlsTab, /Thermal Cycler/);
    assert.match(controlsTab, /Chiller System/);
    assert.match(controlsTab, /Commissioning Tools/);
    assert.match(liquidPanel, /showCommissioningControls && \(/);
    assert.match(liquidPanel, />\s*Aspirate\s*</);
    assert.match(liquidPanel, />\s*Dispense\s*</);
    assert.match(liquidPanel, />\s*Mix\s*</);
});
