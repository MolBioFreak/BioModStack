#!/usr/bin/env node
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, '..');

export function parseArgs(argv) {
  const out = { config: path.join(projectRoot, 'cordova.runtime.json') };
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token === '--config') {
      out.config = path.resolve(argv[i + 1]);
      i += 1;
    }
  }
  return out;
}

export async function readJson(filePath) {
  return JSON.parse(await fs.readFile(filePath, 'utf8'));
}

export async function exists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

export function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: 'inherit',
    ...options,
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed with exit code ${result.status ?? 'unknown'}`);
  }
}

function clampNumber(value, minimum, maximum, fallback) {
  if (!Number.isFinite(value)) {
    return fallback;
  }
  return Math.min(maximum, Math.max(minimum, value));
}

export function normalizeUiUpdateChannel(value, fallback = 'phone') {
  const fallbackChannel = /^[A-Za-z0-9._-]{1,64}$/.test(String(fallback || '').trim())
    ? String(fallback || '').trim()
    : 'phone';
  const candidate = String(value || '').trim();
  if (!candidate) {
    return fallbackChannel;
  }
  return /^[A-Za-z0-9._-]{1,64}$/.test(candidate) ? candidate : fallbackChannel;
}

export function buildUiUpdateManifestPath(channel) {
  return `/api/mobile-ui/channels/${encodeURIComponent(normalizeUiUpdateChannel(channel))}/manifest`;
}

export function buildMobileViewportContent(runtime = {}) {
  const initialScale = clampNumber(Number(runtime.mobileInitialScale ?? 0.82), 0.55, 1.1, 0.82);
  const minimumScale = clampNumber(Number(runtime.mobileMinimumScale ?? 0.25), 0.25, 1.1, 0.25);
  const maximumScale = clampNumber(Number(runtime.mobileMaximumScale ?? 3), 1, 5, 3);

  return [
    'width=device-width',
    'initial-scale=' + initialScale.toFixed(2),
    'minimum-scale=' + Math.min(minimumScale, initialScale).toFixed(2),
    'maximum-scale=' + Math.max(maximumScale, initialScale).toFixed(2),
    'viewport-fit=cover',
    'user-scalable=yes',
  ].join(', ');
}

export function normalizeConfig(raw) {
  if (!raw || typeof raw !== 'object') {
    throw new Error('Runtime config must be a JSON object');
  }

  const frontendCheckout = String(raw.frontendCheckout || '').trim();
  const apiBaseUrl = String(raw.apiBaseUrl || '').trim().replace(/\/+$/, '');
  const remoteUiUrlInput = String(raw.remoteUiUrl || '').trim();
  let remoteUiUrl = '';
  if (remoteUiUrlInput) {
    const parsedRemoteUiUrl = new URL(remoteUiUrlInput);
    if (
      parsedRemoteUiUrl.protocol !== 'https:'
      || parsedRemoteUiUrl.username
      || parsedRemoteUiUrl.password
      || parsedRemoteUiUrl.search
      || parsedRemoteUiUrl.hash
      || (parsedRemoteUiUrl.pathname && parsedRemoteUiUrl.pathname !== '/')
    ) {
      throw new Error('remoteUiUrl must be an exact HTTPS origin');
    }
    remoteUiUrl = parsedRemoteUiUrl.origin + '/';
  }
  const routerBasename = String(raw.routerBasename || '/').trim() || '/';
  const uiUpdateChannel = normalizeUiUpdateChannel(raw.uiUpdateChannel, 'phone');
  const uiUpdateManifestPath = String(raw.uiUpdateManifestPath || buildUiUpdateManifestPath(uiUpdateChannel)).trim()
    || buildUiUpdateManifestPath(uiUpdateChannel);
  const shellApiVersion = Number.parseInt(raw.shellApiVersion ?? 1, 10);
  const bundledUiVersion = String(raw.bundledUiVersion || 'bundled').trim() || 'bundled';

  if (!frontendCheckout) {
    throw new Error('cordova.runtime.json must define frontendCheckout');
  }
  if (!apiBaseUrl) {
    throw new Error('cordova.runtime.json must define apiBaseUrl');
  }

  return {
    frontendCheckout,
    apiBaseUrl,
    remoteUiUrl,
    routerBasename,
    mobileInitialScale: clampNumber(Number(raw.mobileInitialScale ?? 0.82), 0.55, 1.1, 0.82),
    mobileMinimumScale: clampNumber(Number(raw.mobileMinimumScale ?? 0.25), 0.25, 1.1, 0.25),
    mobileMaximumScale: clampNumber(Number(raw.mobileMaximumScale ?? 3), 1, 5, 3),
    mobileCompactMode: raw.mobileCompactMode !== false,
    uiUpdateChannel,
    uiUpdateManifestPath,
    shellApiVersion: Number.isInteger(shellApiVersion) && shellApiVersion > 0 ? shellApiVersion : 1,
    bundledUiVersion,
  };
}

export async function resolveFrontendDir(frontendCheckout) {
  const candidateA = frontendCheckout;
  const candidateB = path.join(frontendCheckout, 'platform', 'frontend');

  if (await exists(path.join(candidateA, 'package.json'))) {
    return candidateA;
  }
  if (await exists(path.join(candidateB, 'package.json'))) {
    return candidateB;
  }

  throw new Error(`Could not find BioModStack frontend package.json under ${frontendCheckout}`);
}

export function buildRuntimeConfigScript(runtimeConfig) {
  return `window.__BMS_CORDOVA_DEFAULT_RUNTIME__ = ${JSON.stringify(runtimeConfig, null, 2)};
(() => {
  const storageKey = 'bms.cordova.runtimeOverrides';

  // Cordova opens the shell at /index.html, but the frontend router owns the root path.
  // Normalize before any React entry module is injected so the initial route resolves.
  if (window.location && window.location.pathname === '/index.html' && window.history) {
    window.history.replaceState(
      window.history.state,
      '',
      '/' + (window.location.search || '') + (window.location.hash || ''),
    );
  }

  function clampNumber(value, minimum, maximum, fallback) {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) {
      return fallback;
    }
    return Math.min(maximum, Math.max(minimum, numericValue));
  }

  function normalizeUiUpdateChannel(value, fallback) {
    const fallbackChannel = /^[A-Za-z0-9._-]{1,64}$/.test(String(fallback || '').trim())
      ? String(fallback || '').trim()
      : 'phone';
    const candidate = String(value || '').trim();
    if (!candidate) {
      return fallbackChannel;
    }
    return /^[A-Za-z0-9._-]{1,64}$/.test(candidate) ? candidate : fallbackChannel;
  }

  function buildUiUpdateManifestPath(channel) {
    return '/api/mobile-ui/channels/' + encodeURIComponent(normalizeUiUpdateChannel(channel, 'phone')) + '/manifest';
  }

  function readOverrides() {
    if (typeof localStorage === 'undefined') {
      return {};
    }
    try {
      const parsed = JSON.parse(localStorage.getItem(storageKey) || '{}');
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (_) {
      return {};
    }
  }

  const defaults = window.__BMS_CORDOVA_DEFAULT_RUNTIME__ || {};
  const overrides = readOverrides();
  const runtime = { ...defaults };

  if (typeof overrides.apiBaseUrl === 'string' && overrides.apiBaseUrl.trim()) {
    runtime.apiBaseUrl = overrides.apiBaseUrl.trim().replace(/\\/+$/, '');
  }
  if (typeof overrides.uiUpdateChannel === 'string' && overrides.uiUpdateChannel.trim()) {
    runtime.uiUpdateChannel = normalizeUiUpdateChannel(overrides.uiUpdateChannel, defaults.uiUpdateChannel || 'phone');
    runtime.uiUpdateManifestPath = buildUiUpdateManifestPath(runtime.uiUpdateChannel);
  }
  if (overrides.mobileInitialScale != null) {
    runtime.mobileInitialScale = clampNumber(overrides.mobileInitialScale, 0.55, 1.1, defaults.mobileInitialScale ?? 0.82);
  }
  if (overrides.mobileMinimumScale != null) {
    runtime.mobileMinimumScale = clampNumber(overrides.mobileMinimumScale, 0.25, 1.1, defaults.mobileMinimumScale ?? 0.25);
  }
  if (overrides.mobileMaximumScale != null) {
    runtime.mobileMaximumScale = clampNumber(overrides.mobileMaximumScale, 1, 5, defaults.mobileMaximumScale ?? 3);
  }
  if (typeof overrides.mobileCompactMode === 'boolean') {
    runtime.mobileCompactMode = overrides.mobileCompactMode;
  }

  window.__BMS_CORDOVA_RUNTIME__ = runtime;
  window.__BMS_CORDOVA_RUNTIME_OVERRIDES__ = overrides;
  window.__BMS_CORDOVA_RUNTIME_STORAGE_KEY__ = storageKey;
  window.__BMS_ROUTER_BASENAME__ = runtime.routerBasename || '/';
  window.__BMS_API_BASE_URL__ = runtime.apiBaseUrl || '';
})();
`;
}

export function buildShimScript() {
  return `(() => {
  const runtime = window.__BMS_CORDOVA_RUNTIME__ || {};
  const apiBaseUrl = String(runtime.apiBaseUrl || '').replace(/\\/+$/, '');

  function rewriteUrl(value) {
    if (!apiBaseUrl || typeof value !== 'string') {
      return value;
    }
    if (value.startsWith('/api')) {
      return apiBaseUrl + value;
    }
    if (value.startsWith('api/')) {
      return apiBaseUrl + '/' + value;
    }
    try {
      const currentLocation = typeof window !== 'undefined' && window.location ? window.location : null;
      const parsed = currentLocation && typeof URL !== 'undefined'
        ? new URL(value, currentLocation.href)
        : null;
      if (parsed && currentLocation && parsed.origin === currentLocation.origin && parsed.pathname.startsWith('/api')) {
        return apiBaseUrl + parsed.pathname + parsed.search + parsed.hash;
      }
    } catch (_) {
      // Ignore parse failures and fall back to the original value.
    }
    return value;
  }

  if (typeof window.fetch === 'function') {
    const originalFetch = window.fetch.bind(window);
    window.fetch = function patchedFetch(input, init) {
      if (typeof input === 'string') {
        return originalFetch(rewriteUrl(input), init);
      }
      if (typeof URL !== 'undefined' && input instanceof URL) {
        return originalFetch(rewriteUrl(input.toString()), init);
      }
      if (typeof Request !== 'undefined' && input instanceof Request) {
        return originalFetch(new Request(rewriteUrl(input.url), input), init);
      }
      return originalFetch(input, init);
    };
  }

  if (typeof XMLHttpRequest !== 'undefined') {
    const originalOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function patchedOpen(method, url, ...rest) {
      return originalOpen.call(this, method, rewriteUrl(url), ...rest);
    };
  }

  if (typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
    const originalSendBeacon = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = function patchedSendBeacon(url, data) {
      return originalSendBeacon(rewriteUrl(url), data);
    };
  }

  if (typeof EventSource !== 'undefined') {
    const OriginalEventSource = EventSource;
    window.EventSource = class PatchedEventSource extends OriginalEventSource {
      constructor(url, configuration) {
        super(rewriteUrl(url), configuration);
      }
    };
  }

  if (typeof WebSocket !== 'undefined') {
    const OriginalWebSocket = WebSocket;
    window.WebSocket = class PatchedWebSocket extends OriginalWebSocket {
      constructor(url, protocols) {
        super(rewriteUrl(url), protocols);
      }
    };
  }

  function patchProperty(ctor, property) {
    if (typeof ctor === 'undefined' || !ctor.prototype) {
      return;
    }
    const descriptor = Object.getOwnPropertyDescriptor(ctor.prototype, property);
    if (!descriptor || typeof descriptor.set !== 'function') {
      return;
    }
    try {
      Object.defineProperty(ctor.prototype, property, {
        ...descriptor,
        set(value) {
          return descriptor.set.call(this, rewriteUrl(value));
        },
      });
    } catch (_) {
      // Non-configurable in some WebView builds; skip silently.
    }
  }

  patchProperty(typeof HTMLImageElement !== 'undefined' ? HTMLImageElement : undefined, 'src');
  patchProperty(typeof HTMLSourceElement !== 'undefined' ? HTMLSourceElement : undefined, 'src');
})();
`;
}

export function buildMobileShellScript() {
  return `(() => {
  const runtime = window.__BMS_CORDOVA_RUNTIME__ || {};

  function clampNumber(value, minimum, maximum, fallback) {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) {
      return fallback;
    }
    return Math.min(maximum, Math.max(minimum, numericValue));
  }

  function ensureViewportMeta() {
    let meta = document.querySelector('meta[name="viewport"]');
    if (!meta) {
      meta = document.createElement('meta');
      meta.setAttribute('name', 'viewport');
      document.head.appendChild(meta);
    }
    return meta;
  }

  const initialScale = clampNumber(runtime.mobileInitialScale, 0.55, 1.1, 0.82);
  const minimumScale = clampNumber(runtime.mobileMinimumScale, 0.25, 1.1, 0.25);
  const maximumScale = clampNumber(runtime.mobileMaximumScale, 1, 5, 3);
  const viewportMeta = ensureViewportMeta();

  viewportMeta.setAttribute('content', [
    'width=device-width',
    'initial-scale=' + initialScale.toFixed(2),
    'minimum-scale=' + Math.min(minimumScale, initialScale).toFixed(2),
    'maximum-scale=' + Math.max(maximumScale, initialScale).toFixed(2),
    'viewport-fit=cover',
    'user-scalable=yes',
  ].join(', '));

  document.documentElement.classList.add('bms-cordova-shell');
  document.documentElement.classList.toggle('bms-cordova-compact', runtime.mobileCompactMode !== false);
})();
`;
}

export function buildMobileShellCss() {
  return `html.bms-cordova-shell {
  min-height: 100%;
  background: #020617;
}

