import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const source = readFileSync(resolve('src/components/BioXpOperatorControlTabs.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');
const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const dashboard = readFileSync(resolve('src/components/BioXpQuickDashboard.tsx'), 'utf8');

test('catalog-driven control plane renders every action, critical groups, meta actions, and logs as separate panes', () => {
    for (const label of [
        'OEM Route Control Plane', 'Individual Controls', 'Critical Controls', 'All Individual Controls',
        'Motion Power', 'Transport / Evidence', 'Safety / Recovery', 'Initialization',
        'Meta Actions', 'Logs', 'role="tablist"', 'role="tab"', 'role="tabpanel"',
        'Run exactly this action', 'Search individual controls',
    ]) assert.match(source, new RegExp(label));
    for (const token of ['isCriticalAction', 'action.subsystem === subsystemFilter', 'catalogQuery.data?.actions']) {
        assert.ok(source.includes(token), `missing catalog grouping token: ${token}`);
    }
    assert.match(cockpit, /BioXpOperatorControlTabs/);
});

test('action forms and route provenance come only from the robot catalog and admission', () => {
    for (const field of [
        'selected.inputs.map', 'selected.informational_method', 'selected.informational_path',
        'selected.source_anchor', 'selected.stages', 'selected.enabled', 'selected.disabled_reason',
        'selected.dependencies', 'admission.data?.enabled', 'source_authority_verified',
    ]) assert.ok(source.includes(field), `missing source token: ${field}`);
    assert.match(source, /catalogQuery\.data\?\.ownership_generation/);
    assert.match(client, /expected_connection_generation/);
    assert.match(client, /expected_ownership_generation/);
    assert.doesNotMatch(client, /\{ expected_generation: generation, inputs:/);
    assert.doesNotMatch(source, /api\.post\([^)]*selected\.informational_path/);
    assert.match(source, /selected\?\.safety_class === 'stop'/);
    assert.match(source, /selected\?\.safety_class === 'emergency'/);
    assert.match(source, /catalogQuery\.error \? \[\]/);
});

test('main tab has a compact live status dashboard for motion axes temperatures and pipettes', () => {
    assert.match(cockpit, /BioXpQuickDashboard/);
    for (const label of [
        'Live Robot Dashboard', 'Motion', 'Door / latch', 'Axis Analytics',
        'Temperatures', 'Pipettes', 'Motor temperature not reported',
    ]) assert.match(dashboard, new RegExp(label));
    assert.match(dashboard, /motor_temperature_available/);
    assert.match(dashboard, /position_steps/);
    assert.match(dashboard, /run_current/);
    assert.match(dashboard, /tip_loaded/);
    assert.match(dashboard, /sensor\.label/);
    assert.match(dashboard, /sensor\.unit/);
    assert.match(dashboard, /dashboard\.error \? undefined/);
    assert.doesNotMatch(`${dashboard}\n${cockpit}`, /type="password"|Login required|Authentication required/i);
});

test('browser uses fixed BMS routes and action ids, never arbitrary robot paths', () => {
    for (const routeToken of [
        '/api/bioxp/operator-controls/catalog',
        '/api/bioxp/operator-controls/dashboard',
        '/api/bioxp/operator-controls/actions/',
        '/admission',
        '/api/bioxp/operator-controls/receipts/',
        '/assessment',
    ]) assert.ok(client.includes(routeToken), `missing fixed BMS route token: ${routeToken}`);
    assert.doesNotMatch(client, /informationalPath|robotPath|targetPath/);
});

test('receipts expose machine assessment and require explicit human PASS or FAIL observations', () => {
    for (const label of [
        'machine_assessment', 'operator_assessment', 'physical_effect_verified',
        'remote_acknowledged', 'duration_ms', 'Stage receipts', 'Bounded response',
        'Your physical observation', 'Record PASS', 'Record FAIL',
    ]) assert.match(source, new RegExp(label));
    assert.match(source, /A non-empty operator observation is required/);
});
