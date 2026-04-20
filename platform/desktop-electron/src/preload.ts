import { contextBridge, ipcRenderer } from 'electron';

import { resolvePointerZoomStep } from './pointerZoom.js';
import {
  ADJUST_ZOOM_CHANNEL,
  GET_SHELL_CONTEXT_CHANNEL,
  GET_STATUS_CHANNEL,
  GET_ZOOM_FACTOR_CHANNEL,
  OPEN_IN_BROWSER_CHANNEL,
  RESET_ZOOM_CHANNEL,
  RESTART_ALL_CHANNEL,
  RESTART_API_CHANNEL,
  SET_ZOOM_FACTOR_CHANNEL,
  START_ALL_CHANNEL,
  STOP_ALL_CHANNEL,
  resolveShellContext,
  type BiomodstackDesktopApi,
  type ShellRuntimeMode,
} from './windowState';

declare global {
  interface Window {
    biomodstack: BiomodstackDesktopApi;
    __BMS_ROUTER_BASENAME__: string;
  }
}

const shellContext = resolveShellContext();

const biomodstackApi: BiomodstackDesktopApi = {
  getShellContext: () => ipcRenderer.invoke(GET_SHELL_CONTEXT_CHANNEL),
  getStatus: (runtimeMode?: ShellRuntimeMode) => ipcRenderer.invoke(GET_STATUS_CHANNEL, runtimeMode),
  startAll: (runtimeMode?: ShellRuntimeMode) => ipcRenderer.invoke(START_ALL_CHANNEL, runtimeMode),
  stopAll: (runtimeMode?: ShellRuntimeMode) => ipcRenderer.invoke(STOP_ALL_CHANNEL, runtimeMode),
  restartAll: (runtimeMode?: ShellRuntimeMode) => ipcRenderer.invoke(RESTART_ALL_CHANNEL, runtimeMode),
  restartApi: (runtimeMode?: ShellRuntimeMode) => ipcRenderer.invoke(RESTART_API_CHANNEL, runtimeMode),
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
