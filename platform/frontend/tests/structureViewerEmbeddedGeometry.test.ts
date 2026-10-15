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
