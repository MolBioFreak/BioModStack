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
    assert.match(appSource, /BioXP Handler Controls - OEM\/liquid-handler-first robot-local runtime proxy/);
    assert.match(cockpitSource, />BioXP Handler Controls</);
    assert.match(cockpitSource, /OEM\/liquid-handler-first BMS proxy for the robot-local BioXP runtime/);
    assert.match(cockpitSource, /label: 'Runtime Linkage'/);
    assert.doesNotMatch(layoutSource, /BioXP Cockpit/);
    assert.doesNotMatch(layoutSource, /BioXP Control Surface/);
});

test('default Handler Controls tab contains readback and OEM surfaces, not commissioning action panels', () => {
    const controlsTab = sourceBetween(cockpitSource, "{activeTab === 'controls'", "{activeTab === 'camera'");

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
        '/api/bioxp/oem/runtime/commands/',
        '/api/bioxp/liquid/status',
        '/api/bioxp/motion/axis/relative',
        '/api/bioxp/motion/power/enable',
        '/api/bioxp/protocol/execute',
    ]) {
        assert.match(clientSource, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
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
