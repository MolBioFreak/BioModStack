import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const css = readFileSync('src/index.css', 'utf8');
const telemetry = readFileSync('src/components/InfraLiveTelemetry.tsx', 'utf8');

const semanticTokens = [
    '--surface-page',
    '--surface-card',
    '--surface-control',
    '--surface-control-strong',
    '--surface-plot',
    '--chart-grid',
    '--chart-axis',
    '--chart-legend',
    '--control-track',
    '--control-thumb',
    '--overlay-scrim',
    '--text-on-accent',
    '--shadow-card',
];

test('primary themes define the complete semantic surface and chart token contract', () => {
    for (const selector of [':root,\n[data-theme="midnight"]', '[data-theme="black"]', '[data-theme="clean_light"]']) {
        const start = css.indexOf(selector);
        assert.notEqual(start, -1, `missing theme selector ${selector}`);
        const block = css.slice(start, css.indexOf('}', start));
        for (const token of semanticTokens) {
            assert.match(block, new RegExp(`${token}:`), `${selector} must define ${token}`);
        }
    }
});

test('telemetry plots consume semantic tokens instead of fixed dark colors', () => {
    for (const token of ['--surface-plot', '--chart-grid', '--chart-axis', '--chart-legend']) {
        assert.match(telemetry, new RegExp(`var\\(${token}(?:,|\\))`));
    }
    for (const leakedDarkColor of ['rgba(51, 65, 85, 0.42)', '#cbd5e1', '#94a3b8', 'rgba(15, 23, 42, 0.22)']) {
        assert.doesNotMatch(telemetry, new RegExp(leakedDarkColor.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});

test('theme compatibility CSS does not force every white-text button to remain white', () => {
    assert.doesNotMatch(css, /button \.text-white,[\s\S]*?button\.text-white,[\s\S]*?color:\s*#ffffff\s*!important/);
});
