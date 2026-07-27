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

function exactSelectionReceipt(environment) {
  const runtimeRevision = '6'.repeat(40);
  const build = { revision: runtimeRevision, build_id: 'build-1', build_time: '2026-07-26T00:00:00Z' };
  const healthPayload = {
    build,
    status: 'healthy',
    liveness: { alive: true, status: 'alive' },
    readiness: { ready: true },
  };
  const containerProcessReport = (name, marker, pid, containerPid = 1) => {
    const api = name === 'biomodstack-api';
    const master = containerPid === 1;
    return {
      pid,
      cgroup: `0::/system.slice/docker-${marker.repeat(64)}.scope`,
      container_pid: containerPid,
      parent_container_pid: master ? 0 : 1,
      executable: api ? '/usr/local/bin/python3.10' : '/usr/sbin/nginx',
      argv: api
        ? ['/app/platform/api/.venv/bin/python', '/app/platform/api/.venv/bin/uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000']
        : [master ? 'nginx: master process nginx -g daemon off;' : 'nginx: worker process'],
      cwd: api ? '/app/platform/api' : '/',
      uid: api ? 1000 : (master ? 0 : 101),
    };
  };
  const container = (name, marker, pid) => ({
    name,
    container_id: marker.repeat(64),
    revision: runtimeRevision,
    compose_working_dir: '/srv/biomodstack',
    pid,
    cgroup: `0::/system.slice/docker-${marker.repeat(64)}.scope`,
    image_id: name === 'biomodstack-api'
      ? 'sha256:74bf34e32e2f5d0f72d3f6d117c1b4877c169e7a62c0da06ea05b75d5e0cd12c'
      : 'sha256:7e79b645349216a2457cd2f64af53beb26d9041c7911ed8438d6708239017c3e',
    cmdline: name === 'biomodstack-api'
      ? '/bin/sh -ec /app/platform/api/.venv/bin/python run_migrations.py && exec /app/platform/api/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000'
      : '/docker-entrypoint.sh nginx -g daemon off;',
    cwd: name === 'biomodstack-api' ? '/app/platform/api' : '/',
    host_pids: [pid],
    process_reports: [containerProcessReport(name, marker, pid)],
  });
  const processReport = (pid, marker, overrides = {}) => ({
    pid,
    cwd: '/app/platform/api',
    cmdline: 'uvicorn main:app',
    argv: ['/app/platform/api/.venv/bin/python', '-m', 'uvicorn', 'main:app'],
    executable: '/usr/bin/python3',
    cgroup: `0::/system.slice/docker-${marker.repeat(64)}.scope`,
    build_revision: null,
    ...overrides,
  });
  const containerListener = (name, marker, port, pid, inode) => ({
    container_name: name,
    container_id: marker.repeat(64),
    port,
    bind_addresses: ['127.0.0.1'],
    container_listener_pids: [1],
    listener_pid_map: [{ container_pid: 1, host_pid: pid }],
    host_listener_pids: [pid],
    listener_inodes: [inode],
    listener_inode_owners: { [inode]: [pid] },
    container_host_pids: [pid],
    runtime_image_id: name === 'biomodstack-api'
      ? 'sha256:74bf34e32e2f5d0f72d3f6d117c1b4877c169e7a62c0da06ea05b75d5e0cd12c'
      : 'sha256:7e79b645349216a2457cd2f64af53beb26d9041c7911ed8438d6708239017c3e',
    runtime_cmdline: name === 'biomodstack-api'
      ? '/bin/sh -ec /app/platform/api/.venv/bin/python run_migrations.py && exec /app/platform/api/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000'
      : '/docker-entrypoint.sh nginx -g daemon off;',
    runtime_cwd: name === 'biomodstack-api' ? '/app/platform/api' : '/',
    listener_reports: [containerProcessReport(name, marker, pid)],
  });
  const apiListener = containerListener('biomodstack-api', '1', 8000, 101, 80001);
  const adapterReport = processReport(301, '9', {
    cwd: '/srv/selector/platform/api',
    cmdline: '/srv/selector/platform/api/.venv/bin/python /srv/selector/platform/api/.venv/bin/uvicorn workflow_adapter_app:app --port 8001 --host 127.0.0.1 --no-proxy-headers --no-access-log',
    argv: [
      '/srv/selector/platform/api/.venv/bin/python',
      '/srv/selector/platform/api/.venv/bin/uvicorn',
      'workflow_adapter_app:app', '--port', '8001', '--host', '127.0.0.1',
      '--no-proxy-headers', '--no-access-log',
    ],
    executable: '/srv/selector/platform/api/.venv/bin/python',
    cgroup: '0::/user.slice/user-1000.slice/user@1000.service/app.slice/biomodstack-workflow-adapter.service',
    build_revision: runtimeRevision,
  });
  const receipt = {
    selected_environment: environment,
    frontend_target: environment === 'development' ? 'http://127.0.0.1:5173' : 'http://127.0.0.1:18080/bms/',
    api_health_target: 'http://127.0.0.1:8000/api/health',
    serve_root_proxy: environment === 'development' ? 'http://127.0.0.1:5173' : 'http://127.0.0.1:18081',
    runtime_mode: environment === 'development' ? 'dev' : 'container',
    runtime_target: environment === 'development' ? 'dev' : 'prod',
    tailnet_origin: 'https://compute-node.taileb3a90.ts.net',
    project_root: '/srv/selector',
    project_revision: runtimeRevision,
    selector_revision: 'a'.repeat(40),
    serve_handlers: {
      '/': { Proxy: environment === 'development' ? 'http://127.0.0.1:5173' : 'http://127.0.0.1:18081' },
      '/api/tailnet-environment': { Proxy: 'http://127.0.0.1:8001' },
    },
    managed_api_runtime: {
      validated_revision: runtimeRevision,
      validated_compose_root: '/srv/biomodstack',
      containers: [container('biomodstack-api', '1', 101)],
    },
    managed_api_listener: apiListener,
    api_listeners: apiListener.listener_reports,
    workflow_adapter_listener: {
      port: 8001,
      bind_addresses: ['127.0.0.1'],
      listener_inodes: [80011],
      listener_inode_owners: { 80011: [301] },
      listener_reports: [adapterReport],
      systemd_service: 'biomodstack-workflow-adapter.service',
      source_root: '/srv/selector',
      source_revision: 'a'.repeat(40),
    },
    health: {
      local_frontend: { status: 200 },
      tailnet_frontend: { status: 200 },
      local_api: { status: 200, payload: { ...healthPayload } },
      tailnet_api: {
        status: 200,
        payload: { ...healthPayload, build: { ...build }, liveness: { ...healthPayload.liveness }, readiness: { ...healthPayload.readiness } },
      },
    },
  };
  if (environment === 'production') {
    receipt.container_runtime = {
      validated_revision: runtimeRevision,
      validated_compose_root: '/srv/biomodstack',
      containers: [container('biomodstack-api', '1', 101), container('biomodstack-web', '2', 202)],
    };
    const webListener = containerListener('biomodstack-web', '2', 18080, 202, 180801);
    receipt.managed_frontend_listener = webListener;
    receipt.frontend_listeners = webListener.listener_reports;
    receipt.tailnet_production_proxy = {
      container_id: '3'.repeat(64),
      image: 'nginx@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10',
      image_id: 'sha256:6769dc3a703c719c1d2756bda113659be28ae16cf0da58dd5fd823d6b9a050ea',
      config_path: '/srv/selector/docker/tailnet-production-proxy.conf',
      config_sha256: '2c5943ce3ae5fa2ca35cd0a094a90c42b8e38b71c85c1fae58d2afe392082b62',
      listener_port: 18081,
      pid: 303,
      listener_pids: [303, 304],
      container_listener_pids: [1, 27],
      listener_pid_map: [
        { container_pid: 1, host_pid: 303 },
        { container_pid: 27, host_pid: 304 },
      ],
      listener_inodes: [180811, 180812],
      listener_inode_owners: { 180811: [303], 180812: [304] },
      container_host_pids: [303, 304],
      listener_reports: [
        containerProcessReport('biomodstack-tailnet-production-proxy', '3', 303, 1),
        containerProcessReport('biomodstack-tailnet-production-proxy', '3', 304, 27),
      ],
      cgroup: `0::/system.slice/docker-${'3'.repeat(64)}.scope`,
      cmdline: '/docker-entrypoint.sh nginx -g daemon off;',
      cwd: '/',
    };
    receipt.tailnet_production_proxy_listeners = receipt.tailnet_production_proxy.listener_reports;
  } else {
    const frontendReport = processReport(201, '8', {
      cwd: '/srv/selector/platform/frontend',
      cmdline: '/usr/bin/node /srv/selector/platform/frontend/node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173',
      argv: ['/usr/bin/node', '/srv/selector/platform/frontend/node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5173'],
      executable: '/usr/bin/node',
      cgroup: '0::/user.slice/user-1000.slice/user@1000.service/app.slice/biomodstack-frontend.service',
      build_revision: 'a'.repeat(40),
    });
    receipt.development_frontend_listener = {
      port: 5173,
      bind_addresses: ['127.0.0.1'],
      listener_inodes: [51731],
      listener_inode_owners: { 51731: [201] },
      listener_reports: [frontendReport],
      systemd_service: 'biomodstack-frontend.service',
      source_root: '/srv/selector/platform/frontend',
      source_revision: 'a'.repeat(40),
    };
    receipt.frontend_listeners = [frontendReport];
  }
  return receipt;
}

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
  assert.match(script, /awaiting-environment-selection/);
  assert.match(script, /__BMS_CORDOVA_SELECT_AND_BOOT_REMOTE_UI__/);
  assert.doesNotMatch(script, /__BMS_CORDOVA_BOOT_REMOTE_UI__/);
  assert.match(script, /BioModStack live UI/);
  assert.match(script, /DOMContentLoaded/);
  assert.doesNotThrow(() => new vm.Script(script));
});