html.bms-cordova-shell body {
  min-height: 100%;
  margin: 0;
  background: #020617;
  overscroll-behavior-y: none;
  -webkit-text-size-adjust: 100%;
}

html.bms-cordova-shell #root {
  min-height: 100%;
}

@media (max-width: 960px) {
  html.bms-cordova-compact nav [class*='max-w-7xl'] {
    padding-left: 0.5rem !important;
    padding-right: 0.5rem !important;
  }

  html.bms-cordova-compact nav [class*='justify-between'][class*='h-16'][class*='gap-3'] {
    height: auto !important;
    min-height: 3.25rem !important;
    gap: 0.5rem !important;
    align-items: flex-start !important;
    padding-top: 0.25rem !important;
    padding-bottom: 0.25rem !important;
  }

  html.bms-cordova-compact nav [class*='gap-1.5'][class*='flex-1'][class*='ml-2'] {
    margin-left: 0 !important;
    gap: 0.375rem !important;
    overflow-x: auto !important;
    padding-bottom: 0.25rem;
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
  }

  html.bms-cordova-compact nav [class*='gap-1.5'][class*='flex-1'][class*='ml-2']::-webkit-scrollbar {
    display: none;
  }

  html.bms-cordova-compact .min-h-screen.bg-slate-950.p-6 {
    padding: 0.75rem !important;
  }

  html.bms-cordova-compact .min-h-screen.bg-slate-950.px-6.pt-3.pb-6 {
    padding-left: 0.75rem !important;
    padding-right: 0.75rem !important;
    padding-bottom: 0.75rem !important;
  }

  html.bms-cordova-compact [class*='min-w-[420px]'] {
    min-width: 0 !important;
    width: 100% !important;
  }

  html.bms-cordova-compact [class*='w-[520px]'] {
    width: min(100vw - 1rem, 24rem) !important;
    right: 0 !important;
    left: auto !important;
  }

  html.bms-cordova-compact [class*='min-w-[9.5rem]'] {
    min-width: 0 !important;
    flex: 1 1 calc(50% - 0.25rem) !important;
  }

  html.bms-cordova-compact .molbio-toolkit .sequence-library {
    width: min(72vw, 14rem) !important;
  }

  html.bms-cordova-compact .molbio-toolkit [class*='min-w-[12rem]'] {
    min-width: min(9.5rem, calc(100vw - 2rem)) !important;
  }

  html.bms-cordova-compact .molbio-toolkit [class*='border-l'][class*='bg-slate-800'][class*='transition-[width]'] {
    width: min(75vw, 16rem) !important;
  }

  html.bms-cordova-compact [class*='w-80'][class*='bg-slate-900/80'][class*='rounded-lg'] {
    width: min(76vw, 17rem) !important;
  }

  html.bms-cordova-compact [class*='min-w-[280px]'][class*='max-w-[400px]'] {
    min-width: min(76vw, 15rem) !important;
    max-width: min(82vw, 17rem) !important;
  }
}

