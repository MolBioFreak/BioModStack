import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';
import * as prepareAssets from '../scripts/prepare-bms-assets.mjs';

import {
  buildBundleDescriptor,
  buildMobileShellCss,
  buildMobileShellScript,
  buildPreflightCss,
  buildPreflightScript,
  buildRuntimeConfigScript,
  buildUiUpdateManifestPath,
  buildShimScript,
  buildUpdateLoaderScript,
  normalizeUiUpdateChannel,
  normalizeConfig,
  patchIndexHtmlContent,
} from '../scripts/prepare-bms-assets.mjs';

test('normalizeConfig fills in phone-friendly mobile viewport defaults while preserving extra pinch-zoom-out headroom', () => {
  const config = normalizeConfig({
    frontendCheckout: '/tmp/frontend',
    apiBaseUrl: 'https://example.test///',
  });

  assert.deepEqual(config, {
    frontendCheckout: '/tmp/frontend',
    apiBaseUrl: 'https://example.test',
    remoteUiUrl: '',
    routerBasename: '/',
    mobileInitialScale: 0.82,
    mobileMinimumScale: 0.25,
    mobileMaximumScale: 3,
    mobileCompactMode: true,
    uiUpdateChannel: 'phone',
    uiUpdateManifestPath: '/api/mobile-ui/channels/phone/manifest',
    shellApiVersion: 1,
    bundledUiVersion: 'bundled',
  });
  assert.ok(config.mobileMinimumScale < config.mobileInitialScale);
});

test('normalizeConfig accepts only an exact HTTPS live UI origin', () => {
  const config = normalizeConfig({
    frontendCheckout: '/tmp/frontend',
    apiBaseUrl: 'https://example.test',
    remoteUiUrl: 'https://live.example.test/',
  });
  assert.equal(config.remoteUiUrl, 'https://live.example.test/');
  assert.throws(() => normalizeConfig({
    frontendCheckout: '/tmp/frontend',
    apiBaseUrl: 'https://example.test',
    remoteUiUrl: 'https://live.example.test/path',
  }), /exact HTTPS origin/);
});

test('normalizeConfig derives safe APK UI update manifest paths from explicit channels', () => {
  assert.equal(normalizeUiUpdateChannel('dev-fast_01'), 'dev-fast_01');
  assert.equal(normalizeUiUpdateChannel('../bad/channel', 'phone'), 'phone');
  assert.equal(buildUiUpdateManifestPath('dev-fast_01'), '/api/mobile-ui/channels/dev-fast_01/manifest');

  const config = normalizeConfig({
    frontendCheckout: '/tmp/frontend',
    apiBaseUrl: 'https://example.test',
    uiUpdateChannel: 'dev-fast_01',
  });

  assert.equal(config.uiUpdateChannel, 'dev-fast_01');
  assert.equal(config.uiUpdateManifestPath, '/api/mobile-ui/channels/dev-fast_01/manifest');
});

test('buildRuntimeConfigScript exposes defaults and local override storage for the APK shell', () => {
  const script = buildRuntimeConfigScript({
    frontendCheckout: '/tmp/frontend',
    apiBaseUrl: 'https://example.test',
    routerBasename: '/',
    uiUpdateChannel: 'phone',
    uiUpdateManifestPath: '/api/mobile-ui/channels/phone/manifest',
    mobileInitialScale: 0.72,
    mobileMinimumScale: 0.55,
    mobileMaximumScale: 3,
    mobileCompactMode: true,
  });

  assert.match(script, /__BMS_CORDOVA_DEFAULT_RUNTIME__/);
  assert.match(script, /bms\.cordova\.runtimeOverrides/);
  assert.match(script, /__BMS_CORDOVA_RUNTIME_OVERRIDES__/);
  assert.match(script, /runtime\.apiBaseUrl = overrides\.apiBaseUrl/);
  assert.match(script, /runtime\.uiUpdateChannel = normalizeUiUpdateChannel/);
  assert.match(script, /runtime\.uiUpdateManifestPath = buildUiUpdateManifestPath/);
  assert.doesNotThrow(() => new vm.Script(script));
});

