import { contextBridge, ipcRenderer } from 'electron';

import {
  GET_SHELL_CONTEXT_CHANNEL,
  GET_STATUS_CHANNEL,
  OPEN_IN_BROWSER_CHANNEL,
  RESTART_ALL_CHANNEL,
  RESTART_API_CHANNEL,
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
};

contextBridge.exposeInMainWorld('__BMS_ROUTER_BASENAME__', shellContext.routerBasename);
contextBridge.exposeInMainWorld('biomodstack', biomodstackApi);