test('remote-live loader clears stored bundles and cannot mount an iframe before successful selection', async () => {
  const script = buildUpdateLoaderScript();
  const elements = [];
  const storage = new Map([['bms.cordova.uiBundleState', JSON.stringify({
    descriptor: { version: 'old', shellApiVersion: 1, entryCss: [], entryJs: ['assets/old.js'] },
    basePath: '/__bms_ui__/active/',
  })]]);
  const document = {
    head: { appendChild: (element) => elements.push(element) },
    body: { appendChild: (element) => { element.isConnected = true; elements.push(element); } },
    querySelector: () => null,
    getElementById: (id) => elements.find((element) => element.id === id) || null,
    addEventListener: () => {},
    createElement: (tagName) => ({
      tagName,
      id: '',
      isConnected: false,
      style: {},
      setAttribute() {},
      addEventListener() {},
    }),
  };
  const context = {
    URL,
    Date,
    document,
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      removeItem: (key) => storage.delete(key),
    },
    setTimeout,
    clearTimeout,
    fetch: async () => ({ ok: false, status: 503, json: async () => ({ detail: 'blocked' }) }),
    window: {
      __BMS_CORDOVA_RUNTIME__: {
        apiBaseUrl: 'https://compute-node.taileb3a90.ts.net',
        remoteUiUrl: 'https://compute-node.taileb3a90.ts.net/',
      },
    },
  };
  context.window.window = context.window;
  vm.createContext(context);
  new vm.Script(script).runInContext(context);

  assert.equal(storage.has('bms.cordova.uiBundleState'), false);
  assert.equal(elements.filter((element) => element.tagName === 'iframe').length, 0);
  context.window.__BMS_CORDOVA_BOOT_UI__({
    version: 'bypass',
    shellApiVersion: 1,
    entryCss: [],
    entryJs: ['assets/bypass.js'],
  });
  assert.equal(elements.filter((element) => element.tagName === 'script').length, 0);
  await assert.rejects(
    context.window.__BMS_CORDOVA_SELECT_AND_BOOT_REMOTE_UI__(
      'https://compute-node.taileb3a90.ts.net',
      'development',
    ),
    /blocked/,
  );
  assert.equal(elements.filter((element) => element.tagName === 'iframe').length, 0);

  context.fetch = async () => ({
    ok: true,
    status: 200,
    json: async () => exactSelectionReceipt('development'),
  });
  await context.window.__BMS_CORDOVA_SELECT_AND_BOOT_REMOTE_UI__(
    'https://compute-node.taileb3a90.ts.net',
    'development',
  );
  assert.equal(elements.filter((element) => element.tagName === 'iframe').length, 1);
});

