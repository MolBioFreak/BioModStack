import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import test from 'node:test';

// Real Chromium cascade against production Mol* and shared viewer styles. This
// is a region-layout fixture, not a populated molecular-scene acceptance test.
const chrome = process.env.BMS_CHROME_BINARY ?? 'google-chrome';
const available = spawnSync(chrome, ['--version']).status === 0;
const css = readFileSync(resolve('node_modules/molstar/build/viewer/molstar.css'), 'utf8') + '\n' + readFileSync(resolve('src/structureViewer/structureViewer.css'), 'utf8');
const predictionCss = readFileSync(resolve('src/components/StructurePredictionResults.css'), 'utf8');
for (const width of [430, 900, 1440]) {
    test(`growing metric workspace reserves sibling chart flow at ${width}px`, { skip: !available }, () => {
        const dir = mkdtempSync(join(tmpdir(), 'bms-viewer-flow-'));
        try {
            const path = join(dir, 'fixture.html');
            writeFileSync(path, `<!doctype html><style>${css}\n${predictionCss}</style>
<style>body{margin:0}.prediction-embedded-canvas{position:relative}.adapter{position:relative;height:100%;min-height:540px}.bms-structure-workspace{position:relative}.msp-plugin{position:absolute;inset:0}.bms-structure-workbench{height:1280px}button{height:40px;width:220px}</style>
<div class="prediction-embedded-canvas" style="min-height:720px"><div class="bms-structure-workspace" style="height:100%"><div class="bms-structure-workspace-layout" data-workbench-visible="true"><div class="bms-structure-canvas"><div class="adapter" data-bms-molstar-adapter><div class="msp-plugin"><div class="msp-layout-standard msp-layout-standard-reactive"><div class="msp-layout-collapse-left"><div class="msp-layout-main msp-layout-region"></div></div></div></div></div></div><aside class="bms-structure-workbench">Metrics and exact atom form</aside></div></div></div>
<section class="prediction-companion"><button id="charts">Companion charts</button><div style="height:400px">Chart owner</div></section><pre id="result"></pre>
<script>const rect=s=>document.querySelector(s).getBoundingClientRect().toJSON();document.querySelector('#charts').scrollIntoView();const b=rect('#charts');document.querySelector('#result').textContent=JSON.stringify({host:rect('.prediction-embedded-canvas'),workspace:rect('.bms-structure-workspace'),charts:rect('.prediction-companion'),hit:document.elementFromPoint(b.x+b.width/2,b.y+b.height/2)?.id});</script>`);
            const html = execFileSync(chrome, ['--headless', '--no-sandbox', '--disable-gpu', '--no-proxy-server', `--user-data-dir=${join(dir, 'profile')}`, `--window-size=${width},1100`, '--dump-dom', `file://${path}`], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 30000 });
            const geometry = JSON.parse(html.match(/<pre id="result">([^<]+)<\/pre>/)![1]);
            assert.ok(geometry.host.bottom >= geometry.workspace.bottom, JSON.stringify(geometry));
            assert.ok(geometry.charts.top >= geometry.workspace.bottom, JSON.stringify(geometry));
            assert.equal(geometry.hit, 'charts');
        } finally { rmSync(dir, { recursive: true, force: true }); }
    });
}
for (const [viewport, panel, hidden] of [[430, 355, false], [1600, 620, false], [1600, 1000, false], [430, 355, true]] as const) {
    test(`embedded native regions: viewport ${viewport}, panel ${panel}, controls hidden ${hidden}`, { skip: !available }, () => {
        const dir = mkdtempSync(join(tmpdir(), 'bms-viewer-layout-'));
        try {
            const path = join(dir, 'fixture.html');
            writeFileSync(path, `<!doctype html><style>${css}</style><style>body{margin:0}.bms-structure-workspace{width:${panel}px}.mount{position:relative;flex:1;min-height:0}.adapter{display:flex;flex-direction:column;position:relative;height:100%}</style>
<div class="bms-structure-workspace" style="height:400px"><div class="bms-structure-workspace-layout"><div class="bms-structure-canvas"><div class="adapter" data-bms-molstar-adapter><div class="mount"><div class="msp-plugin"><div class="msp-layout-standard msp-layout-standard-reactive"><div class="${hidden ? 'msp-layout-hide-top msp-layout-hide-bottom msp-layout-hide-left msp-layout-hide-right' : 'msp-layout-collapse-left'}"><div class="msp-layout-main msp-layout-region"></div><div class="msp-layout-top msp-layout-region">Sequence</div><div class="msp-layout-bottom msp-layout-region">Log</div><div class="msp-layout-left msp-layout-region">Tools rail</div><div class="msp-layout-right msp-layout-region">Structure Tools</div></div></div></div></div></div></div></div></div><pre id="result"></pre>
<script>const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom}};document.querySelector('#result').textContent=JSON.stringify({main:rect('.msp-layout-main'),tools:rect('.msp-layout-right'),log:rect('.msp-layout-bottom'),host:rect('.bms-structure-workspace')});</script>`);
            const html = execFileSync(chrome, ['--headless', '--no-sandbox', '--disable-gpu', '--no-proxy-server', `--user-data-dir=${join(dir, 'profile')}`, `--window-size=${viewport},1100`, '--dump-dom', `file://${path}`], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 30000 });
            const match = html.match(/<pre id="result">([^<]+)<\/pre>/);
            assert.ok(match, 'browser returned geometry');
            const geometry = JSON.parse(match[1]);
            assert.ok(geometry.main.height >= 350, JSON.stringify(geometry));
            if (!hidden && panel < 896) {
                assert.ok(geometry.main.width >= panel - 4, JSON.stringify(geometry));
                assert.ok(geometry.log.y >= geometry.main.bottom, JSON.stringify(geometry));
                assert.ok(geometry.tools.y >= geometry.log.bottom, JSON.stringify(geometry));
                assert.ok(geometry.host.height >= geometry.tools.bottom, JSON.stringify(geometry));
            }
            console.log(JSON.stringify({ viewport, panel, hidden, ...geometry }));
        } finally { rmSync(dir, { recursive: true, force: true }); }
    });
}