test('buildRuntimeConfigScript normalizes Cordova index.html before React Router starts', () => {
  const script = buildRuntimeConfigScript({
    frontendCheckout: '/tmp/frontend',
    apiBaseUrl: 'https://example.test',
    routerBasename: '/',
    uiUpdateChannel: 'phone',
    uiUpdateManifestPath: '/api/mobile-ui/channels/phone/manifest',
  });
  const replacements = [];
  const context = {
    localStorage: { getItem: () => null },
    window: {
      location: { pathname: '/index.html', search: '?from=apk', hash: '#dashboard' },
      history: {
        state: { shell: true },
        replaceState: (...args) => replacements.push(args),
      },
    },
  };

  vm.createContext(context);
  new vm.Script(script).runInContext(context);

  assert.deepEqual(replacements, [[{ shell: true }, '', '/?from=apk#dashboard']]);
});

test('production builds resolve Molstar through its cycle-safe CommonJS distribution', async () => {
  const viteSource = await readFile(new URL('../../frontend/vite.config.ts', import.meta.url), 'utf8');

  assert.match(viteSource, /name:\s*'bms-molstar-commonjs-build-resolver'/);
  assert.match(viteSource, /apply:\s*'build'/);
  assert.match(viteSource, /'molstar\/lib\/commonjs\/'/);
  assert.match(viteSource, /preserveEntrySignatures:\s*false/);
  assert.doesNotMatch(viteSource, /preserveModules/);
  assert.match(viteSource, /output:\s*\{\s*manualChunks,\s*\}/s);
});


test('buildRuntimeConfigScript applies only sanitized APK UI update channel overrides', () => {
  const script = buildRuntimeConfigScript({
    frontendCheckout: '/tmp/frontend',
    apiBaseUrl: 'https://example.test',
    routerBasename: '/',
    uiUpdateChannel: 'phone',
    uiUpdateManifestPath: '/api/mobile-ui/channels/phone/manifest',
  });
  const storage = new Map([
    ['bms.cordova.runtimeOverrides', JSON.stringify({ uiUpdateChannel: 'dev-fast_01' })],
  ]);
  const context = {
    window: {},
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
    },
  };

  vm.createContext(context);
  new vm.Script(script).runInContext(context);

  assert.equal(context.window.__BMS_CORDOVA_RUNTIME__.uiUpdateChannel, 'dev-fast_01');
  assert.equal(
    context.window.__BMS_CORDOVA_RUNTIME__.uiUpdateManifestPath,
    '/api/mobile-ui/channels/dev-fast_01/manifest',
  );
});

test('buildShimScript rewrites same-origin absolute /api URLs so Molstar can load structures inside the Cordova localhost shell', () => {
  const script = buildShimScript();
  const calls = [];
  const context = {
    URL,
    console,
    navigator: {},
    window: {
      __BMS_CORDOVA_RUNTIME__: { apiBaseUrl: 'https://compute-node.taileb3a90.ts.net' },
      location: {
        origin: 'https://localhost',
        href: 'https://localhost/index.html',
      },
      fetch(url) {
        calls.push(url);
        return url;
      },
    },
  };
  context.window.window = context.window;

  vm.createContext(context);
  assert.doesNotThrow(() => new vm.Script(script).runInContext(context));

  context.window.fetch('/api/health');
  context.window.fetch('https://localhost/api/designs/123/pdb?download=1#viewer');
  context.window.fetch('https://example.test/static/model.pdb');

  assert.deepEqual(calls, [
    'https://compute-node.taileb3a90.ts.net/api/health',
    'https://compute-node.taileb3a90.ts.net/api/designs/123/pdb?download=1#viewer',
    'https://example.test/static/model.pdb',
  ]);
});

