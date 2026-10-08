import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import {
  buildUiDiagnosticsPayload,
  resolveUiSurfaceLabel,
} from '../src/runtime/uiDiagnostics.js';

test('ui diagnostics labels Vite dev, stable hosted web, Electron, and APK surfaces distinctly', () => {
  assert.equal(resolveUiSurfaceLabel({ viteDev: true, electronShell: false, cordovaShell: false }), 'Vite dev web');
  assert.equal(resolveUiSurfaceLabel({ viteDev: false, electronShell: false, cordovaShell: false }), 'Stable hosted web');
  assert.equal(resolveUiSurfaceLabel({ viteDev: false, electronShell: true, cordovaShell: false }), 'Electron stable shell');
  assert.equal(resolveUiSurfaceLabel({ viteDev: false, electronShell: false, cordovaShell: true }), 'APK/Cordova stable shell');
});

test('ui diagnostics payload includes channel-critical details without dumping arbitrary environment', () => {
  const payload = buildUiDiagnosticsPayload({
    surfaceLabel: 'Electron stable shell',
    origin: 'http://127.0.0.1:18080',
    href: 'http://127.0.0.1:18080/bms/designs',
    routerBasename: '/bms/',
    viteMode: 'production',
    viteBaseUrl: '/bms/',
    apiHealth: 'healthy',
    shellContext: {
      runtimeMode: 'container',
      frontendOrigin: 'http://127.0.0.1:18080',
      routerBasename: '/bms/',
      windowUrl: 'http://127.0.0.1:18080/bms/',
      browserUrl: 'http://127.0.0.1:18080/bms/',
    },
  });

  assert.match(payload.text, /Surface: Electron stable shell/);
  assert.match(payload.text, /Origin: http:\/\/127\.0\.0\.1:18080/);
  assert.match(payload.text, /Router basename: \/bms\//);
  assert.match(payload.text, /API health: healthy/);
  assert.match(payload.text, /Shell runtime: container/);
  assert.doesNotMatch(payload.text, /SECRET|TOKEN|PASSWORD|KEY=/i);
});

test('layout exposes one far-left diagnostics top-bar entry with copy support', () => {
  const layoutSource = fs.readFileSync(path.join(process.cwd(), 'src', 'components', 'Layout.tsx'), 'utf8');

  assert.match(layoutSource, /<DiagnosticsMenu/);
  assert.match(layoutSource, /Diagnostics/);
  assert.match(layoutSource, /Surface \+ API/);
  assert.match(layoutSource, /Runtime channel/);
  assert.match(layoutSource, /Switch \+ start/);
  assert.match(layoutSource, /Switch to Vite dev/);
  assert.match(layoutSource, /Switch to stable \/bms\//);
  assert.match(layoutSource, /navigator\.clipboard\.writeText/);
  assert.match(layoutSource, /window\.biomodstack\.getShellContext/);
  assert.match(layoutSource, /window\.biomodstack\.switchRuntime/);
  assert.match(layoutSource, /\/api\/system\/runtime-ports/);
  assert.match(layoutSource, /\/api\/system\/runtime\/start-target/);
  assert.match(layoutSource, /\/api\/health/);
  assert.match(layoutSource, /data-bms-topbar-left="true"/);
  assert.match(layoutSource, /data-bms-primary-nav-rail="true"/);
  assert.match(layoutSource, /data-bms-topbar-utilities="true"/);

  assert.match(layoutSource, /flex min-w-0 flex-wrap items-center/);
  assert.match(layoutSource, /2xl:flex-nowrap/);
  assert.match(layoutSource, /order-2 min-w-0 w-full overflow-x-auto/);
  assert.match(layoutSource, /2xl:order-2 2xl:w-auto 2xl:flex-1/);
  assert.match(layoutSource, /order-2 ml-auto flex max-w-\[calc\(100vw-1rem\)\]/);
  assert.match(layoutSource, /flex-wrap items-center justify-end gap-1\.5 overflow-visible/);
  assert.match(layoutSource, /fixed right-2 top-12 w-\[min\(1060px,calc\(100vw-1rem\)\)\]/);
  assert.match(layoutSource, /max-h-\[calc\(100vh-4rem\)\] overflow-y-auto/);
  assert.match(layoutSource, /2xl:order-3/);
  assert.match(layoutSource, /overflow-x-auto overscroll-x-contain/);
  assert.match(layoutSource, /cursor-grab active:cursor-grabbing/);
  assert.match(layoutSource, /touchAction: 'pan-x'/);
  assert.doesNotMatch(layoutSource, /data-bms-topbar-utilities="true"[\s\S]{0,240}overflow-x-auto/);
  assert.match(layoutSource, /onPointerDown=\{handlePointerDown\}/);
  assert.match(layoutSource, /onClickCapture=\{handleClickCapture\}/);
  assert.match(layoutSource, /data-bms-drag-scroll-ignore="true"/);
  assert.match(layoutSource, /scrollIntoView\(\{ block: 'nearest', inline: 'center' \}\)/);
  assert.doesNotMatch(layoutSource, /Surface, channel, shell, and API health summary/);
  assert.doesNotMatch(layoutSource, /Switch web\/Electron surfaces by reloading onto the selected local channel\./);

  const diagnosticsIndex = layoutSource.indexOf('<DiagnosticsMenu');
  const logoIndex = layoutSource.indexOf('{/* Logo / Brand */}');
  assert.ok(diagnosticsIndex > -1 && logoIndex > -1 && diagnosticsIndex < logoIndex);
});

test('theme selector marks floating surfaces so top-bar drag scrolling does not hijack dropdown interaction', () => {
  const themeSelectorSource = fs.readFileSync(path.join(process.cwd(), 'src', 'components', 'ThemeSelector.tsx'), 'utf8');

  assert.match(themeSelectorSource, /data-bms-drag-scroll-ignore="true"/);
  assert.match(themeSelectorSource, /className="relative shrink-0"/);
});
