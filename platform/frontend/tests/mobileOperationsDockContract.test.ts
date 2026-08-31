import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path: string) => readFileSync(path, 'utf8');

test('Cordova compact mode exposes one safe-area operations dock', () => {
    const ledger = read('src/components/DevIssueLedger.tsx');
    const layout = read('src/components/Layout.tsx');
    const css = read('src/index.css');

    assert.match(ledger, /data-bms-mobile-operations-dock="true"/u);
    assert.match(ledger, /fixed z-\[90\]/u);
    assert.match(ledger, />\s*Mobile Issues/u);
    assert.match(ledger, />\s*Settings/u);
    assert.match(ledger, /bms-cordova-preflight-toggle/u);
    assert.match(layout, /className="bms-layout-main/u);
    assert.match(css, /html\.bms-cordova-compact \.bms-mobile-operations-dock/u);
    assert.match(css, /env\(safe-area-inset-bottom\)/u);
    assert.match(css, /html\.bms-cordova-compact #bms-cordova-preflight-toggle[\s\S]*display:\s*none/u);
    assert.match(css, /html\.bms-cordova-compact \.bms-layout-main[\s\S]*padding-bottom/u);
    assert.match(css, /html\.bms-cordova-compact \[data-molbio-mobile-layout='true'\][\s\S]*bottom: calc\(5\.75rem[\s\S]*height: auto/u);
});

test('mobile Settings remains reachable when the development issue API is unavailable', () => {
    const ledger = read('src/components/DevIssueLedger.tsx');

    assert.match(ledger, /const issuesAvailable = issuesQuery\.data\?\.available !== false/u);
    assert.doesNotMatch(ledger, /if \(issuesQuery\.data\?\.available === false\) return null/u);
    assert.match(ledger, /\{issuesAvailable && \(/u);
    assert.match(ledger, /cordovaSettingsAvailable/u);
});

test('mobile issue reporting retains page scope inside a dedicated Mobile lane', () => {
    const ledger = read('src/components/DevIssueLedger.tsx');

    assert.match(ledger, /type DevIssueLane = 'general' \| 'mobile'/u);
    assert.match(ledger, /lane:\s*issueLane/u);
    assert.match(ledger, /params\.set\('lane', 'mobile'\)/u);
    assert.match(ledger, />Current page</u);
    assert.match(ledger, />Mobile</u);
    assert.match(ledger, />All active</u);
});

test('Cordova telemetry legends stay in flow inside their chart cards', () => {
    const telemetry = read('src/components/InfraLiveTelemetry.tsx');
    const css = read('src/index.css');

    assert.match(telemetry, /data-bms-telemetry-plot="true"/u);
    assert.match(telemetry, /data-bms-telemetry-legend="true"/u);
    assert.match(telemetry, /data-bms-telemetry-canvas="true"/u);
    assert.match(css, /html\.bms-cordova-compact \[data-bms-telemetry-legend='true'\][\s\S]*position:\s*static/u);
    assert.match(css, /html\.bms-cordova-compact \[data-bms-telemetry-canvas='true'\][\s\S]*position:\s*relative/u);
});