@media (max-width: 640px) {
  html.bms-cordova-compact [class*='min-w-[9.5rem]'] {
    flex-basis: 100% !important;
  }
}
`;
}

function normalizeAssetPath(value) {
  const trimmed = String(value || '').trim();
  if (!trimmed) {
    return '';
  }

  let normalized = trimmed.replace(/^\.\//, '');
  while (normalized.startsWith('/')) {
    normalized = normalized.slice(1);
  }
  return normalized;
}

function collectUniqueMatches(source, regex) {
  const seen = new Set();
  const values = [];
  for (const match of String(source || '').matchAll(regex)) {
    const normalized = normalizeAssetPath(match[1]);
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    values.push(normalized);
  }
  return values;
}

export function buildBundleDescriptor(html, options = {}) {
  const version = String(options.version || 'bundled').trim() || 'bundled';
  const shellApiVersion = Number.parseInt(options.shellApiVersion ?? 1, 10);

  return {
    version,
    shellApiVersion: Number.isInteger(shellApiVersion) && shellApiVersion > 0 ? shellApiVersion : 1,
    entryCss: collectUniqueMatches(
      html,
      /<link\b(?=[^>]*rel=["'][^"']*stylesheet[^"']*["'])(?=[^>]*href=["']([^"']+)["'])[^>]*>/gi,
    ).filter((value) => value.startsWith('assets/')),
    entryJs: collectUniqueMatches(
      html,
      /<script\b(?=[^>]*type=["']module["'])(?=[^>]*src=["']([^"']+)["'])[^>]*>\s*<\/script>/gi,
    ).filter((value) => value.startsWith('assets/')),
  };
}

export function validateTailnetSelectionPayload(payload, environment, trustedOrigin) {
  const expected = environment === 'development'
    ? {
      frontendTarget: 'http://127.0.0.1:5173',
      serveRootProxy: 'http://127.0.0.1:5173',
      runtimeMode: 'dev',
      runtimeTarget: 'dev',
    }
    : environment === 'production'
      ? {
        frontendTarget: 'http://127.0.0.1:18080/bms/',
        serveRootProxy: 'http://127.0.0.1:18081',
        runtimeMode: 'container',
        runtimeTarget: 'prod',
      }
      : null;
  const reject = () => {
    throw new Error('Environment selection returned a mismatched runtime identity.');
  };
  const revisionPattern = /^[0-9a-f]{40}$/;
  const digestPattern = /^sha256:[0-9a-f]{64}$/;
  const containerIdPattern = /^[0-9a-f]{64}$/;
  const nonEmptyBounded = (value, limit = 512) => (
    typeof value === 'string' && value.trim().length > 0 && value.length <= limit
  );
  const validBuildTime = (value) => {
    if (typeof value !== 'string') return false;
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z$/.exec(value);
    if (!match) return false;
    const [, yearText, monthText, dayText, hourText, minuteText, secondText, fraction = ''] = match;
    const [year, month, day, hour, minute, second] = [
      yearText, monthText, dayText, hourText, minuteText, secondText,
    ].map(Number);
    if (year < 2000) return false;
    const millisecond = Number((fraction + '000').slice(0, 3));
    const parsed = new Date(Date.UTC(year, month - 1, day, hour, minute, second, millisecond));
    return parsed.getUTCFullYear() === year
      && parsed.getUTCMonth() === month - 1
      && parsed.getUTCDate() === day
      && parsed.getUTCHours() === hour
      && parsed.getUTCMinutes() === minute
      && parsed.getUTCSeconds() === second;
  };
  const exactContainerCgroup = (cgroup, containerId) => {
    if (!containerIdPattern.test(String(containerId || ''))) return false;
    const expectedPaths = new Set([
      `/docker/${containerId}`,
      `/system.slice/docker-${containerId}.scope`,
    ]);
    const paths = String(cgroup || '').split(/\r?\n/)
      .filter(Boolean)
      .map((line) => line.split(':', 3).at(-1));
    return paths.length > 0
      && paths.some((path) => expectedPaths.has(path))
      && paths.every((path) => path === '/' || expectedPaths.has(path));
  };
  const validContainerSet = (runtime, requiredNames, revision) => {
    if (
      !runtime
      || runtime.validated_revision !== revision
      || !nonEmptyBounded(runtime.validated_compose_root)
      || !runtime.validated_compose_root.startsWith('/')
      || !Array.isArray(runtime.containers)
      || runtime.containers.length !== requiredNames.length
    ) return false;
    const byName = new Map(runtime.containers.map((container) => [container?.name, container]));
    if (byName.size !== requiredNames.length) return false;
    return requiredNames.every((name) => {
      const container = byName.get(name);
      return container
        && containerIdPattern.test(String(container.container_id || ''))
        && digestPattern.test(String(container.image_id || ''))
        && container.revision === revision
        && container.compose_working_dir === runtime.validated_compose_root
        && Number.isInteger(container.pid) && container.pid > 0
        && nonEmptyBounded(container.cgroup, 4096)
        && exactContainerCgroup(container.cgroup, container.container_id)
        && nonEmptyBounded(container.cmdline, 4096)
        && nonEmptyBounded(container.cwd, 4096);
    });
  };
  const sortedUniquePositiveIntegers = (value, allowEmpty = false) => {
    if (!Array.isArray(value) || (!allowEmpty && value.length === 0)) return false;
    if (!value.every((item) => Number.isInteger(item) && item > 0)) return false;
    return value.every((item, index) => index === 0 || value[index - 1] < item);
  };
  const normalizeAbsolutePath = (value) => {
    if (typeof value !== 'string' || !value.startsWith('/')) return null;
    const parts = [];
    for (const part of value.split('/')) {
      if (!part || part === '.') continue;
      if (part === '..') {
        if (parts.length === 0) return null;
        parts.pop();
      } else parts.push(part);
    }
    return `/${parts.join('/')}`;
  };
  const hasExactKeys = (value, keys) => value
    && typeof value === 'object'
    && !Array.isArray(value)
    && JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...keys].sort());
  const validListenerClosure = (listener, port, bindAddresses) => {
    if (
      !listener
      || listener.port !== port
      || JSON.stringify(listener.bind_addresses) !== JSON.stringify(bindAddresses)
      || !sortedUniquePositiveIntegers(listener.listener_inodes)
      || !listener.listener_inode_owners
      || typeof listener.listener_inode_owners !== 'object'
      || Array.isArray(listener.listener_inode_owners)
      || !Array.isArray(listener.listener_reports)
      || listener.listener_reports.length === 0
    ) return false;
    const inodeKeys = Object.keys(listener.listener_inode_owners);
    if (
      inodeKeys.length !== listener.listener_inodes.length
      || !listener.listener_inodes.every((inode) => Object.hasOwn(listener.listener_inode_owners, String(inode)))
    ) return false;
    const ownerPids = [...new Set(inodeKeys.flatMap((inode) => {
      const owners = listener.listener_inode_owners[inode];
      return sortedUniquePositiveIntegers(owners) ? owners : [NaN];
    }))].sort((left, right) => left - right);
    const reportPids = listener.listener_reports.map((report) => report?.pid).sort((left, right) => left - right);
    return ownerPids.length > 0
      && ownerPids.every(Number.isInteger)
      && JSON.stringify(ownerPids) === JSON.stringify(reportPids)
      && listener.listener_reports.every((report) => nonEmptyBounded(report?.cgroup, 4096));
  };
  const validContainerListener = (listener, runtime, containerName, port) => {
    if (!validListenerClosure(listener, port, ['127.0.0.1'])) return false;
    const container = runtime?.containers?.find((item) => item?.name === containerName);
    if (
      !container
      || listener.container_name !== containerName
      || listener.container_id !== container.container_id
      || JSON.stringify(listener.container_listener_pids) !== JSON.stringify([1])
      || !sortedUniquePositiveIntegers(listener.host_listener_pids)
      || !sortedUniquePositiveIntegers(listener.container_host_pids)
      || JSON.stringify(listener.host_listener_pids) !== JSON.stringify([container.pid])
      || !listener.container_host_pids.includes(container.pid)
    ) return false;
    const ownerPids = [...new Set(Object.values(listener.listener_inode_owners).flat())];
    return listener.host_listener_pids.every((pid) => ownerPids.includes(pid))
      && ownerPids.every((pid) => listener.container_host_pids.includes(pid))
      && listener.listener_reports.every((report) => (
        hasExactKeys(report, ['pid', 'cgroup'])
        && exactContainerCgroup(report.cgroup, container.container_id)
      ));
  };
  const reportInExactUnit = (report, service) => {
    if (!/^[A-Za-z0-9_.@-]+\.service$/.test(service)) return false;
    const escaped = service.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const pattern = new RegExp(`^/user\\.slice/user-(\\d+)\\.slice/user@\\1\\.service/app\\.slice/${escaped}$`);
    const paths = String(report?.cgroup || '').split(/\r?\n/)
      .filter(Boolean)
      .map((line) => line.split(':', 3).at(-1));
    return paths.length > 0
      && paths.some((path) => pattern.test(path))
      && paths.every((path) => path === '/' || pattern.test(path));
  };
  const validDevelopmentListener = (listener, projectRoot, revision) => {
    const sourceRoot = `${projectRoot}/platform/frontend`;
    const expectedVite = `${sourceRoot}/node_modules/vite/bin/vite.js`;
    return validListenerClosure(listener, 5173, ['127.0.0.1'])
      && listener.systemd_service === 'biomodstack-frontend.service'
      && listener.source_root === sourceRoot
      && listener.source_revision === revision
      && listener.listener_reports.every((report) => (
        hasExactKeys(report, [
          'pid', 'cwd', 'cmdline', 'argv', 'executable', 'cgroup', 'build_revision',
        ])
        && report.executable === '/usr/bin/node'
        && report.cwd === sourceRoot
        && report.build_revision === revision
        && report.argv.length === 6
        && report.argv[0] === '/usr/bin/node'
        && normalizeAbsolutePath(report.argv[1]) === expectedVite
        && JSON.stringify(report.argv.slice(2)) === JSON.stringify(['--host', '127.0.0.1', '--port', '5173'])
        && report.cmdline === report.argv.join(' ')
        && reportInExactUnit(report, 'biomodstack-frontend.service')
      ));
  };
  const validWorkflowAdapterListener = (
    listener, projectRoot, selectorRevision, runtimeRevision,
  ) => {
    const apiRoot = `${projectRoot}/platform/api`;
    return validListenerClosure(listener, 8001, ['127.0.0.1'])
      && listener.systemd_service === 'biomodstack-workflow-adapter.service'
      && listener.source_root === projectRoot
      && listener.source_revision === selectorRevision
      && listener.listener_reports.every((report) => (
        hasExactKeys(report, [
          'pid', 'cwd', 'cmdline', 'argv', 'executable', 'cgroup', 'build_revision',
        ])
        && report.executable === `${apiRoot}/.venv/bin/python`
        && report.cwd === apiRoot
        && report.build_revision === runtimeRevision
        && report.argv.length === 9
        && normalizeAbsolutePath(report.argv[0]) === `${apiRoot}/.venv/bin/python`
        && normalizeAbsolutePath(report.argv[1]) === `${apiRoot}/.venv/bin/uvicorn`
        && JSON.stringify(report.argv.slice(2)) === JSON.stringify([
          'workflow_adapter_app:app', '--port', '8001', '--host', '127.0.0.1',
          '--no-proxy-headers', '--no-access-log',
        ])
        && report.cmdline === report.argv.join(' ')
        && reportInExactUnit(report, 'biomodstack-workflow-adapter.service')
      ));
  };
  if (!expected || !payload || typeof payload !== 'object') reject();
  if (
    payload.selected_environment !== environment
    || payload.frontend_target !== expected.frontendTarget
    || payload.api_health_target !== 'http://127.0.0.1:8000/api/health'
    || payload.serve_root_proxy !== expected.serveRootProxy
    || payload.runtime_mode !== expected.runtimeMode
    || payload.runtime_target !== expected.runtimeTarget
    || payload.tailnet_origin !== trustedOrigin
    || !revisionPattern.test(String(payload.project_revision || ''))
    || !revisionPattern.test(String(payload.selector_revision || ''))
    || payload.serve_handlers?.['/']?.Proxy !== expected.serveRootProxy
    || payload.serve_handlers?.['/api/tailnet-environment']?.Proxy !== 'http://127.0.0.1:8001'
  ) reject();

  const health = payload.health;
  const localBuild = health?.local_api?.payload?.build;
  const tailnetBuild = health?.tailnet_api?.payload?.build;
  if (
    health?.local_frontend?.status !== 200
    || health?.tailnet_frontend?.status !== 200
    || health?.local_api?.status !== 200
    || health?.tailnet_api?.status !== 200
    || !localBuild
    || !tailnetBuild
    || !revisionPattern.test(String(localBuild.revision || ''))
    || !nonEmptyBounded(localBuild.build_id, 256)
    || !validBuildTime(localBuild.build_time)
    || health.local_api.payload.status !== 'healthy'
    || health.tailnet_api.payload.status !== 'healthy'
    || health.local_api.payload.liveness?.alive !== true
    || health.tailnet_api.payload.liveness?.alive !== true
    || health.local_api.payload.readiness?.ready !== true
    || health.tailnet_api.payload.readiness?.ready !== true
    || localBuild.revision !== tailnetBuild.revision
    || localBuild.build_id !== tailnetBuild.build_id
    || localBuild.build_time !== tailnetBuild.build_time
    || payload.project_revision !== localBuild.revision
    || !validContainerSet(payload.managed_api_runtime, ['biomodstack-api'], localBuild.revision)
    || !validContainerListener(
      payload.managed_api_listener,
      payload.managed_api_runtime,
      'biomodstack-api',
      8000,
    )
    || JSON.stringify(payload.api_listeners) !== JSON.stringify(payload.managed_api_listener?.listener_reports)
    || !validWorkflowAdapterListener(
      payload.workflow_adapter_listener,
      payload.project_root,
      payload.selector_revision,
      localBuild.revision,
    )
  ) reject();

  if (environment === 'development') {
    if (
      payload.container_runtime !== undefined
      || payload.tailnet_production_proxy !== undefined
      || payload.managed_frontend_listener !== undefined
      || !validDevelopmentListener(
        payload.development_frontend_listener,
        payload.project_root,
        payload.selector_revision,
      )
      || JSON.stringify(payload.frontend_listeners)
        !== JSON.stringify(payload.development_frontend_listener?.listener_reports)
    ) reject();
  } else {
    const proxy = payload.tailnet_production_proxy;
    if (
      !validContainerSet(payload.container_runtime, ['biomodstack-api', 'biomodstack-web'], localBuild.revision)
      || !validContainerListener(
        payload.managed_frontend_listener,
        payload.container_runtime,
        'biomodstack-web',
        18080,
      )
      || JSON.stringify(payload.frontend_listeners)
        !== JSON.stringify(payload.managed_frontend_listener?.listener_reports)
      || !proxy
      || !containerIdPattern.test(String(proxy.container_id || ''))
      || proxy.image !== 'nginx@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10'
      || proxy.image_id !== 'sha256:6769dc3a703c719c1d2756bda113659be28ae16cf0da58dd5fd823d6b9a050ea'
      || !nonEmptyBounded(proxy.config_path, 4096)
      || !proxy.config_path.endsWith('/docker/tailnet-production-proxy.conf')
      || proxy.config_sha256 !== '2c5943ce3ae5fa2ca35cd0a094a90c42b8e38b71c85c1fae58d2afe392082b62'
      || proxy.listener_port !== 18081
      || !Number.isInteger(proxy.pid) || proxy.pid <= 0
      || !Array.isArray(proxy.listener_pids) || proxy.listener_pids.length === 0
      || proxy.listener_pids.some((pid) => !Number.isInteger(pid) || pid <= 0)
      || !nonEmptyBounded(proxy.cgroup, 4096)
      || !exactContainerCgroup(proxy.cgroup, proxy.container_id)
      || proxy.cmdline !== '/docker-entrypoint.sh nginx -g daemon off;'
      || proxy.cwd !== '/'
    ) reject();
  }
  return payload;
}

export function buildUpdateLoaderScript(bundledDescriptor = { version: 'bundled', shellApiVersion: 1, entryCss: [], entryJs: [] }) {
  const normalizedBundledDescriptor = {
    version: String(bundledDescriptor.version || 'bundled').trim() || 'bundled',
    shellApiVersion: Number.parseInt(bundledDescriptor.shellApiVersion ?? 1, 10) || 1,
    entryCss: Array.isArray(bundledDescriptor.entryCss) ? bundledDescriptor.entryCss.map(normalizeAssetPath).filter(Boolean) : [],
    entryJs: Array.isArray(bundledDescriptor.entryJs) ? bundledDescriptor.entryJs.map(normalizeAssetPath).filter(Boolean) : [],
  };

  return `(() => {
  const bundleStateStorageKey = 'bms.cordova.uiBundleState';
  const downloadedBasePath = '/__bms_ui__/active/';
  const bundledDescriptor = ${JSON.stringify(normalizedBundledDescriptor, null, 2)};
  const validateTailnetSelectionPayload = ${validateTailnetSelectionPayload.toString()};
  const runtime = window.__BMS_CORDOVA_RUNTIME__ || {};
  const bootStatus = window.__BMS_CORDOVA_UI_BOOT_STATUS__ = window.__BMS_CORDOVA_UI_BOOT_STATUS__ || {
    source: 'idle',
    ready: false,
    descriptor: null,
    basePath: null,
    error: null,
    detail: null,
  };

  window.__BMS_CORDOVA_BUNDLED_DESCRIPTOR__ = bundledDescriptor;
  window.__BMS_CORDOVA_UI_BUNDLE_STORAGE_KEY__ = bundleStateStorageKey;

  function normalizeAssetPath(value) {
    const trimmed = String(value || '').trim();
    if (!trimmed) {
      return '';
    }
    let normalized = trimmed.startsWith('./') ? trimmed.slice(2) : trimmed;
    while (normalized.startsWith('/')) {
      normalized = normalized.slice(1);
    }
    return normalized;
  }

  function normalizeDescriptor(raw) {
    const descriptor = raw && typeof raw === 'object' ? raw : {};
    return {
      version: String(descriptor.version || bundledDescriptor.version || 'bundled').trim() || 'bundled',
      shellApiVersion: Number.parseInt(descriptor.shellApiVersion ?? bundledDescriptor.shellApiVersion ?? 1, 10) || 1,
      entryCss: Array.isArray(descriptor.entryCss) ? descriptor.entryCss.map(normalizeAssetPath).filter(Boolean) : [],
      entryJs: Array.isArray(descriptor.entryJs) ? descriptor.entryJs.map(normalizeAssetPath).filter(Boolean) : [],
    };
  }

  function normalizeBasePath(value, fallback) {
    const candidate = String(value || fallback || './').trim() || './';
    return candidate.endsWith('/') ? candidate : candidate + '/';
  }

  function resolveAssetUrl(basePath, assetPath) {
    const normalizedBasePath = normalizeBasePath(basePath, './');
    const normalizedAssetPath = normalizeAssetPath(assetPath);
    if (!normalizedAssetPath) {
      return normalizedBasePath;
    }
    if (normalizedBasePath.startsWith('http://') || normalizedBasePath.startsWith('https://')) {
      return new URL(normalizedAssetPath, normalizedBasePath).toString();
    }
    if (normalizedBasePath === './') {
      return './' + normalizedAssetPath;
    }
    return normalizedBasePath + normalizedAssetPath;
  }

  function readStoredBundleState() {
    if (typeof localStorage === 'undefined') {
      return null;
    }
    try {
      const parsed = JSON.parse(localStorage.getItem(bundleStateStorageKey) || 'null');
      return parsed && typeof parsed === 'object' ? parsed : null;
    } catch (_) {
      return null;
    }
  }

  function clearStoredBundleState() {
    if (typeof localStorage === 'undefined') {
      return;
    }
    try {
      localStorage.removeItem(bundleStateStorageKey);
    } catch (_) {
      // Ignore localStorage failures and fall back to the bundled UI.
    }
  }

  function appendStylesheet(href) {
    if (typeof document === 'undefined' || !document.head) {
      return;
    }
    if (document.querySelector('link[data-bms-cordova-ui-asset="' + href + '"]')) {
      return;
    }
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    link.setAttribute('data-bms-cordova-ui-asset', href);
    document.head.appendChild(link);
  }

  function appendModuleScript(src, onError) {
    if (typeof document === 'undefined' || !document.head) {
      return;
    }
    if (document.querySelector('script[data-bms-cordova-ui-asset="' + src + '"]')) {
      return;
    }
    const script = document.createElement('script');
    script.type = 'module';
    script.src = src;
    script.setAttribute('data-bms-cordova-ui-asset', src);
    script.addEventListener('error', () => {
      if (typeof onError === 'function') {
        onError(new Error('Could not load ' + src));
      }
    });
    document.head.appendChild(script);
  }

  let readyTimer = null;

  function clearReadyTimer() {
    if (readyTimer != null) {
      clearTimeout(readyTimer);
      readyTimer = null;
    }
  }

  function bootBundledUi() {
    const runtime = window.__BMS_CORDOVA_RUNTIME__ || {};
    const remoteUiUrl = String(runtime.remoteUiUrl || '').trim();
    if (remoteUiUrl) {
      bootStatus.source = 'preflight';
      bootStatus.ready = false;
      bootStatus.descriptor = { version: remoteUiUrl, shellApiVersion: bundledDescriptor.shellApiVersion, entryCss: [], entryJs: [] };
      bootStatus.basePath = remoteUiUrl;
      bootStatus.error = null;
      bootStatus.detail = { mode: 'awaiting-environment-selection' };
      return bootStatus;
    }
    return bootDescriptor(bundledDescriptor, { source: 'bundled', basePath: './' });
  }

  function mountVerifiedRemoteUi(remoteUiUrl) {
    bootStatus.source = 'remote';
    bootStatus.ready = false;
    bootStatus.descriptor = { version: remoteUiUrl, shellApiVersion: bundledDescriptor.shellApiVersion, entryCss: [], entryJs: [] };
    bootStatus.basePath = remoteUiUrl;
    bootStatus.error = null;
    bootStatus.detail = null;

    let frame = document.getElementById('bms-cordova-remote-ui');
    if (!frame) {
      frame = document.createElement('iframe');
      frame.id = 'bms-cordova-remote-ui';
      frame.title = 'BioModStack live UI';
      frame.setAttribute('allow', 'clipboard-read; clipboard-write; fullscreen');
      frame.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;border:0;background:#020617;z-index:0;';
      const mountFrame = () => {
        if (document.body && !frame.isConnected) {
          document.body.appendChild(frame);
        }
      };
      if (document.body) {
        mountFrame();
      } else {
        document.addEventListener('DOMContentLoaded', mountFrame, { once: true });
      }
    }
    frame.addEventListener('load', () => {
      bootStatus.ready = true;
      bootStatus.detail = { mode: 'remote', url: remoteUiUrl };
    }, { once: true });
    frame.addEventListener('error', () => {
      bootStatus.error = 'The live BioModStack UI could not be loaded.';
    }, { once: true });
    frame.src = remoteUiUrl;
    return bootStatus;
  }

  async function selectAndBootRemoteUi(apiBaseUrl, environment) {
    if (environment !== 'development' && environment !== 'production') {
      throw new Error('Choose Development or Production before launching.');
    }
    const configured = new URL(String(apiBaseUrl || ''));
    const trustedDefault = new URL(String(runtime.apiBaseUrl || ''));
    if (configured.protocol !== 'https:' || !configured.hostname.endsWith('.ts.net') || configured.origin !== trustedDefault.origin) {
      throw new Error('Environment selection is restricted to the APK build Tailnet origin.');
    }
    const response = await fetch(configured.origin + '/api/tailnet-environment/select', {
      method: 'POST',
      credentials: 'omit',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify({ environment }),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = payload && payload.detail ? String(payload.detail) : 'HTTP ' + response.status;
      throw new Error('Environment selection failed: ' + detail);
    }
    validateTailnetSelectionPayload(payload, environment, configured.origin);
    const remoteUiUrl = new URL(String(runtime.remoteUiUrl || configured.origin + '/'));
    if (remoteUiUrl.origin !== configured.origin) {
      throw new Error('Remote UI origin does not match the authenticated Tailnet control origin.');
    }
    remoteUiUrl.searchParams.set('bms_environment', environment);
    remoteUiUrl.searchParams.set('bms_switch', String(Date.now()));
    mountVerifiedRemoteUi(remoteUiUrl.toString());
    return payload;
  }

  function armReadyTimeout(source) {
    clearReadyTimer();
    if (source !== 'downloaded') {
      return;
    }
    readyTimer = setTimeout(() => {
      if (bootStatus.source === source && !bootStatus.ready) {
        clearStoredBundleState();
        bootStatus.error = 'Downloaded UI did not call __BMS_CORDOVA_CONFIRM_READY__ before the timeout expired.';
        bootBundledUi();
      }
    }, 15000);
  }

  function bootDescriptor(descriptorInput, options = {}) {
    const descriptor = normalizeDescriptor(descriptorInput);
    const source = options && options.source ? String(options.source) : 'bundled';
    const basePath = normalizeBasePath(options && options.basePath, source === 'downloaded' ? downloadedBasePath : './');

    bootStatus.source = source;
    bootStatus.ready = false;
    bootStatus.descriptor = descriptor;
    bootStatus.basePath = basePath;
    bootStatus.error = null;
    bootStatus.detail = null;

    descriptor.entryCss.forEach((assetPath) => {
      appendStylesheet(resolveAssetUrl(basePath, assetPath));
    });

    if (descriptor.entryJs.length === 0) {
      bootStatus.error = 'No entry JS assets were declared for the ' + source + ' UI.';
      if (source === 'downloaded') {
        clearStoredBundleState();
        return bootBundledUi();
      }
      return bootStatus;
    }

    armReadyTimeout(source);
    descriptor.entryJs.forEach((assetPath) => {
      appendModuleScript(resolveAssetUrl(basePath, assetPath), (error) => {
        bootStatus.error = error && error.message ? error.message : String(error);
        if (source === 'downloaded') {
          clearStoredBundleState();
          bootBundledUi();
        }
      });
    });

    return bootStatus;
  }

  window.__BMS_CORDOVA_CONFIRM_READY__ = function confirmCordovaUiReady(detail) {
    bootStatus.ready = true;
    bootStatus.detail = detail == null ? null : detail;
    clearReadyTimer();
    return bootStatus;
  };

  const remoteLiveMode = Boolean(String(runtime.remoteUiUrl || '').trim());
  window.__BMS_CORDOVA_BOOT_UI__ = remoteLiveMode ? bootBundledUi : bootDescriptor;
  window.__BMS_CORDOVA_SELECT_AND_BOOT_REMOTE_UI__ = selectAndBootRemoteUi;

  const storedBundleState = readStoredBundleState();
  if (remoteLiveMode) {
    if (storedBundleState && storedBundleState.descriptor) {
      clearStoredBundleState();
    }
    bootBundledUi();
  } else if (
    storedBundleState
    && storedBundleState.descriptor
    && Number.parseInt(storedBundleState.descriptor.shellApiVersion ?? 0, 10) === bundledDescriptor.shellApiVersion
  ) {
    bootDescriptor(storedBundleState.descriptor, {
      source: 'downloaded',
      basePath: storedBundleState.basePath || downloadedBasePath,
    });
  } else {
    if (storedBundleState && storedBundleState.descriptor) {
      clearStoredBundleState();
    }
    bootBundledUi();
  }
})();
`;
}

export function reduceNativeApkState(previousSequence, detail) {
  const tones = {
    checking: 'pending',
    available: 'success',
    up_to_date: 'success',
    downloading: 'pending',
    verifying: 'pending',
    awaiting_install_permission: 'pending',
    install_permission_denied: 'pending',
    installer_opened: 'pending',
    error: 'error',
  };
  const rejected = {
    accepted: false,
    sequence: Number.isSafeInteger(previousSequence) && previousSequence >= 0 ? previousSequence : 0,
    status: 'error',
    message: 'Ignored malformed or stale native APK update state.',
    tone: 'error',
    manifest: null,
  };
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) {
    return rejected;
  }
  if (!Object.keys(detail).every((key) => ['sequence', 'status', 'message', 'manifest'].includes(key))) {
    return rejected;
  }
  if (!Number.isSafeInteger(detail.sequence) || detail.sequence <= rejected.sequence ||
      typeof detail.status !== 'string' || !(detail.status in tones) ||
      typeof detail.message !== 'string' || detail.message.trim().length < 1 || detail.message.length > 1000) {
    return rejected;
  }

  const manifestStatuses = new Set([
    'available', 'downloading', 'verifying', 'awaiting_install_permission',
    'install_permission_denied', 'installer_opened',
  ]);
  const rawManifest = detail.manifest;
  if (manifestStatuses.has(detail.status) && (!rawManifest || typeof rawManifest !== 'object')) {
    return rejected;
  }
  let manifest = null;
  if (rawManifest !== undefined) {
    if (!rawManifest || typeof rawManifest !== 'object' || Array.isArray(rawManifest) ||
        !Object.keys(rawManifest).every((key) => [
          'channel', 'versionCode', 'versionName', 'minSdk', 'sizeBytes', 'publishedAt', 'changelog',
        ].includes(key)) ||
        rawManifest.channel !== 'stable' ||
        !Number.isSafeInteger(rawManifest.versionCode) || rawManifest.versionCode < 1 || rawManifest.versionCode > 2100000000 ||
        typeof rawManifest.versionName !== 'string' || rawManifest.versionName.length < 1 || rawManifest.versionName.length > 128 ||
        !Number.isInteger(rawManifest.minSdk) || rawManifest.minSdk < 1 || rawManifest.minSdk > 100 ||
        !Number.isSafeInteger(rawManifest.sizeBytes) || rawManifest.sizeBytes < 1 || rawManifest.sizeBytes > 250 * 1024 * 1024 ||
        typeof rawManifest.publishedAt !== 'string' || rawManifest.publishedAt.length > 64 ||
        !Array.isArray(rawManifest.changelog) || rawManifest.changelog.length > 50 ||
        rawManifest.changelog.some((item) => typeof item !== 'string' || item.length > 1000)) {
      return rejected;
    }
    manifest = rawManifest;
  }
  return {
    accepted: true,
    sequence: detail.sequence,
    status: detail.status,
    message: detail.message,
    tone: tones[detail.status],
    manifest,
  };
}

export function buildPreflightScript() {
  return `(() => {
  const runtime = window.__BMS_CORDOVA_RUNTIME__ || {};
  const defaults = window.__BMS_CORDOVA_DEFAULT_RUNTIME__ || runtime;
  const bundledDescriptor = window.__BMS_CORDOVA_BUNDLED_DESCRIPTOR__ || {
    version: String(defaults.bundledUiVersion || 'bundled'),
    shellApiVersion: Number.parseInt(defaults.shellApiVersion ?? 1, 10) || 1,
    entryCss: [],
    entryJs: [],
  };
  const bootStatus = window.__BMS_CORDOVA_UI_BOOT_STATUS__ || {
    source: 'bundled',
    descriptor: bundledDescriptor,
  };
  const storageKey = window.__BMS_CORDOVA_RUNTIME_STORAGE_KEY__ || 'bms.cordova.runtimeOverrides';
  const bundleStateStorageKey = window.__BMS_CORDOVA_UI_BUNDLE_STORAGE_KEY__ || 'bms.cordova.uiBundleState';
  const downloadedBasePath = '/__bms_ui__/active/';
  const overlayId = 'bms-cordova-preflight';
  const toggleId = 'bms-cordova-preflight-toggle';
  const reduceNativeApkState = ${reduceNativeApkState.toString()};
  let lastNativeApkSequence = 0;

  function clampNumber(value, minimum, maximum, fallback) {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) {
      return fallback;
    }
    return Math.min(maximum, Math.max(minimum, numericValue));
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function escapeAttribute(value) {
    return escapeHtml(value).replace(/"/g, '&quot;');
  }

  function normalizeApiBaseUrl(value, fallback) {
    const normalized = String(value || '').trim().replace(/\\/+$/, '');
    return normalized || String(fallback || '').trim().replace(/\\/+$/, '');
  }

  function normalizeUiUpdateChannel(value, fallback) {
    const fallbackChannel = /^[A-Za-z0-9._-]{1,64}$/.test(String(fallback || '').trim())
      ? String(fallback || '').trim()
      : 'phone';
    const candidate = String(value || '').trim();
    if (!candidate) {
      return fallbackChannel;
    }
    return /^[A-Za-z0-9._-]{1,64}$/.test(candidate) ? candidate : fallbackChannel;
  }

  function buildUiUpdateManifestPath(channel) {
    return '/api/mobile-ui/channels/' + encodeURIComponent(normalizeUiUpdateChannel(channel, 'phone')) + '/manifest';
  }

  function resolveUiUpdateManifestPath(channel) {
    const defaultChannel = normalizeUiUpdateChannel(defaults.uiUpdateChannel || 'phone', 'phone');
    const runtimeChannel = normalizeUiUpdateChannel(runtime.uiUpdateChannel || defaultChannel, defaultChannel);
    const requestedChannel = normalizeUiUpdateChannel(channel || runtimeChannel, defaultChannel);
    if (requestedChannel === defaultChannel) {
      return String(defaults.uiUpdateManifestPath || runtime.uiUpdateManifestPath || buildUiUpdateManifestPath(requestedChannel)).trim()
        || buildUiUpdateManifestPath(requestedChannel);
    }
    return buildUiUpdateManifestPath(requestedChannel);
  }

  function normalizeAssetPath(value) {
    const trimmed = String(value || '').trim();
    if (!trimmed) {
      return '';
    }
    let normalized = trimmed.startsWith('./') ? trimmed.slice(2) : trimmed;
    while (normalized.startsWith('/')) {
      normalized = normalized.slice(1);
    }
    return normalized;
  }

  function readJsonStorage(key, fallbackValue) {
    if (typeof localStorage === 'undefined') {
      return fallbackValue;
    }
    try {
      const parsed = JSON.parse(localStorage.getItem(key) || 'null');
      return parsed == null ? fallbackValue : parsed;
    } catch (_) {
      return fallbackValue;
    }
  }

  function writeJsonStorage(key, value) {
    if (typeof localStorage === 'undefined') {
      return;
    }
    if (value == null) {
      localStorage.removeItem(key);
      return;
    }
    localStorage.setItem(key, JSON.stringify(value));
  }

  function readOverrides() {
    const parsed = readJsonStorage(storageKey, {});
    return parsed && typeof parsed === 'object' ? parsed : {};
  }

  function writeOverrides(nextOverrides) {
    if (!nextOverrides || Object.keys(nextOverrides).length === 0) {
      writeJsonStorage(storageKey, null);
      return;
    }
    writeJsonStorage(storageKey, nextOverrides);
  }

  function readBundleState() {
    const parsed = readJsonStorage(bundleStateStorageKey, null);
    return parsed && typeof parsed === 'object' ? parsed : null;
  }

  function writeBundleState(nextState) {
    writeJsonStorage(bundleStateStorageKey, nextState);
  }

  function describeDescriptor(descriptor, fallbackVersion) {
    const version = descriptor && descriptor.version ? String(descriptor.version) : String(fallbackVersion || 'unknown');
    const shellApiVersion = Number.parseInt(descriptor && descriptor.shellApiVersion != null ? descriptor.shellApiVersion : defaults.shellApiVersion ?? 1, 10) || 1;
    return version + ' (shell ' + shellApiVersion + ')';
  }

  function getCurrentUiSnapshot() {
    const storedBundle = readBundleState();
    return {
      source: bootStatus.source || (storedBundle && storedBundle.descriptor ? 'downloaded' : 'bundled'),
      descriptor: bootStatus.descriptor || (storedBundle && storedBundle.descriptor) || bundledDescriptor,
    };
  }

  function setActiveUiLabel(panel, snapshot = getCurrentUiSnapshot()) {
    const activeUiLabel = panel.querySelector('[data-role="active-ui-label"]');
    if (!activeUiLabel) {
      return;
    }
    activeUiLabel.textContent = snapshot.source + ' · ' + describeDescriptor(snapshot.descriptor, defaults.bundledUiVersion || 'bundled');
  }

  function getDraftState() {
    return {
      apiBaseUrl: normalizeApiBaseUrl(runtime.apiBaseUrl, defaults.apiBaseUrl),
      uiUpdateChannel: normalizeUiUpdateChannel(runtime.uiUpdateChannel, defaults.uiUpdateChannel || 'phone'),
      mobileInitialScale: clampNumber(runtime.mobileInitialScale, 0.55, 1.1, defaults.mobileInitialScale ?? 0.82),
      mobileCompactMode: runtime.mobileCompactMode !== false,
    };
  }

  function buildStoredOverrides(draft) {
    const next = {};
    const normalizedApiBaseUrl = normalizeApiBaseUrl(draft.apiBaseUrl, defaults.apiBaseUrl);
    const defaultApiBaseUrl = normalizeApiBaseUrl(defaults.apiBaseUrl, '');
    if (normalizedApiBaseUrl && normalizedApiBaseUrl !== defaultApiBaseUrl) {
      next.apiBaseUrl = normalizedApiBaseUrl;
    }

    const normalizedUiUpdateChannel = normalizeUiUpdateChannel(draft.uiUpdateChannel, defaults.uiUpdateChannel || 'phone');
    const defaultUiUpdateChannel = normalizeUiUpdateChannel(defaults.uiUpdateChannel || 'phone', 'phone');
    if (normalizedUiUpdateChannel !== defaultUiUpdateChannel) {
      next.uiUpdateChannel = normalizedUiUpdateChannel;
    }

    const normalizedScale = clampNumber(draft.mobileInitialScale, 0.55, 1.1, defaults.mobileInitialScale ?? 0.82);
    const defaultScale = clampNumber(defaults.mobileInitialScale, 0.55, 1.1, 0.82);
    if (Math.abs(normalizedScale - defaultScale) > 0.001) {
      next.mobileInitialScale = Number(normalizedScale.toFixed(2));
    }

    const defaultCompactMode = defaults.mobileCompactMode !== false;
    if (Boolean(draft.mobileCompactMode) !== defaultCompactMode) {
      next.mobileCompactMode = Boolean(draft.mobileCompactMode);
    }

    return next;
  }

  function setStatus(panel, message, tone) {
    const status = panel.querySelector('[data-role="status"]');
    if (!status) {
      return;
    }
    status.dataset.status = tone || 'idle';
    status.textContent = message;
  }

  function getUiManifestUrl(apiBaseUrl, uiUpdateChannel) {
    const manifestPath = resolveUiUpdateManifestPath(uiUpdateChannel);
    if (!manifestPath) {
      throw new Error('This build does not define a UI update manifest path.');
    }
    if (manifestPath.startsWith('http://') || manifestPath.startsWith('https://')) {
      return manifestPath;
    }
    const normalizedApiBaseUrl = normalizeApiBaseUrl(apiBaseUrl, defaults.apiBaseUrl);
    if (!normalizedApiBaseUrl) {
      throw new Error('Enter an API base URL before checking for UI updates.');
    }
    return manifestPath.startsWith('/')
      ? normalizedApiBaseUrl + manifestPath
      : normalizedApiBaseUrl + '/' + manifestPath;
  }

  function normalizeBundleDescriptor(rawDescriptor) {
    const descriptor = rawDescriptor && typeof rawDescriptor === 'object' ? rawDescriptor : {};
    const shellApiVersion = Number.parseInt(descriptor.shellApiVersion ?? defaults.shellApiVersion ?? 1, 10) || 1;
    const normalized = {
      version: String(descriptor.version || '').trim(),
      shellApiVersion,
      entryCss: Array.isArray(descriptor.entryCss) ? descriptor.entryCss.map(normalizeAssetPath).filter(Boolean) : [],
      entryJs: Array.isArray(descriptor.entryJs) ? descriptor.entryJs.map(normalizeAssetPath).filter(Boolean) : [],
    };

    if (!normalized.version) {
      throw new Error('Update manifest is missing descriptor.version.');
    }
    if (normalized.entryJs.length === 0) {
      throw new Error('Update manifest is missing descriptor.entryJs.');
    }
    return normalized;
  }

  function buildFallbackFileList(descriptor) {
    const seen = new Set();
    return [...descriptor.entryCss, ...descriptor.entryJs].reduce((files, assetPath) => {
      const normalizedPath = normalizeAssetPath(assetPath);
      if (!normalizedPath || seen.has(normalizedPath)) {
        return files;
      }
      seen.add(normalizedPath);
      files.push({ path: normalizedPath });
      return files;
    }, []);
  }

  function normalizeManifestFileEntry(entry, manifest) {
    const manifestDirectoryUrl = new URL('.', manifest.manifestUrl).toString();
    const fallbackBaseUrl = [
      entry && typeof entry === 'object' && typeof entry.baseUrl === 'string' ? entry.baseUrl : '',
      manifest.payload && typeof manifest.payload.assetBaseUrl === 'string' ? manifest.payload.assetBaseUrl : '',
      manifest.payload && typeof manifest.payload.baseUrl === 'string' ? manifest.payload.baseUrl : '',
      manifest.payload && typeof manifest.payload.bundleBaseUrl === 'string' ? manifest.payload.bundleBaseUrl : '',
      manifestDirectoryUrl,
    ].find((candidate) => String(candidate || '').trim());

    const rawUrl = typeof entry === 'string'
      ? entry
      : String((entry && (entry.url || entry.href || entry.path)) || '').trim();
    let path = typeof entry === 'string'
      ? normalizeAssetPath(entry)
      : normalizeAssetPath(entry && (entry.path || entry.href || ''));

    if (!path && rawUrl) {
      try {
        path = normalizeAssetPath(new URL(rawUrl, fallbackBaseUrl).pathname);
      } catch (_) {
        path = '';
      }
    }

    if (!path) {
      throw new Error('Update manifest declared a file without a relative path.');
    }

    return {
      path,
      url: new URL(rawUrl || path, fallbackBaseUrl).toString(),
      mimeType: entry && typeof entry === 'object' && entry.mimeType ? String(entry.mimeType) : '',
    };
  }

  function callUiBundlePlugin(action, args = []) {
    if (window.cordova && window.cordova.plugins && window.cordova.plugins.bmsUiBundle && typeof window.cordova.plugins.bmsUiBundle[action] === 'function') {
      return window.cordova.plugins.bmsUiBundle[action](...args);
    }
    if (!window.cordova || typeof window.cordova.exec !== 'function') {
      throw new Error('Cordova bridge is not ready yet. Wait for deviceready before using UI update controls.');
    }
    return new Promise((resolve, reject) => {
      window.cordova.exec(resolve, reject, 'BmsUiBundle', action, args);
    });
  }

  function callNativeApkUpdater(command) {
    const bridge = window.BmsAndroidUpdater;
    if (!bridge || typeof bridge.postMessage !== 'function') {
      throw new Error('Secure native APK updater is not available in this shell.');
    }
    bridge.postMessage(JSON.stringify({ action: command }));
  }

  function runNativeApkAction(panel, command) {
    setStatus(panel, command === 'installApkUpdate' ? 'Preparing the verified Android package installer…' : 'Checking for a native APK update…', 'pending');
    try {
      callNativeApkUpdater(command);
    } catch (error) {
      setStatus(panel, 'Native APK update failed: ' + (error && error.message ? error.message : String(error)), 'error');
    }
  }

  async function fetchManifest(panel, apiBaseUrl, uiUpdateChannel) {
    const manifestUrl = getUiManifestUrl(apiBaseUrl, uiUpdateChannel);
    setStatus(panel, 'Checking UI update…', 'pending');
    const response = await fetch(manifestUrl, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) {
      throw new Error('Update manifest returned HTTP ' + response.status + '.');
    }
    const payload = await response.json();
    const descriptor = normalizeBundleDescriptor(payload && (payload.descriptor || payload.bundle || payload));
    return {
      manifestUrl,
      payload,
      descriptor,
      files: Array.isArray(payload && payload.files) ? payload.files : buildFallbackFileList(descriptor),
    };
  }

  function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    const chunkSize = 0x8000;
    let binary = '';
    for (let index = 0; index < bytes.length; index += chunkSize) {
      const chunk = bytes.subarray(index, index + chunkSize);
      binary += String.fromCharCode(...chunk);
    }
    return btoa(binary);
  }

  async function downloadBundleFiles(manifest) {
    const normalizedFiles = manifest.files.map((entry) => normalizeManifestFileEntry(entry, manifest));
    const downloadedFiles = [];

    for (const file of normalizedFiles) {
      const response = await fetch(file.url, {
        method: 'GET',
        headers: { Accept: '*/*' },
      });
      if (!response.ok) {
        throw new Error('Could not download ' + file.path + ' (HTTP ' + response.status + ').');
      }
      downloadedFiles.push({
        path: file.path,
        mimeType: file.mimeType || response.headers.get('content-type') || '',
        dataBase64: arrayBufferToBase64(await response.arrayBuffer()),
      });
    }

    return downloadedFiles;
  }

  async function probeHealth(panel, apiBaseUrl) {
    const normalizedApiBaseUrl = normalizeApiBaseUrl(apiBaseUrl, '');
    if (!normalizedApiBaseUrl) {
      setStatus(panel, 'Enter an API base URL before probing the control plane.', 'error');
      return;
    }

    setStatus(panel, 'Checking API health…', 'pending');
    try {
      const response = await fetch(normalizedApiBaseUrl + '/api/health', {
        method: 'GET',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        setStatus(panel, 'API probe returned HTTP ' + response.status + '.', 'error');
        return;
      }
      const payload = await response.json().catch(() => null);
      const statusLabel = payload && payload.status ? String(payload.status) : 'ok';
      setStatus(panel, 'API reachable (' + response.status + ', ' + statusLabel + ').', 'success');
    } catch (error) {
      const detail = error && error.message ? error.message : String(error);
      setStatus(panel, 'API probe failed: ' + detail, 'error');
    }
  }

  function resolveTailnetControlBase(apiBaseUrl) {
    const normalizedApiBaseUrl = normalizeApiBaseUrl(apiBaseUrl, defaults.apiBaseUrl);
    const configured = new URL(normalizedApiBaseUrl);
    const trustedDefault = new URL(normalizeApiBaseUrl(defaults.apiBaseUrl, ''));
    if (configured.protocol !== 'https:' || !configured.hostname.endsWith('.ts.net')) {
      throw new Error('Environment selection requires the private HTTPS Tailnet origin.');
    }
    if (configured.origin !== trustedDefault.origin) {
      throw new Error('Environment selection is restricted to the APK build Tailnet origin.');
    }
    return configured.origin;
  }

  async function selectTailnetEnvironment(panel, apiBaseUrl, environment) {
    if (environment !== 'development' && environment !== 'production') {
      throw new Error('Choose Development or Production before launching.');
    }
    const controlBase = resolveTailnetControlBase(apiBaseUrl);
    setStatus(panel, 'Starting and verifying ' + environment + '…', 'pending');
    if (typeof window.__BMS_CORDOVA_SELECT_AND_BOOT_REMOTE_UI__ !== 'function') {
      throw new Error('The trusted local shell cannot select and launch the remote UI.');
    }
    const payload = await window.__BMS_CORDOVA_SELECT_AND_BOOT_REMOTE_UI__(controlBase, environment);
    setStatus(panel, 'Verified ' + environment + '. Launching the mirrored Tailnet UI…', 'success');
    return payload;
  }

  async function refreshActiveUiFromPlugin(panel) {
    try {
      const result = await callUiBundlePlugin('getStatus');
      if (result && result.installed && result.descriptor) {
        setActiveUiLabel(panel, { source: 'downloaded', descriptor: result.descriptor });
      }
    } catch (_) {
      setActiveUiLabel(panel);
    }
  }

  async function checkUiUpdate(panel, apiBaseUrl, uiUpdateChannel) {
    try {
      const manifest = await fetchManifest(panel, apiBaseUrl, uiUpdateChannel);
      const shellApiVersion = Number.parseInt(defaults.shellApiVersion ?? runtime.shellApiVersion ?? 1, 10) || 1;
      if (manifest.descriptor.shellApiVersion !== shellApiVersion) {
        setStatus(panel, 'Found UI ' + manifest.descriptor.version + ', but it targets shell API ' + manifest.descriptor.shellApiVersion + ' while this APK exposes shell API ' + shellApiVersion + '.', 'error');
        return null;
      }

      const currentSnapshot = getCurrentUiSnapshot();
      if (currentSnapshot.descriptor && currentSnapshot.descriptor.version === manifest.descriptor.version) {
        setStatus(panel, 'UI is already on ' + manifest.descriptor.version + '.', 'success');
      } else {
        setStatus(panel, 'Update available: ' + describeDescriptor(currentSnapshot.descriptor, defaults.bundledUiVersion || 'bundled') + ' → ' + describeDescriptor(manifest.descriptor, manifest.descriptor.version) + '.', 'success');
      }
      return manifest;
    } catch (error) {
      const detail = error && error.message ? error.message : String(error);
      setStatus(panel, 'UI update check failed: ' + detail, 'error');
      return null;
    }
  }

  async function installUiUpdate(panel, apiBaseUrl, uiUpdateChannel) {
    try {
      const manifest = await fetchManifest(panel, apiBaseUrl, uiUpdateChannel);
      const shellApiVersion = Number.parseInt(defaults.shellApiVersion ?? runtime.shellApiVersion ?? 1, 10) || 1;
      if (manifest.descriptor.shellApiVersion !== shellApiVersion) {
        setStatus(panel, 'Update ' + manifest.descriptor.version + ' targets shell API ' + manifest.descriptor.shellApiVersion + ', not this APK shell API ' + shellApiVersion + '.', 'error');
        return;
      }

      setStatus(panel, 'Downloading UI bundle ' + manifest.descriptor.version + '…', 'pending');
      const downloadedFiles = await downloadBundleFiles(manifest);
      setStatus(panel, 'Installing UI bundle ' + manifest.descriptor.version + '…', 'pending');
      const result = await callUiBundlePlugin('installBundle', [manifest.descriptor, downloadedFiles]);
      writeBundleState({
        descriptor: manifest.descriptor,
        basePath: result && result.basePath ? result.basePath : downloadedBasePath,
      });
      setActiveUiLabel(panel, { source: 'downloaded', descriptor: manifest.descriptor });
      setStatus(panel, 'Installed UI ' + manifest.descriptor.version + '. Reloading…', 'success');
      setTimeout(() => {
        window.location.reload();
      }, 150);
    } catch (error) {
      const detail = error && error.message ? error.message : String(error);
      setStatus(panel, 'Update UI failed: ' + detail, 'error');
    }
  }

  async function revertToBundledUi(panel) {
    try {
      setStatus(panel, 'Reverting to bundled UI…', 'pending');
      await callUiBundlePlugin('clearBundle');
      writeBundleState(null);
      setActiveUiLabel(panel, { source: 'bundled', descriptor: bundledDescriptor });
      setStatus(panel, 'Reverted to bundled UI. Reloading…', 'success');
      setTimeout(() => {
        window.location.reload();
      }, 150);
    } catch (error) {
      const detail = error && error.message ? error.message : String(error);
      setStatus(panel, 'Could not revert to bundled UI: ' + detail, 'error');
    }
  }

  function hidePanel() {
    const panel = document.getElementById(overlayId);
    if (panel) {
      panel.hidden = true;
    }
  }

  function showPanel() {
    const panel = document.getElementById(overlayId);
    if (panel) {
      panel.hidden = false;
    }
  }

  function mountControlSurface() {
    if (!document.body || document.getElementById(overlayId)) {
      return;
    }

    const overrides = readOverrides();
    const draft = getDraftState();
    const currentSnapshot = getCurrentUiSnapshot();

    const panel = document.createElement('div');
    panel.id = overlayId;
    panel.className = 'bms-cordova-preflight';
    panel.innerHTML = [
      '<div class="bms-cordova-preflight__scrim"></div>',
      '<section class="bms-cordova-preflight__panel" role="dialog" aria-modal="true" aria-labelledby="bms-cordova-preflight-title">',
      '  <div class="bms-cordova-preflight__eyebrow">BioModStack APK control surface</div>',
      '  <h1 id="bms-cordova-preflight-title" class="bms-cordova-preflight__title">Pre-flight settings</h1>',
      '  <p class="bms-cordova-preflight__copy">Choose the canonical environment that Tailnet must mirror, then launch only after its frontend, API, listener ownership, and Serve route verify.</p>',
      '  <label class="bms-cordova-preflight__field">',
      '    <span>Environment <strong>(required before launch)</strong></span>',
      '    <select data-role="tailnet-environment">',
      '      <option value="">Choose an environment…</option>',
      '      <option value="development">Development</option>',
      '      <option value="production">Production</option>',
      '    </select>',
      '  </label>',
      '  <div class="bms-cordova-preflight__hint">Tailnet is a private routing layer only. It mirrors the selected canonical environment and never serves a third checkout.</div>',
      '  <label class="bms-cordova-preflight__field">',
      '    <span>API base URL</span>',
      '    <input data-role="api-base-url" type="url" inputmode="url" autocomplete="off" spellcheck="false" value="' + escapeAttribute(draft.apiBaseUrl) + '" />',
      '  </label>',
      '  <div class="bms-cordova-preflight__hint">Build default: <span class="bms-cordova-preflight__mono">' + escapeHtml(normalizeApiBaseUrl(defaults.apiBaseUrl, '')) + '</span></div>',
      '  <label class="bms-cordova-preflight__field">',
      '    <span>UI update channel</span>',
      '    <input data-role="ui-update-channel" type="text" inputmode="text" autocomplete="off" spellcheck="false" pattern="[A-Za-z0-9._-]{1,64}" value="' + escapeAttribute(draft.uiUpdateChannel) + '" />',
      '  </label>',
      '  <div class="bms-cordova-preflight__hint"><strong>UI bundle updates</strong> replace web assets only; they never replace the installed Android app.</div>',
      '  <div class="bms-cordova-preflight__hint">Update manifest: <span class="bms-cordova-preflight__mono">' + escapeHtml(resolveUiUpdateManifestPath(draft.uiUpdateChannel)) + '</span></div>',
      '  <div class="bms-cordova-preflight__hint">Bundled UI: <span class="bms-cordova-preflight__mono">' + escapeHtml(describeDescriptor(bundledDescriptor, defaults.bundledUiVersion || 'bundled')) + '</span></div>',
      '  <div class="bms-cordova-preflight__hint">Active UI: <span class="bms-cordova-preflight__mono" data-role="active-ui-label">' + escapeHtml(currentSnapshot.source + ' · ' + describeDescriptor(currentSnapshot.descriptor, defaults.bundledUiVersion || 'bundled')) + '</span></div>',
      '  <div class="bms-cordova-preflight__hint"><strong>Native APK update</strong> replaces the Android shell only after package, signer, version, size, minSdk, and SHA-256 verification and Android user approval.</div>',
      '  <label class="bms-cordova-preflight__field">',
      '    <span>Global UI scale <strong data-role="scale-value">' + draft.mobileInitialScale.toFixed(2) + '</strong></span>',
      '    <input data-role="mobile-scale" type="range" min="0.55" max="1.00" step="0.01" value="' + draft.mobileInitialScale.toFixed(2) + '" />',
      '  </label>',
      '  <label class="bms-cordova-preflight__checkbox">',
      '    <input data-role="compact-mode" type="checkbox"' + (draft.mobileCompactMode ? ' checked' : '') + ' />',
      '    <span>Use compact mobile shell overrides</span>',
      '  </label>',
      '  <div class="bms-cordova-preflight__hint">Saved override keys: <span class="bms-cordova-preflight__mono">' + escapeHtml(Object.keys(overrides).join(', ')) + '</span></div>',
      '  <div class="bms-cordova-preflight__status" data-role="status" data-status="idle">Ready. UI-bundle and native-APK update controls are intentionally separate.</div>',
      '  <div class="bms-cordova-preflight__actions">',
      '    <button type="button" data-action="probe">Test connection</button>',
      '    <button type="button" data-action="check-ui-update">Check UI update</button>',
      '    <button type="button" data-action="update-ui">Update UI</button>',
      '    <button type="button" data-action="revert-ui">Revert to bundled UI</button>',
      '    <button type="button" data-action="check-apk-update">Check native APK</button>',
      '    <button type="button" data-action="install-apk-update">Install native APK</button>',
      '    <button type="button" data-action="reset">Reset defaults</button>',
      '    <button type="button" data-action="save-reload" data-variant="primary">Save + reload</button>',
      '    <button type="button" data-action="launch" data-variant="primary" disabled>Verify environment + launch</button>',
      '  </div>',
      '  <div class="bms-cordova-preflight__footnote">The environment choice is intentionally not persisted: Development or Production must be selected explicitly before each launch.</div>',
      '</section>',
    ].join('');
    document.body.appendChild(panel);

    let toggle = document.getElementById(toggleId);
    if (!toggle) {
      toggle = document.createElement('button');
      toggle.id = toggleId;
      toggle.type = 'button';
      toggle.className = 'bms-cordova-preflight-toggle';
      toggle.setAttribute('aria-label', 'Open BioModStack APK settings');
      toggle.textContent = '⚙';
      toggle.addEventListener('click', () => {
        showPanel();
      });
      document.body.appendChild(toggle);
    }

    const apiBaseUrlInput = panel.querySelector('[data-role="api-base-url"]');
    const tailnetEnvironmentInput = panel.querySelector('[data-role="tailnet-environment"]');
    const uiUpdateChannelInput = panel.querySelector('[data-role="ui-update-channel"]');
    const scaleInput = panel.querySelector('[data-role="mobile-scale"]');
    const scaleValue = panel.querySelector('[data-role="scale-value"]');
    const compactModeInput = panel.querySelector('[data-role="compact-mode"]');
    const launchButton = panel.querySelector('button[data-action="launch"]');

    tailnetEnvironmentInput.addEventListener('change', () => {
      launchButton.disabled = !['development', 'production'].includes(tailnetEnvironmentInput.value);
    });

    scaleInput.addEventListener('input', () => {
      scaleValue.textContent = clampNumber(scaleInput.value, 0.55, 1.0, draft.mobileInitialScale).toFixed(2);
    });

    panel.addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-action]');
      if (!button) {
        return;
      }

      const currentDraft = {
        apiBaseUrl: apiBaseUrlInput.value,
        uiUpdateChannel: uiUpdateChannelInput.value,
        mobileInitialScale: scaleInput.value,
        mobileCompactMode: compactModeInput.checked,
      };

      if (button.dataset.action === 'probe') {
        await probeHealth(panel, currentDraft.apiBaseUrl);
        return;
      }

      if (button.dataset.action === 'check-ui-update') {
        await checkUiUpdate(panel, currentDraft.apiBaseUrl, currentDraft.uiUpdateChannel);
        return;
      }

      if (button.dataset.action === 'update-ui') {
        await installUiUpdate(panel, currentDraft.apiBaseUrl, currentDraft.uiUpdateChannel);
        return;
      }

      if (button.dataset.action === 'revert-ui') {
        await revertToBundledUi(panel);
        return;
      }

      const nativeApkCommands = {
        'check-apk-update': 'checkForApkUpdate',
        'install-apk-update': 'installApkUpdate',
      };
      if (nativeApkCommands[button.dataset.action]) {
        runNativeApkAction(panel, nativeApkCommands[button.dataset.action]);
        return;
      }

      if (button.dataset.action === 'reset') {
        apiBaseUrlInput.value = normalizeApiBaseUrl(defaults.apiBaseUrl, '');
        uiUpdateChannelInput.value = normalizeUiUpdateChannel(defaults.uiUpdateChannel || 'phone', 'phone');
        scaleInput.value = clampNumber(defaults.mobileInitialScale, 0.55, 1.0, 0.82).toFixed(2);
        scaleValue.textContent = scaleInput.value;
        compactModeInput.checked = defaults.mobileCompactMode !== false;
        try {
          writeOverrides({});
        } catch (_) {
          // Ignore localStorage write issues; the next reload will fall back to build defaults.
        }
        setStatus(panel, 'Reverted to build defaults. Tap Save + reload to apply them.', 'idle');
        return;
      }

      if (button.dataset.action === 'save-reload') {
        try {
          writeOverrides(buildStoredOverrides(currentDraft));
          window.location.reload();
        } catch (error) {
          const detail = error && error.message ? error.message : String(error);
          setStatus(panel, 'Could not persist settings: ' + detail, 'error');
        }
        return;
      }

      if (button.dataset.action === 'launch') {
        button.disabled = true;
        try {
          await selectTailnetEnvironment(panel, currentDraft.apiBaseUrl, tailnetEnvironmentInput.value);
          hidePanel();
        } catch (error) {
          const detail = error && error.message ? error.message : String(error);
          setStatus(panel, detail, 'error');
        } finally {
          button.disabled = !['development', 'production'].includes(tailnetEnvironmentInput.value);
        }
      }
    });

    document.addEventListener('deviceready', () => {
      refreshActiveUiFromPlugin(panel);
    }, { once: true });

    window.addEventListener('biomodstack-apk-update-state', (event) => {
      const nextState = reduceNativeApkState(lastNativeApkSequence, event && event.detail);
      if (!nextState.accepted) {
        setStatus(panel, nextState.message, nextState.tone);
        return;
      }
      lastNativeApkSequence = nextState.sequence;
      setStatus(panel, nextState.message, nextState.tone);
    });

    setTimeout(() => {
      probeHealth(panel, draft.apiBaseUrl);
      setActiveUiLabel(panel);
    }, 0);
  }

  window.__BMS_CORDOVA_OPEN_PREFLIGHT__ = showPanel;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountControlSurface, { once: true });
  } else {
    mountControlSurface();
  }
})();
`;
}

export function buildPreflightCss() {
  return `.bms-cordova-preflight[hidden] {
  display: none;
}