test('selection response contract accepts exact development and production receipts and rejects cross-environment identities', () => {
  const receipt = exactSelectionReceipt;

  assert.doesNotThrow(() => prepareAssets.validateTailnetSelectionPayload(
    receipt('development'), 'development', 'https://compute-node.taileb3a90.ts.net',
  ));
  assert.doesNotThrow(() => prepareAssets.validateTailnetSelectionPayload(
    receipt('production'), 'production', 'https://compute-node.taileb3a90.ts.net',
  ));
  const fractionalTimestamp = receipt('production');
  fractionalTimestamp.health.local_api.payload.build.build_time = '2026-07-27T05:29:07.904235Z';
  fractionalTimestamp.health.tailnet_api.payload.build.build_time = '2026-07-27T05:29:07.904235Z';
  assert.doesNotThrow(() => prepareAssets.validateTailnetSelectionPayload(
    fractionalTimestamp, 'production', 'https://compute-node.taileb3a90.ts.net',
  ));

  const multiWorker = receipt('production');
  const webContainer = multiWorker.container_runtime.containers.find(
    (item) => item.name === 'biomodstack-web',
  );
  const webListener = multiWorker.managed_frontend_listener;
  webContainer.host_pids = [202, 203];
  const webMasterReport = webContainer.process_reports[0];
  const webWorkerReport = {
    ...webMasterReport,
    pid: 203,
    container_pid: 27,
    parent_container_pid: 1,
    argv: ['nginx: worker process'],
    uid: 101,
  };
  webContainer.process_reports = [webMasterReport, webWorkerReport];
  webListener.container_listener_pids = [1, 27];
  webListener.listener_pid_map = [
    { container_pid: 1, host_pid: 202 },
    { container_pid: 27, host_pid: 203 },
  ];
  webListener.host_listener_pids = [202, 203];
  webListener.listener_inode_owners = { 180801: [202, 203] };
  webListener.container_host_pids = [202, 203];
  webListener.listener_reports = [webMasterReport, webWorkerReport];
  multiWorker.frontend_listeners = webListener.listener_reports;
  assert.doesNotThrow(() => prepareAssets.validateTailnetSelectionPayload(
    multiWorker, 'production', 'https://compute-node.taileb3a90.ts.net',
  ));
  const swappedWorkerMap = structuredClone(multiWorker);
  swappedWorkerMap.managed_frontend_listener.listener_pid_map = [
    { container_pid: 1, host_pid: 203 },
    { container_pid: 27, host_pid: 202 },
  ];
  assert.throws(() => prepareAssets.validateTailnetSelectionPayload(
    swappedWorkerMap, 'production', 'https://compute-node.taileb3a90.ts.net',
  ));

  const rogueWebOwner = structuredClone(multiWorker);
  const rogueWebContainer = rogueWebOwner.container_runtime.containers.find(
    (item) => item.name === 'biomodstack-web',
  );
  const rogueWebListener = rogueWebOwner.managed_frontend_listener;
  const rogueWebReport = {
    ...rogueWebContainer.process_reports[1],
    pid: 999,
    container_pid: 99,
    executable: '/bin/sh',
    argv: ['/bin/sh', '-c', 'rogue'],
  };
  rogueWebContainer.host_pids.push(999);
  rogueWebContainer.process_reports.push(rogueWebReport);
  rogueWebListener.container_listener_pids.push(99);
  rogueWebListener.listener_pid_map.push({ container_pid: 99, host_pid: 999 });
  rogueWebListener.host_listener_pids.push(999);
  rogueWebListener.container_host_pids.push(999);
  rogueWebListener.listener_inode_owners[180801].push(999);
  rogueWebListener.listener_reports.push(rogueWebReport);
  rogueWebOwner.frontend_listeners = rogueWebListener.listener_reports;
  assert.throws(() => prepareAssets.validateTailnetSelectionPayload(
    rogueWebOwner, 'production', 'https://compute-node.taileb3a90.ts.net',
  ));

  const rogueProxyOwner = structuredClone(multiWorker);
  const proxy = rogueProxyOwner.tailnet_production_proxy;
  const rogueProxyReport = {
    ...proxy.listener_reports[1],
    pid: 999,
    container_pid: 99,
    executable: '/bin/sh',
    argv: ['/bin/sh', '-c', 'rogue'],
  };
  proxy.listener_pids.push(999);
  proxy.container_listener_pids.push(99);
  proxy.listener_pid_map.push({ container_pid: 99, host_pid: 999 });
  proxy.container_host_pids.push(999);
  proxy.listener_inode_owners[180811].push(999);
  proxy.listener_reports.push(rogueProxyReport);
  rogueProxyOwner.tailnet_production_proxy_listeners = proxy.listener_reports;
  assert.throws(() => prepareAssets.validateTailnetSelectionPayload(
    rogueProxyOwner, 'production', 'https://compute-node.taileb3a90.ts.net',
  ));

  for (const mutation of [
    (value) => { value.frontend_target = 'http://127.0.0.1:5173'; },
    (value) => { value.serve_root_proxy = 'http://127.0.0.1:5173'; },
    (value) => { value.runtime_mode = 'dev'; },
    (value) => { value.runtime_target = 'dev'; },
    (value) => { value.tailnet_origin = 'https://wrong.ts.net'; },
    (value) => { value.health.tailnet_api.payload.build.revision = 'b'.repeat(40); },
    (value) => { value.health.local_api.payload.build.build_id = ''; },
    (value) => { value.health.local_api.payload.build.build_id = '   '; },
    (value) => { value.health.local_api.payload.build.build_time = ''; },
    (value) => { value.health.local_api.payload.build.build_time = '2026-02-30T00:00:00Z'; },
    (value) => { value.health.local_api.payload.status = 'degraded'; },
    (value) => { value.health.local_api.payload.readiness.ready = false; },
    (value) => { value.project_revision = 'b'.repeat(40); },
    (value) => { delete value.selector_revision; },
    (value) => { value.managed_api_runtime.validated_revision = 'b'.repeat(40); },
    (value) => { value.managed_api_runtime.containers = []; },
    (value) => { value.managed_api_runtime.containers[0].image_id = 'sha256:' + 'f'.repeat(64); },
    (value) => { delete value.managed_api_listener; },
    (value) => { value.managed_api_listener.listener_inode_owners['80001'] = [101, 999]; },
    (value) => {
      const cgroup = `0::/system.slice/docker-${'1'.repeat(64)}.scope`;
      value.managed_api_runtime.containers[0].host_pids = [101, 999];
      value.managed_api_runtime.containers[0].process_reports.push({ pid: 999, cgroup });
      value.managed_api_listener.listener_inode_owners['80001'] = [101, 999];
      value.managed_api_listener.container_host_pids = [101, 999];
      value.managed_api_listener.listener_reports.push({ pid: 999, cgroup });
      value.api_listeners = value.managed_api_listener.listener_reports;
    },
    (value) => { value.api_listeners = []; },
    (value) => { delete value.workflow_adapter_listener; },
    (value) => { value.workflow_adapter_listener.listener_reports[0].executable = '/tmp/attacker/python'; },
    (value) => { value.workflow_adapter_listener.listener_reports[0].cmdline = 'python -m http.server 8001'; },
    (value) => { value.workflow_adapter_listener.listener_reports[0].build_revision = 'f'.repeat(40); },
    (value) => { value.workflow_adapter_listener.listener_reports[0].cgroup = '0::/rogue.service'; },
    (value) => {
      const report = value.managed_api_listener.listener_reports[0];
      Object.assign(report, {
        executable: '/tmp/attacker/python', cmdline: 'python -m http.server 8000',
        argv: ['python', '-m', 'http.server', '8000'], cwd: '/tmp', build_revision: 'f'.repeat(40),
      });
      value.api_listeners = value.managed_api_listener.listener_reports;
    },
    (value) => {
      const id = value.managed_api_listener.container_id;
      const lookalike = `${id.slice(0, 12)}${'f'.repeat(52)}`;
      value.managed_api_listener.listener_reports[0].cgroup = `0::/system.slice/docker-${lookalike}.scope`;
      value.api_listeners = value.managed_api_listener.listener_reports;
    },
    (value) => { value.serve_handlers['/api/tailnet-environment'].Proxy = 'http://127.0.0.1:9999'; },
    (value) => { delete value.container_runtime; },
    (value) => { value.container_runtime.containers[1].revision = 'b'.repeat(40); },
    (value) => { delete value.tailnet_production_proxy; },
    (value) => { delete value.managed_frontend_listener; },
    (value) => { value.managed_frontend_listener.listener_inode_owners['180801'] = [202, 999]; },
    (value) => {
      const report = value.managed_frontend_listener.listener_reports[0];
      Object.assign(report, {
        executable: '/tmp/attacker/nginx', cmdline: 'rogue nginx', argv: ['rogue'],
        cwd: '/tmp', build_revision: 'f'.repeat(40),
      });
      value.frontend_listeners = value.managed_frontend_listener.listener_reports;
    },
    (value) => { value.frontend_listeners = []; },
    (value) => { value.tailnet_production_proxy.listener_pids = []; },
    (value) => { value.tailnet_production_proxy.container_listener_pids = [999, 1000]; },
    (value) => { value.tailnet_production_proxy.container_host_pids = [888, 999]; },
    (value) => { value.tailnet_production_proxy.listener_inodes = [999]; },
    (value) => { value.tailnet_production_proxy.listener_inode_owners = { 999: [303, 304] }; },
    (value) => { value.tailnet_production_proxy.listener_reports[0].pid = 999; },
    (value) => { value.tailnet_production_proxy_listeners = []; },
    (value) => { value.tailnet_production_proxy.pid = 999; },
    (value) => { value.tailnet_production_proxy.config_path = '/tmp/docker/tailnet-production-proxy.conf'; },
    (value) => { value.tailnet_production_proxy.image_id = 'sha256:' + '0'.repeat(64); },
    (value) => { value.unexpected_authority = { pid: 999 }; },
    (value) => { value.workflow_adapter_listener.unexpected_authority = { pid: 999 }; },
    (value) => {
      value.managed_api_listener.container_listener_pids = [999];
      value.managed_api_listener.listener_pid_map = [{ container_pid: 999, host_pid: 101 }];
    },
    (value) => {
      const bare = `/system.slice/docker-${'1'.repeat(64)}.scope`;
      value.managed_api_runtime.containers[0].cgroup = bare;
      value.managed_api_runtime.containers[0].process_reports[0].cgroup = bare;
      value.managed_api_listener.listener_reports[0].cgroup = bare;
      value.api_listeners[0].cgroup = bare;
    },
    (value) => { value.tailnet_production_proxy.listener_pid_map.reverse(); },
    (value) => { value.tailnet_production_proxy.pid = 304; },
    (value) => {
      const web = value.container_runtime.containers.find((item) => item.name === 'biomodstack-web');
      const cgroup = `0::/system.slice/docker-${'2'.repeat(64)}.scope`;
      web.host_pids = [202, 999];
      web.process_reports.push({ pid: 999, cgroup });
      value.managed_frontend_listener.container_host_pids = [202, 999];
      value.managed_frontend_listener.listener_inode_owners['180801'] = [202, 999];
      value.managed_frontend_listener.listener_reports.push({ pid: 999, cgroup });
      value.frontend_listeners = value.managed_frontend_listener.listener_reports;
    },
    (value) => {
      value.container_runtime.containers.find((item) => item.name === 'biomodstack-web').pid = 203;
    },
    (value) => {
      const cgroup = `0::/system.slice/docker-${'4'.repeat(64)}.scope`;
      const managed = value.managed_api_runtime.containers[0];
      Object.assign(managed, {
        container_id: '4'.repeat(64), image_id: `sha256:${'4'.repeat(64)}`,
        pid: 401, cgroup, host_pids: [401], process_reports: [{ pid: 401, cgroup }],
      });
      Object.assign(value.managed_api_listener, {
        container_id: '4'.repeat(64), listener_pid_map: [{ container_pid: 1, host_pid: 401 }],
        host_listener_pids: [401], listener_inode_owners: { 80001: [401] },
        container_host_pids: [401], runtime_image_id: `sha256:${'4'.repeat(64)}`,
        listener_reports: [{ pid: 401, cgroup }],
      });
      value.api_listeners = value.managed_api_listener.listener_reports;
    },
  ]) {
    const malformed = receipt('production');
    mutation(malformed);
    assert.throws(() => prepareAssets.validateTailnetSelectionPayload(
      malformed, 'production', 'https://compute-node.taileb3a90.ts.net',
    ));
  }
  for (const mutation of [
    (value) => { delete value.development_frontend_listener; },
    (value) => { value.development_frontend_listener.listener_inode_owners['51731'] = [201, 999]; },
    (value) => { value.development_frontend_listener.listener_reports[0].executable = '/tmp/attacker/node'; },
    (value) => { value.development_frontend_listener.listener_reports[0].executable = '/home/attacker/.nvm/versions/node/v99.99.99/bin/node'; },
    (value) => { value.development_frontend_listener.listener_reports[0].cmdline = 'python -m http.server 5173'; },
    (value) => { value.development_frontend_listener.listener_reports[0].cgroup = '0::/rogue.service'; },
    (value) => { value.frontend_listeners = []; },
    (value) => { value.development_frontend_listener.unexpected_authority = { pid: 999 }; },
  ]) {
    const malformed = receipt('development');
    mutation(malformed);
    assert.throws(() => prepareAssets.validateTailnetSelectionPayload(
      malformed, 'development', 'https://compute-node.taileb3a90.ts.net',
    ));
  }
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
  assert.match(script, /Environment \<strong\>\(required before launch\)/);
  assert.match(script, /data-role="tailnet-environment"/);
  assert.match(script, /Choose an environment/);
  assert.match(script, /development/);
  assert.match(script, /production/);
  assert.match(script, /__BMS_CORDOVA_SELECT_AND_BOOT_REMOTE_UI__/);
  assert.match(script, /must be selected explicitly before each launch/);
  assert.match(script, /Revert to bundled UI/);
  assert.match(script, /window\.__BMS_CORDOVA_OPEN_PREFLIGHT__/);
  assert.doesNotThrow(() => new vm.Script(script));
  assert.match(css, /bms-cordova-preflight/);
  assert.match(css, /padding-top:\s*max\(3\.5rem, calc\(1rem \+ env\(safe-area-inset-top\)\)\)/);
  assert.match(css, /max-height:\s*calc\(100dvh - 4\.5rem\)/);
  assert.match(css, /overflow-y:\s*auto/);
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