test('buildMobileShellScript wires the mobile viewport and keeps pinch-zoom-out headroom below the readable launch scale', () => {
  const script = buildMobileShellScript();

  assert.match(script, /initial-scale=/);
  assert.match(script, /minimum-scale=/);
  assert.match(script, /viewport-fit=cover/);
  assert.match(script, /user-scalable=yes/);
  assert.match(script, /bms-cordova-shell/);
  assert.match(script, /classList\.toggle\('bms-cordova-compact'/);
  assert.match(script, /clampNumber\(runtime\.mobileInitialScale, 0\.55, 1\.1, 0\.82\)/);
  assert.match(script, /clampNumber\(runtime\.mobileMinimumScale, 0\.25, 1\.1, 0\.25\)/);
  assert.match(script, /Math\.min\(minimumScale, initialScale\)/);
});

test('buildMobileShellCss tightens the BioModStack chrome for phone widths and oversized side panels', () => {
  const css = buildMobileShellCss();

  assert.match(css, /min-w-\[420px\]/);
  assert.match(css, /w-\[520px\]/);
  assert.match(css, /min-w-\[9\.5rem\]/);
  assert.match(css, /sequence-library/);
  assert.match(css, /min-w-\[280px\]/);
  assert.match(css, /max-w-\[400px\]/);
});

test('buildBundleDescriptor extracts versioned entry assets from a Vite index document', () => {
  const html = [
    '<!doctype html>',
    '<html lang="en">',
    '  <head>',
    '    <link rel="stylesheet" href="./assets/index-abc.css">',
    '    <link rel="modulepreload" href="./assets/chunk-def.js">',
    '    <script type="module" src="./assets/index-ghi.js"></script>',
    '  </head>',
    '</html>',
  ].join('\n');

  assert.deepEqual(buildBundleDescriptor(html, { version: '2026.04.20-phone-01', shellApiVersion: 1 }), {
    version: '2026.04.20-phone-01',
    shellApiVersion: 1,
    entryCss: ['assets/index-abc.css'],
    entryJs: ['assets/index-ghi.js'],
  });
});

test('buildUpdateLoaderScript exposes fallback boot and readiness hooks for downloaded UI bundles', () => {
  const script = buildUpdateLoaderScript();

  assert.match(script, /__BMS_CORDOVA_CONFIRM_READY__/);
  assert.match(script, /__BMS_CORDOVA_BOOT_UI__/);
  assert.match(script, /__BMS_CORDOVA_UI_BOOT_STATUS__/);
  assert.match(script, /__BMS_CORDOVA_BUNDLED_DESCRIPTOR__/);
  assert.match(script, /__bms_ui__\//);
  assert.match(script, /bms-cordova-remote-ui/);
  assert.match(script, /bootRemoteUi\(remoteUiUrl\)/);
  assert.match(script, /BioModStack live UI/);
  assert.match(script, /DOMContentLoaded/);
  assert.doesNotThrow(() => new vm.Script(script));
});

test('buildPreflight assets expose endpoint, manual update, and rollback controls with persistent override flow', () => {
  const script = buildPreflightScript();
  const css = buildPreflightCss();

  assert.match(script, /Pre-flight settings/);
  assert.match(script, /Save \+ reload/);
  assert.match(script, /Test connection/);
  assert.match(script, /Check UI update/);
  assert.match(script, /Update UI/);
  assert.match(script, /UI update channel/);
  assert.match(script, /data-role="ui-update-channel"/);
  assert.match(script, /UI bundle updates/);
  assert.match(script, /Native APK update/);
  assert.match(script, /window\.BmsAndroidUpdater/);
  assert.match(script, /JSON\.stringify\(\{ action: command \}\)/);
  assert.match(script, /biomodstack-apk-update-state/);
  assert.match(script, /checkForApkUpdate/);
  assert.match(script, /installApkUpdate/);
  assert.doesNotMatch(script, /downloadUpdate/);
  assert.doesNotMatch(script, /openUnknownSourcesSettings/);
  assert.match(script, /The APK never points at Vite dev automatically/);
  assert.match(script, /Revert to bundled UI/);
  assert.match(script, /window\.__BMS_CORDOVA_OPEN_PREFLIGHT__/);
  assert.doesNotThrow(() => new vm.Script(script));
  assert.match(css, /bms-cordova-preflight/);
  assert.match(css, /bms-cordova-preflight-toggle/);
});

test('native APK state reducer rejects malformed and stale events and normalizes valid bounded state', () => {
  assert.equal(typeof prepareAssets.reduceNativeApkState, 'function');
  const reduce = prepareAssets.reduceNativeApkState;
  const manifest = {
    channel: 'stable',
    versionCode: 201,
    versionName: '0.2.1',
    minSdk: 24,
    sizeBytes: 1024,
    publishedAt: '2026-07-18T12:00:00Z',
    changelog: ['Security update'],
  };
  assert.deepEqual(reduce(0, { sequence: 1, status: 'available', message: 'Ready', manifest }), {
    accepted: true,
    sequence: 1,
    status: 'available',
    message: 'Ready',
    tone: 'success',
    manifest,
  });
  for (const malformed of [
    null,
    { sequence: 1, status: 'unknown', message: 'bad' },
    { sequence: 1, status: 'available', message: '' },
    { sequence: 1, status: 'available', message: 'bad', manifest: { ...manifest, channel: 'beta' } },
    { sequence: 1, status: 'available', message: 'bad', manifest: { ...manifest, changelog: ['x'.repeat(1001)] } },
  ]) {
    assert.equal(reduce(0, malformed).accepted, false);
  }
  assert.equal(reduce(1, { sequence: 1, status: 'checking', message: 'stale' }).accepted, false);
  assert.equal(reduce(2, { sequence: 1, status: 'checking', message: 'older' }).accepted, false);
  assert.equal(reduce(0, { sequence: 1, status: 'installer_opened', message: 'Pending', manifest }).tone, 'pending');
});

test('patchIndexHtmlContent injects the mobile shell and preflight assets exactly once and preserves runtime-specific viewport scaling', () => {
  const input = [
    '<!doctype html>',
    '<html lang="en">',
    '  <head>',
    '    <meta charset="UTF-8" />',
    '    <meta name="viewport" content="width=device-width, initial-scale=1.0" />',
    '    <script type="module" src="./assets/index.js"></script>',
    '  </head>',
    '  <body>',
    '    <div id="root"></div>',
    '  </body>',
    '</html>',
  ].join('\n');

  const runtimeConfig = {
    mobileInitialScale: 0.55,
    mobileMinimumScale: 0.25,
    mobileMaximumScale: 3,
  };
  const once = patchIndexHtmlContent(input, runtimeConfig);
  const twice = patchIndexHtmlContent(once, runtimeConfig);

  assert.match(once, /initial-scale=0\.55/);
  assert.match(once, /minimum-scale=0\.25/);
  assert.match(once, /maximum-scale=3\.00/);
  assert.match(once, /user-scalable=yes/);
  assert.equal((once.match(/bms-cordova-mobile-shell\.js/g) || []).length, 1);
  assert.equal((once.match(/bms-cordova-mobile-shell\.css/g) || []).length, 1);
  assert.equal((once.match(/bms-cordova-preflight\.js/g) || []).length, 1);
  assert.equal((once.match(/bms-cordova-preflight\.css/g) || []).length, 1);
  assert.equal((twice.match(/bms-cordova-mobile-shell\.js/g) || []).length, 1);
  assert.equal((twice.match(/bms-cordova-mobile-shell\.css/g) || []).length, 1);
  assert.equal((twice.match(/bms-cordova-preflight\.js/g) || []).length, 1);
  assert.equal((twice.match(/bms-cordova-preflight\.css/g) || []).length, 1);
});