.bms-cordova-preflight {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  box-sizing: border-box;
  padding: 1rem;
  padding-top: max(3.5rem, calc(1rem + env(safe-area-inset-top)));
  padding-bottom: max(1rem, env(safe-area-inset-bottom));
}

.bms-cordova-preflight__scrim {
  position: absolute;
  inset: 0;
  background: rgba(2, 6, 23, 0.78);
  backdrop-filter: blur(10px);
}

.bms-cordova-preflight__panel {
  position: relative;
  width: min(100%, 32rem);
  max-height: calc(100dvh - 4.5rem);
  overflow-y: auto;
  overscroll-behavior: contain;
  border-radius: 1.25rem;
  border: 1px solid rgba(148, 163, 184, 0.2);
  background: rgba(15, 23, 42, 0.98);
  box-shadow: 0 32px 80px rgba(15, 23, 42, 0.55);
  padding: 1rem;
  color: #e2e8f0;
}

.bms-cordova-preflight__eyebrow {
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #67e8f9;
}

.bms-cordova-preflight__title {
  margin: 0.35rem 0 0;
  font-size: 1.35rem;
}

.bms-cordova-preflight__copy {
  margin: 0.5rem 0 1rem;
  color: #94a3b8;
  line-height: 1.45;
}

.bms-cordova-preflight__field {
  display: grid;
  gap: 0.45rem;
  margin-top: 0.75rem;
}

