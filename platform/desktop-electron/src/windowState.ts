import type { BrowserWindowConstructorOptions } from 'electron';

import { SHELL_STORAGE_PARTITION } from './shellPaths.js';
import type { ServiceRuntimeMode, ServiceRuntimeTarget, ServiceStatusPayload } from './serviceControl.js';

export type ShellRuntimeMode = ServiceRuntimeMode;

export type ShellContext = {
  runtimeMode: ShellRuntimeMode;
  frontendOrigin: string;
  routerBasename: string;
  windowUrl: string;
  browserUrl: string;
};

export type BiomodstackDesktopApi = {
  getShellContext: () => Promise<ShellContext>;
  getStatus: (runtimeMode?: ShellRuntimeMode) => Promise<ServiceStatusPayload>;
  startAll: (runtimeMode?: ShellRuntimeMode) => Promise<void>;
  stopAll: (runtimeMode?: ShellRuntimeMode) => Promise<void>;
  restartAll: (runtimeMode?: ShellRuntimeMode) => Promise<void>;
  restartApi: (runtimeMode?: ShellRuntimeMode) => Promise<void>;
  switchRuntime: (runtimeMode: ShellRuntimeMode) => Promise<ShellContext>;
  startRuntimeTarget: (target: ServiceRuntimeTarget) => Promise<void>;
  openInBrowser: () => Promise<void>;
  getZoomFactor: () => Promise<number>;
  setZoomFactor: (zoomFactor: number) => Promise<number>;
  adjustZoom: (deltaSteps: number) => Promise<number>;
  resetZoom: () => Promise<number>;
};

export const GET_SHELL_CONTEXT_CHANNEL = 'biomodstack:get-shell-context';
export const GET_STATUS_CHANNEL = 'biomodstack:get-status';
export const START_ALL_CHANNEL = 'biomodstack:start-all';
export const STOP_ALL_CHANNEL = 'biomodstack:stop-all';
export const RESTART_ALL_CHANNEL = 'biomodstack:restart-all';
export const RESTART_API_CHANNEL = 'biomodstack:restart-api';
export const SWITCH_RUNTIME_CHANNEL = 'biomodstack:switch-runtime';
export const START_RUNTIME_TARGET_CHANNEL = 'biomodstack:start-runtime-target';
export const OPEN_IN_BROWSER_CHANNEL = 'biomodstack:open-in-browser';
export const GET_ZOOM_FACTOR_CHANNEL = 'biomodstack:get-zoom-factor';
export const SET_ZOOM_FACTOR_CHANNEL = 'biomodstack:set-zoom-factor';
export const ADJUST_ZOOM_CHANNEL = 'biomodstack:adjust-zoom';
export const RESET_ZOOM_CHANNEL = 'biomodstack:reset-zoom';

export const EXPOSED_BIOMODSTACK_API_KEYS = [
  'getShellContext',
  'getStatus',
  'startAll',
  'stopAll',
  'restartAll',
  'restartApi',
  'switchRuntime',
  'startRuntimeTarget',
  'openInBrowser',
  'getZoomFactor',
  'setZoomFactor',
  'adjustZoom',
  'resetZoom',
] as const;

function normalizeOrigin(origin: string): string {
  return origin.trim().replace(/\/+$/, '');
}

function normalizeRouterBasename(basename: string): string {
  const trimmed = basename.trim();
  if (!trimmed || trimmed === '/') {
    return '/';
  }

  const withLeadingSlash = trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
  const withoutTrailingSlash = withLeadingSlash.replace(/\/+$/, '');
  return withoutTrailingSlash ? `${withoutTrailingSlash}/` : '/';
}

function normalizeAppPath(pathname: string): string {
  const trimmed = pathname.trim();
  if (!trimmed || trimmed === '/') {
    return '/';
  }
  const withLeadingSlash = trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
  const withoutTrailingSlash = withLeadingSlash.replace(/\/+$/, '');
  return withoutTrailingSlash || '/';
}

