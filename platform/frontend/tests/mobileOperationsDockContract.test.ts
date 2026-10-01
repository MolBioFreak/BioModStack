import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path: string) => readFileSync(path, 'utf8');

test('Cordova compact mode retains Settings without passive issue collection', () => {
    const settings = read('src/components/MobilePreflightSettings.tsx');
    const layout = read('src/components/Layout.tsx');
    const css = read('src/index.css');
    assert.match(settings, /data-bms-mobile-operations-dock="true"/u);
    assert.match(settings, />\s*Settings/u);
    assert.match(settings, /bms-cordova-preflight-toggle/u);
    assert.doesNotMatch(settings, /fetch\(|document\.body|Issues/u);
    assert.doesNotMatch(layout, /DevIssueLedger/u);
    assert.match(css, /html\.bms-cordova-compact \.bms-mobile-operations-dock/u);
});

test('Cordova telemetry legends stay in flow inside their chart cards', () => {
    const telemetry = read('src/components/InfraLiveTelemetry.tsx');
    const plot = read('src/components/telemetryMetricPlot.tsx');
    const css = read('src/index.css');

    assert.match(telemetry, /import \{ TimeSeriesPlot \} from '\.\/telemetryMetricPlot'/u);
    assert.match(plot, /data-bms-telemetry-plot="true"/u);
    assert.match(plot, /data-bms-telemetry-legend="true"/u);
    assert.match(plot, /data-bms-telemetry-inspector="true"/u);
    assert.match(css, /html\.bms-cordova-compact \[data-bms-telemetry-legend='true'\][\s\S]*position:\s*static/u);
    assert.match(css, /html\.bms-cordova-compact \[data-bms-telemetry-canvas='true'\][\s\S]*position:\s*relative/u);
});