.bms-cordova-preflight__field span {
  font-size: 0.88rem;
  font-weight: 600;
}

.bms-cordova-preflight__field input[type='url'],
.bms-cordova-preflight__field input[type='text'],
.bms-cordova-preflight__field select {
  width: 100%;
  border-radius: 0.85rem;
  border: 1px solid rgba(71, 85, 105, 0.8);
  background: rgba(15, 23, 42, 0.82);
  color: #f8fafc;
  padding: 0.8rem 0.9rem;
  font-size: 0.95rem;
  box-sizing: border-box;
}

.bms-cordova-preflight__field input[type='range'] {
  width: 100%;
}

.bms-cordova-preflight__checkbox {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  margin-top: 0.9rem;
  color: #cbd5e1;
}

.bms-cordova-preflight__checkbox input {
  width: 1rem;
  height: 1rem;
}

.bms-cordova-preflight__hint,
.bms-cordova-preflight__footnote {
  margin-top: 0.55rem;
  font-size: 0.78rem;
  color: #94a3b8;
}

.bms-cordova-preflight__mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  word-break: break-all;
  color: #e2e8f0;
}

.bms-cordova-preflight__status {
  margin-top: 1rem;
  border-radius: 0.9rem;
  border: 1px solid rgba(71, 85, 105, 0.65);
  background: rgba(15, 23, 42, 0.82);
  padding: 0.8rem 0.9rem;
  font-size: 0.88rem;
  line-height: 1.4;
}

