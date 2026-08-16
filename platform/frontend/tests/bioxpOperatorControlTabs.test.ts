import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const source = readFileSync(resolve('src/components/BioXpOperatorControlTabs.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');
const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const dashboard = readFileSync(resolve('src/components/BioXpQuickDashboard.tsx'), 'utf8');
const pipettePanel = readFileSync(resolve('src/components/BioXpPipetteControlPanel.tsx'), 'utf8');

test('catalog-driven control plane renders every action, critical groups, meta actions, and logs as separate panes', () => {
    for (const label of [
        'OEM Route Control Plane', 'Individual Controls', 'Critical Controls', 'All Individual Controls',
        'Motion Power', 'Transport / Evidence', 'Safety / Recovery', 'Initialization',
        'Meta Actions', 'Logs', 'role="tablist"', 'role="tab"', 'role="tabpanel"',
        'Run exactly this action', 'Search individual controls',
    ]) assert.match(source, new RegExp(label));
    for (const token of ['isCriticalAction', 'action.subsystem === subsystemFilter', 'authoritativeCatalog?.actions']) {
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
    assert.match(source, /authoritativeCatalog\?\.ownership_generation/);
    assert.match(client, /expected_connection_generation/);
    assert.match(client, /expected_ownership_generation/);
    assert.doesNotMatch(client, /\{ expected_generation: generation, inputs:/);
    assert.doesNotMatch(source, /api\.post\([^)]*selected\.informational_path/);
    assert.match(source, /selected\?\.safety_class === 'stop'/);
    assert.match(source, /selected\?\.safety_class === 'emergency'/);
    assert.match(source, /const authoritativeCatalog = !connected \|\| catalogQuery\.error \? undefined : catalogQuery\.data/);
    assert.match(source, /const authoritativeHistory = !connected \|\| historyQuery\.error \? undefined : historyQuery\.data/);
    assert.match(source, /const latestReceipt = connected && authoritativeCatalog && authoritativeHistory/);
    assert.match(source, /resetInvoke\(\)/);
    assert.match(source, /selected\.requires_confirmation && !confirmationMatchesCurrentAction/);
    assert.match(source, /confirmation\?\.fingerprint !== runFingerprint/);
    assert.match(source, /I confirm this exact governed action and its published machine scope/);
    assert.match(cockpit, /!configured \|\| !linkConnected \|\| updateFreshness\.isPending/);
    assert.match(cockpit, /linkConnected && catalog && !historyQuery\.isError && invokeOperatorAction\.data/);
    assert.match(cockpit, /linkConnected && catalog && !historyQuery\.isError && emergencyAction\.data/);
    assert.match(cockpit, /linkConnected && catalog && !historyQuery\.isError && recoverMotion\.data/);
    assert.match(client, /\[\.\.\.operatorHistoryKey, variables\.connectionGeneration, true\]/);
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
    assert.match(dashboard, /error !== null && error !== undefined/);
    assert.doesNotMatch(dashboard, /useBioXpOperatorDashboard\(/);
    assert.match(cockpit, /useBioXpOperatorDashboard\(generation, linkConnected\)/);
    assert.doesNotMatch(`${dashboard}\n${cockpit}`, /type="password"|Login required|Authentication required/i);
});

test('browser uses fixed BMS routes and action ids, never arbitrary robot paths', () => {
    for (const routeToken of [
        '/api/bioxp/operator-controls/catalog',
        '/api/bioxp/operator-controls/dashboard',
        '/api/bioxp/operator-controls/pipettes/application/status',
        '/api/bioxp/operator-controls/pipettes/application/plan',
        '/api/bioxp/operator-controls/actions/',
        '/admission',
        '/api/bioxp/operator-controls/receipts/',
        '/assessment',
    ]) assert.ok(client.includes(routeToken), `missing fixed BMS route token: ${routeToken}`);
    assert.doesNotMatch(client, /informationalPath|robotPath|targetPath/);
});

test('dedicated four-channel pipette surface stays plan-only and renders evidence phases', () => {
    assert.match(source, /BioXpPipetteControlPanel/);
    for (const label of [
        'Four-Channel Pipette Control', 'Channel', 'tip loaded', 'tip location',
        'Load tip workflow', 'Move to waste', 'Detect fluid', 'Plunger up', 'Plunger down',
        'Build no-motion plan', 'Safety gate', 'OEM plan evidence',
    ]) assert.match(pipettePanel, new RegExp(label, 'i'));
    for (const token of [
        'controller_acknowledged', 'completion_verified', 'physical_effect_verified',
        'motion_commanded', 'truth_source', 'live_query_performed',
    ]) assert.ok(pipettePanel.includes(token), `missing pipette evidence token: ${token}`);
    assert.doesNotMatch(pipettePanel, /execute pipette|run physical/i);
});

test('receipts expose machine assessment and require explicit human PASS or FAIL observations', () => {
    for (const label of [
        'machine_assessment', 'operator_assessment', 'physical_effect_verified',
        'remote_acknowledged', 'duration_ms', 'Stage receipts', 'Bounded response',
        'Your physical observation', 'Record PASS', 'Record FAIL',
    ]) assert.match(source, new RegExp(label));
    assert.match(source, /Operator observation must remain attached to the robot-owned receipt/);
});

test('operator observation text is bound to one immutable receipt command id', () => {
    assert.match(source, /type ReceiptBoundObservation = \{/);
    assert.match(source, /receiptCommandId: string \| null/);
    assert.match(source, /operatorObservation\.receiptCommandId === latestReceiptCommandId/);
    assert.match(source, /operatorObservation\.receiptCommandId !== latestReceipt\.command_id/);
    assert.match(source, /receiptCommandId: latestReceiptCommandId/);
});

test('generic governed action confirmation is bound to exact inputs and current authority', () => {
    assert.match(source, /type ActionConfirmation = Readonly<\{/);
    assert.match(source, /fingerprint: string/);
    assert.match(source, /function buildActionConfirmationFingerprint\(/);
    for (const token of [
        'actionId: selected.action_id',
        'inputs: normalized',
        'connectionGeneration: generation',
        'ownershipGeneration: authoritativeCatalog?.ownership_generation ?? 0',
        "registrySha256: authoritativeCatalog?.registry_sha256 ?? ''",
        "evidenceLockSha256: authoritativeCatalog?.evidence_lock_sha256 ?? ''",
        'sourceAuthorityVerified: authoritativeCatalog?.source_authority_verified === true',
    ]) assert.ok(source.includes(token), `missing confirmation authority token: ${token}`);
    assert.match(source, /confirmation\?\.fingerprint !== runFingerprint/);
    assert.match(source, /confirmationMatchesCurrentAction/);
    assert.match(source, /selected\.requires_confirmation && !confirmationMatchesCurrentAction/);
    assert.doesNotMatch(source, /confirmationAccepted/);
});
