import { contextBridge, ipcRenderer } from 'electron';

import {
  GET_SHELL_CONTEXT_CHANNEL,
  OPEN_IN_BROWSER_CHANNEL,
  resolveShellContext,
  type BiomodstackDesktopApi,
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
  openInBrowser: () => ipcRenderer.invoke(OPEN_IN_BROWSER_CHANNEL),
};

contextBridge.exposeInMainWorld('__BMS_ROUTER_BASENAME__', shellContext.routerBasename);
contextBridge.exposeInMainWorld('biomodstack', biomodstackApi);