.bms-cordova-preflight__status[data-status='pending'] {
  border-color: rgba(56, 189, 248, 0.45);
  color: #bae6fd;
}

.bms-cordova-preflight__status[data-status='success'] {
  border-color: rgba(34, 197, 94, 0.5);
  color: #bbf7d0;
}

.bms-cordova-preflight__status[data-status='error'] {
  border-color: rgba(248, 113, 113, 0.55);
  color: #fecaca;
}

.bms-cordova-preflight__actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.6rem;
  margin-top: 1rem;
}

.bms-cordova-preflight__actions button,
.bms-cordova-preflight-toggle {
  border: 1px solid rgba(71, 85, 105, 0.8);
  background: rgba(15, 23, 42, 0.92);
  color: #e2e8f0;
  border-radius: 0.9rem;
  padding: 0.75rem 0.85rem;
  font-size: 0.92rem;
  font-weight: 600;
}

.bms-cordova-preflight__actions button[data-variant='primary'] {
  background: linear-gradient(135deg, #0891b2, #2563eb);
  border-color: rgba(103, 232, 249, 0.45);
  color: white;
}

.bms-cordova-preflight__actions button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.bms-cordova-preflight-toggle {
  position: fixed;
  right: max(0.75rem, env(safe-area-inset-right));
  bottom: max(0.75rem, env(safe-area-inset-bottom));
  z-index: 9998;
  width: 2.9rem;
  height: 2.9rem;
  border-radius: 999px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.35);
}

