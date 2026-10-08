import path from 'node:path';

import { app, BrowserWindow, clipboard, ipcMain, Menu, shell, Tray } from 'electron';

import { buildApplicationMenu } from './menu';
import { createServiceControl } from './serviceControl';
import { createAppTray } from './tray';
import {
  buildBrowserWindowOptions,
  GET_SHELL_CONTEXT_CHANNEL,
  GET_STATUS_CHANNEL,
  OPEN_IN_BROWSER_CHANNEL,
  RESTART_ALL_CHANNEL,
  RESTART_API_CHANNEL,
  resolveShellContext,
  START_ALL_CHANNEL,
  STOP_ALL_CHANNEL,
  type ShellContext,
  type ShellRuntimeMode,
} from './windowState';

let mainWindow: BrowserWindow | null = null;
let appTray: Tray | null = null;

function showMainWindow(): void {
  if (!mainWindow) {
    return;
  }
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  if (!mainWindow.isVisible()) {
    mainWindow.show();
  }
  mainWindow.focus();
}

function openInBrowser(context: ShellContext): Promise<void> {
  return shell.openExternal(context.browserUrl);
}

function buildMenuAndTray(context: ShellContext) {
  const serviceControl = createServiceControl();
  const deps = {
    openInBrowser: () => openInBrowser(context),
    copyLocalUrl: (url: string) => clipboard.writeText(url),
    reloadShell: () => mainWindow?.reload(),
    quitShell: () => app.quit(),
    showWindow: () => showMainWindow(),
  };

  const menu = buildApplicationMenu(context, serviceControl, deps);
  const tray = createAppTray(context, serviceControl, deps);
  return { menu, tray, serviceControl };
}

function registerIpcHandlers(context: ShellContext, runtimeMode: ShellRuntimeMode): void {
  const serviceControl = createServiceControl();

  ipcMain.handle(GET_SHELL_CONTEXT_CHANNEL, async () => context);
  ipcMain.handle(GET_STATUS_CHANNEL, async (_event, requestedRuntimeMode?: ShellRuntimeMode) => {
    return await serviceControl.getStatus(requestedRuntimeMode ?? runtimeMode);
  });
  ipcMain.handle(START_ALL_CHANNEL, async (_event, requestedRuntimeMode?: ShellRuntimeMode) => {
    await serviceControl.startAll(requestedRuntimeMode ?? runtimeMode);
  });
  ipcMain.handle(STOP_ALL_CHANNEL, async (_event, requestedRuntimeMode?: ShellRuntimeMode) => {
    await serviceControl.stopAll(requestedRuntimeMode ?? runtimeMode);
  });
  ipcMain.handle(RESTART_ALL_CHANNEL, async (_event, requestedRuntimeMode?: ShellRuntimeMode) => {
    await serviceControl.restartAll(requestedRuntimeMode ?? runtimeMode);
  });
  ipcMain.handle(RESTART_API_CHANNEL, async (_event, requestedRuntimeMode?: ShellRuntimeMode) => {
    await serviceControl.restartApi(requestedRuntimeMode ?? runtimeMode);
  });
  ipcMain.handle(OPEN_IN_BROWSER_CHANNEL, async () => {
    await openInBrowser(context);
  });
}

function attachExternalLinkHandler(window: BrowserWindow): void {
  window.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: 'deny' };
  });
}

export function createMainWindow(context: ShellContext): BrowserWindow {
  const preloadPath = path.join(__dirname, 'preload.js');
  const window = new BrowserWindow(buildBrowserWindowOptions(preloadPath));

  window.once('ready-to-show', () => {
    window.show();
  });

  attachExternalLinkHandler(window);
  void window.loadURL(context.windowUrl);

  return window;
}

async function bootstrap(): Promise<void> {
  const context = resolveShellContext();
  const { menu, tray } = buildMenuAndTray(context);

  Menu.setApplicationMenu(menu);
  appTray = tray;
  appTray.on('double-click', () => {
    showMainWindow();
  });

  registerIpcHandlers(context, context.runtimeMode);
  mainWindow = createMainWindow(context);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createMainWindow(context);
      return;
    }
    showMainWindow();
  });
}

app.whenReady().then(() => {
  void bootstrap();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