function getCurrentAppPath(pathname: string, routerBasename: string): string {
  const normalizedPathname = normalizeAppPath(pathname);
  const normalizedBasename = normalizeRouterBasename(routerBasename);
  if (normalizedBasename === '/') {
    return normalizedPathname;
  }

  const basenamePrefix = normalizedBasename.slice(0, -1);
  if (normalizedPathname === basenamePrefix) {
    return '/';
  }
  if (normalizedPathname.startsWith(`${basenamePrefix}/`)) {
    return normalizeAppPath(normalizedPathname.slice(basenamePrefix.length));
  }
  return normalizedPathname;
}

function joinAppPath(routerBasename: string, appPath: string): string {
  const basename = normalizeRouterBasename(routerBasename);
  const normalizedAppPath = normalizeAppPath(appPath);
  if (basename === '/') {
    return normalizedAppPath;
  }
  if (normalizedAppPath === '/') {
    return basename;
  }
  return `${basename.slice(0, -1)}${normalizedAppPath}`;
}

function buildHostedUrl(frontendOrigin: string, routerBasename: string): string {
  if (routerBasename === '/') {
    return `${frontendOrigin}/`;
  }
  return `${frontendOrigin}${routerBasename}`;
}

function withAppPath(context: ShellContext, appPath: string, search = '', hash = ''): ShellContext {
  const url = new URL(context.windowUrl);
  url.pathname = joinAppPath(context.routerBasename, appPath);
  url.search = search;
  url.hash = hash;
  const hostedUrl = url.toString();
  return {
    ...context,
    windowUrl: hostedUrl,
    browserUrl: hostedUrl,
  };
}

function defaultFrontendOrigin(runtimeMode: ShellRuntimeMode): string {
  if (runtimeMode === 'dev') {
    const devPort = process.env.BMS_DEV_WEB_HOST_PORT || '5173';
    return `http://127.0.0.1:${devPort}`;
  }
  const stablePort = process.env.BMS_WEB_HOST_PORT || '18080';
  return `http://127.0.0.1:${stablePort}`;
}

export function resolveShellContext(options: Partial<ShellContext> = {}): ShellContext {
  const runtimeMode = options.runtimeMode ?? (process.env.BMS_RUNTIME_MODE === 'dev' ? 'dev' : 'container');
  const frontendOrigin = normalizeOrigin(
    options.frontendOrigin ?? process.env.BMS_FRONTEND_ORIGIN ?? defaultFrontendOrigin(runtimeMode),
  );
  const routerBasename = normalizeRouterBasename(
    options.routerBasename ?? process.env.BMS_ROUTER_BASENAME ?? (runtimeMode === 'dev' ? '/' : '/bms/'),
  );
  const hostedUrl = buildHostedUrl(frontendOrigin, routerBasename);

  return {
    runtimeMode,
    frontendOrigin,
    routerBasename,
    windowUrl: options.windowUrl ?? hostedUrl,
    browserUrl: options.browserUrl ?? hostedUrl,
  };
}

export type RuntimeSwitchContextOptions = {
  currentContext: ShellContext;
  currentUrl: string;
  targetRuntimeMode: ShellRuntimeMode;
};

function resolveRuntimeSwitchBaseContext(runtimeMode: ShellRuntimeMode): ShellContext {
  return resolveShellContext({
    runtimeMode,
    frontendOrigin: defaultFrontendOrigin(runtimeMode),
    routerBasename: runtimeMode === 'dev' ? '/' : '/bms/',
  });
}

export function resolveRuntimeSwitchContext(options: RuntimeSwitchContextOptions): ShellContext {
  const nextContext = resolveRuntimeSwitchBaseContext(options.targetRuntimeMode);
  try {
    const currentUrl = new URL(options.currentUrl);
    const appPath = getCurrentAppPath(currentUrl.pathname, options.currentContext.routerBasename);
    return withAppPath(nextContext, appPath, currentUrl.search, currentUrl.hash);
  } catch {
    return nextContext;
  }
}

export function buildBrowserWindowOptions(preloadPath: string): BrowserWindowConstructorOptions {
  return {
    title: 'BioModStack',
    width: 1600,
    height: 1024,
    minWidth: 1200,
    minHeight: 800,
    show: false,
    autoHideMenuBar: false,
    backgroundColor: '#0b1020',
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
      partition: SHELL_STORAGE_PARTITION,
      backgroundThrottling: false,
    },
  };
}
