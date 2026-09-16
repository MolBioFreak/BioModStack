import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const frontend = fileURLToPath(new URL('..', import.meta.url));

// Exercise the installed React runtime, not a substitute performance collector.
// Each child starts with a fresh timing buffer and renders identical bounded data.
const renderProbe = `
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<div id="root"></div>', { pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
const React = require('react');
const { createRoot } = require('react-dom/client');
const { flushSync } = require('react-dom');
const root = createRoot(document.getElementById('root'));
function Chart({ samples }) { return React.createElement('span', null, samples.at(-1).value); }
(async () => {
    for (let i = 0; i < 100; i++) {
        flushSync(() => root.render(React.createElement(Chart, {
            samples: Array.from({length: 180}, (_, j) => ({time: j, value: i + j})),
        })));
        await new Promise(resolve => setTimeout(resolve, 0));
    }
    console.log(JSON.stringify({text: document.body.textContent, measures: performance.getEntriesByType('measure').length}));
    root.unmount();
    dom.window.close();
})();
`;

function renderWith(mode: string): { text: string; measures: number } {
    return JSON.parse(execFileSync(process.execPath, ['-e', renderProbe], {
        cwd: frontend, env: { ...process.env, NODE_ENV: mode }, encoding: 'utf8', timeout: 30_000,
    }));
}

test('operator frontend selects non-profiling React; repeated renders retain no timing records', () => {
    const { scripts } = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
    assert.equal(scripts.dev, 'NODE_ENV=production vite');
    const selectedMode = scripts.dev.split(' ', 1)[0].split('=')[1];
    const control = renderWith('development');
    const operator = renderWith(selectedMode);
    assert.ok(control.measures >= 100, 'positive control reproduces automatic timing retention');
    assert.equal(operator.measures, 0);
    assert.equal(operator.text, control.text, 'normal renders continue without profiling');
});
