import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path: string) => readFileSync(path, 'utf8');
test('phone Dashboard reserves Settings in its flex layout without changing other workspaces', () => {
    const layout = read('src/components/Layout.tsx');
    const css = read('src/index.css');
    assert.match(layout, /data-bms-mobile-dock-flow=\{location.pathname === '\/' \? 'true' : undefined\}/u);
    const rule = css.match(/html\.bms-cordova-compact \[data-bms-mobile-dock-flow='true'\] \.bms-mobile-operations-dock\s*\{([^}]+)\}/u)?.[1] || '';
    assert.match(rule, /position:\s*static/u, 'Dashboard Settings must take space rather than cover the scrolling queue');
    assert.match(rule, /flex-shrink:\s*0/u);
});

test('phone GPU control groups cannot squeeze process names between power and fan sliders', () => {
    const telemetry = read('src/components/InfraLiveTelemetry.tsx');
    const css = read('src/index.css');
    assert.ok(telemetry.includes('data-bms-gpu-inline-controls="true"'));
    assert.ok(telemetry.includes('data-bms-gpu-process-chips="true"'));
    const rule = css.match(/html\.bms-cordova-compact \[data-bms-gpu-inline-controls='true'\]\s*\{([^}]+)\}/u)?.[1] || '';
    assert.match(rule, /grid-template-columns:\s*minmax\(0, 1fr\)/u);
    assert.match(css, /html\.bms-cordova-compact \[data-bms-gpu-process-chips='true'\][\s\S]*justify-content:\s*flex-start/u);
});
