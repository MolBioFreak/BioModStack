import { contextBridge, ipcRenderer } from 'electron';

import type {
  BiomodstackDesktopApi,
  ShellContext,
  ShellRuntimeMode,
} from './windowState.js';
import type { ServiceRuntimeTarget } from './serviceControl.js';

declare global {
  interface Window {
    biomodstack: BiomodstackDesktopApi;
    __BMS_ROUTER_BASENAME__: string;
  }
}

const GET_SHELL_CONTEXT_CHANNEL = 'biomodstack:get-shell-context';
const GET_STATUS_CHANNEL = 'biomodstack:get-status';
const START_ALL_CHANNEL = 'biomodstack:start-all';
const STOP_ALL_CHANNEL = 'biomodstack:stop-all';
const RESTART_ALL_CHANNEL = 'biomodstack:restart-all';
const RESTART_API_CHANNEL = 'biomodstack:restart-api';
const SWITCH_RUNTIME_CHANNEL = 'biomodstack:switch-runtime';
const START_RUNTIME_TARGET_CHANNEL = 'biomodstack:start-runtime-target';
const OPEN_IN_BROWSER_CHANNEL = 'biomodstack:open-in-browser';
const GET_ZOOM_FACTOR_CHANNEL = 'biomodstack:get-zoom-factor';
const SET_ZOOM_FACTOR_CHANNEL = 'biomodstack:set-zoom-factor';
const ADJUST_ZOOM_CHANNEL = 'biomodstack:adjust-zoom';
const RESET_ZOOM_CHANNEL = 'biomodstack:reset-zoom';

type PointerZoomGesture = {
  ctrlKey: boolean;
  metaKey: boolean;
  deltaY: number;
};

function resolvePointerZoomStep(gesture: PointerZoomGesture): 1 | -1 | null {
  if ((!gesture.ctrlKey && !gesture.metaKey) || gesture.deltaY === 0) {
    return null;
  }
  return gesture.deltaY < 0 ? 1 : -1;
}

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

function buildHostedUrl(frontendOrigin: string, routerBasename: string): string {
  if (routerBasename === '/') {
    return `${frontendOrigin}/`;
  }
  return `${frontendOrigin}${routerBasename}`;
}

function defaultFrontendOrigin(runtimeMode: ShellRuntimeMode): string {
  if (runtimeMode === 'dev') {
    const devPort = process.env.BMS_DEV_WEB_HOST_PORT || '18082';
    return `http://127.0.0.1:${devPort}`;
  }
  const stablePort = process.env.BMS_WEB_HOST_PORT || '18080';
  return `http://127.0.0.1:${stablePort}`;
}

function resolvePreloadShellContext(options: Partial<ShellContext> = {}): ShellContext {
  const runtimeMode = options.runtimeMode ?? (process.env.BMS_RUNTIME_MODE === 'dev' ? 'dev' : 'container');
  const frontendOrigin = normalizeOrigin(
    options.frontendOrigin
      ?? process.env.BMS_ACTIVE_FRONTEND_ORIGIN
      ?? process.env.BMS_FRONTEND_ORIGIN
      ?? defaultFrontendOrigin(runtimeMode),
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

const shellContext = resolvePreloadShellContext();

const biomodstackApi: BiomodstackDesktopApi = {
  getShellContext: () => ipcRenderer.invoke(GET_SHELL_CONTEXT_CHANNEL),
  getStatus: (runtimeMode?: ShellRuntimeMode) => ipcRenderer.invoke(GET_STATUS_CHANNEL, runtimeMode),
  startAll: (runtimeMode?: ShellRuntimeMode) => ipcRenderer.invoke(START_ALL_CHANNEL, runtimeMode),
  stopAll: (runtimeMode?: ShellRuntimeMode) => ipcRenderer.invoke(STOP_ALL_CHANNEL, runtimeMode),
  restartAll: (runtimeMode?: ShellRuntimeMode) => ipcRenderer.invoke(RESTART_ALL_CHANNEL, runtimeMode),
  restartApi: (runtimeMode?: ShellRuntimeMode) => ipcRenderer.invoke(RESTART_API_CHANNEL, runtimeMode),
  switchRuntime: (runtimeMode: ShellRuntimeMode) => ipcRenderer.invoke(SWITCH_RUNTIME_CHANNEL, runtimeMode),
  startRuntimeTarget: (target: ServiceRuntimeTarget) => ipcRenderer.invoke(START_RUNTIME_TARGET_CHANNEL, target),
  openInBrowser: () => ipcRenderer.invoke(OPEN_IN_BROWSER_CHANNEL),
  getZoomFactor: () => ipcRenderer.invoke(GET_ZOOM_FACTOR_CHANNEL),
  setZoomFactor: (zoomFactor: number) => ipcRenderer.invoke(SET_ZOOM_FACTOR_CHANNEL, zoomFactor),
  adjustZoom: (deltaSteps: number) => ipcRenderer.invoke(ADJUST_ZOOM_CHANNEL, deltaSteps),
  resetZoom: () => ipcRenderer.invoke(RESET_ZOOM_CHANNEL),
};

function installPointerZoomShortcut(): void {
  window.addEventListener('wheel', (event) => {
    const zoomStep = resolvePointerZoomStep({
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      deltaY: event.deltaY,
    });
    if (zoomStep === null) {
      return;
    }

    event.preventDefault();
    void biomodstackApi.adjustZoom(zoomStep);
  }, { passive: false, capture: true });
}

contextBridge.exposeInMainWorld('__BMS_ROUTER_BASENAME__', shellContext.routerBasename);
contextBridge.exposeInMainWorld('biomodstack', biomodstackApi);
installPointerZoomShortcut();