@media (max-width: 640px) {
  .bms-cordova-preflight {
    align-items: flex-end;
    padding: 0.75rem;
  }

  .bms-cordova-preflight__panel {
    width: 100%;
    padding: 0.9rem;
  }

  .bms-cordova-preflight__actions {
    grid-template-columns: 1fr;
  }
}
`;
}

function stripBundledEntrypointTags(html) {
  return html
    .replace(/^[ \t]*<link\b(?=[^>]*rel=["'][^"']*(?:stylesheet|modulepreload)[^"']*["'])(?=[^>]*href=["'](?:\.\/)?assets\/[^"']+["'])[^>]*>\s*\n?/gim, '')
    .replace(/^[ \t]*<script\b(?=[^>]*type=["']module["'])(?=[^>]*src=["'](?:\.\/)?assets\/[^"']+["'])[^>]*>\s*<\/script>\s*\n?/gim, '');
}

export function patchIndexHtmlContent(html, runtimeConfig = {}) {
  let patched = stripBundledEntrypointTags(html);
  const viewportTag = `    <meta name="viewport" content="${buildMobileViewportContent(runtimeConfig)}" />\n`;
  if (patched.includes('<meta name="viewport"')) {
    patched = patched.replace(/^[ \t]*<meta name="viewport"[^>]*>\s*\n?/im, viewportTag);
  } else if (patched.includes('<meta charset')) {
    patched = patched.replace(/^[ \t]*<meta charset[^>]*>\s*\n?/im, (match) => match + viewportTag);
  } else {
    patched = patched.replace('<head>', `<head>\n${viewportTag}`);
  }
  const cspTag = '    <meta http-equiv="Content-Security-Policy" content="default-src \'self\' data: blob: gap: https: http:; img-src * data: blob:; style-src \'self\' \'unsafe-inline\' https: http:; font-src \'self\' data: https: http:; media-src * data: blob:; connect-src * data: blob: ws: wss:; script-src \'self\' \'unsafe-inline\' \'unsafe-eval\' https: http:;">\n';
  if (!patched.includes('Content-Security-Policy')) {
    if (patched.includes('<meta name="viewport"')) {
      patched = patched.replace(/<meta name="viewport"[^>]*>\n/, (match) => match + cspTag);
    } else {
      patched = patched.replace('</head>', `${cspTag}</head>`);
    }
  }

  const mobileCssTags = [
    '    <link rel="stylesheet" href="./bms-cordova-mobile-shell.css">',
    '    <link rel="stylesheet" href="./bms-cordova-preflight.css">',
  ].join('\n') + '\n';
  if (!patched.includes('bms-cordova-mobile-shell.css')) {
    patched = patched.replace('</head>', `${mobileCssTags}</head>`);
  }

  const shellScriptTags = [
    '    <script src="cordova.js"></script>',
    '    <script src="bms-runtime-config.js"></script>',
    '    <script src="bms-cordova-shim.js"></script>',
    '    <script src="bms-cordova-mobile-shell.js"></script>',
    '    <script src="bms-cordova-update-loader.js"></script>',
    '    <script src="bms-cordova-preflight.js"></script>',
  ].join('\n') + '\n';

  if (!patched.includes('bms-runtime-config.js')) {
    patched = patched.replace('</head>', `${shellScriptTags}</head>`);
  } else {
    const missingTags = [];
    if (!patched.includes('bms-cordova-mobile-shell.js')) {
      missingTags.push('    <script src="bms-cordova-mobile-shell.js"></script>');
    }
    if (!patched.includes('bms-cordova-update-loader.js')) {
      missingTags.push('    <script src="bms-cordova-update-loader.js"></script>');
    }
    if (!patched.includes('bms-cordova-preflight.js')) {
      missingTags.push('    <script src="bms-cordova-preflight.js"></script>');
    }

    if (missingTags.length > 0) {
      if (patched.includes('<script src="bms-cordova-shim.js"></script>')) {
        patched = patched.replace(
          '<script src="bms-cordova-shim.js"></script>',
          `<script src="bms-cordova-shim.js"></script>\n${missingTags.join('\n')}`,
        );
      } else {
        patched = patched.replace('</head>', `${missingTags.join('\n')}\n</head>`);
      }
    }
  }

  return patched;
}

export async function patchIndexHtml(indexPath, runtimeConfig = {}) {
  const html = await fs.readFile(indexPath, 'utf8');
  await fs.writeFile(indexPath, patchIndexHtmlContent(html, runtimeConfig), 'utf8');
}

export async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  const runtimeConfig = normalizeConfig(await readJson(args.config));
  const frontendDir = await resolveFrontendDir(runtimeConfig.frontendCheckout);
  const packageJsonPath = path.join(frontendDir, 'package.json');
  const nodeModulesPath = path.join(frontendDir, 'node_modules');
  const outDir = path.join(projectRoot, '.cache', 'bms-frontend-dist');
  const wwwDir = path.join(projectRoot, 'www');
  const indexPath = path.join(wwwDir, 'index.html');
  let bundledDescriptor;

  await fs.rm(wwwDir, { recursive: true, force: true });
  await fs.mkdir(wwwDir, { recursive: true });

  if (runtimeConfig.remoteUiUrl) {
    console.log(`Preparing trusted remote-live Cordova shell for: ${runtimeConfig.remoteUiUrl}`);
    await fs.writeFile(
      indexPath,
      '<!doctype html>\n<html><head><meta charset="UTF-8"><title>BioModStack</title></head><body><div id="root"></div></body></html>\n',
      'utf8',
    );
    bundledDescriptor = buildBundleDescriptor(await fs.readFile(indexPath, 'utf8'), {
      version: runtimeConfig.bundledUiVersion,
      shellApiVersion: runtimeConfig.shellApiVersion,
    });
  } else {
    if (!(await exists(packageJsonPath))) {
      throw new Error(`Missing ${packageJsonPath}`);
    }
    if (!(await exists(nodeModulesPath))) {
      throw new Error(`Expected ${nodeModulesPath}. Bootstrap the source checkout once with pnpm install --frozen-lockfile before running this wrapper.`);
    }

    console.log(`Using BioModStack frontend at: ${frontendDir}`);
    console.log(`Building Vite assets into: ${outDir}`);
    run('pnpm', ['exec', 'vite', 'build', '--base', './', '--outDir', outDir, '--emptyOutDir'], {
      cwd: frontendDir,
      env: process.env,
    });
    await fs.cp(outDir, wwwDir, { recursive: true });
    bundledDescriptor = buildBundleDescriptor(await fs.readFile(indexPath, 'utf8'), {
      version: runtimeConfig.bundledUiVersion,
      shellApiVersion: runtimeConfig.shellApiVersion,
    });
  }

  await fs.writeFile(path.join(wwwDir, 'bms-runtime-config.js'), buildRuntimeConfigScript(runtimeConfig), 'utf8');
  await fs.writeFile(path.join(wwwDir, 'bms-cordova-shim.js'), buildShimScript(), 'utf8');
  await fs.writeFile(path.join(wwwDir, 'bms-cordova-mobile-shell.js'), buildMobileShellScript(), 'utf8');
  await fs.writeFile(path.join(wwwDir, 'bms-cordova-mobile-shell.css'), buildMobileShellCss(), 'utf8');
  await fs.writeFile(path.join(wwwDir, 'bms-cordova-update-loader.js'), buildUpdateLoaderScript(bundledDescriptor), 'utf8');
  await fs.writeFile(path.join(wwwDir, 'bms-cordova-preflight.js'), buildPreflightScript(), 'utf8');
  await fs.writeFile(path.join(wwwDir, 'bms-cordova-preflight.css'), buildPreflightCss(), 'utf8');
  await patchIndexHtml(indexPath, runtimeConfig);

  console.log(`Prepared Cordova web assets in: ${wwwDir}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === __filename) {
  main().catch((error) => {
    console.error(error.stack || String(error));
    process.exit(1);
  });
}
