import fs from 'node:fs';
import path from 'node:path';

import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, shell, Tray } from 'electron';

import { buildApplicationMenu } from './menu.js';
import { resolveShellPaths } from './shellPaths.js';
import { createServiceControl } from './serviceControl.js';
import { createAppTray } from './tray.js';
import { attachCloseToTrayBehavior } from './windowLifecycle.js';
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
} from './windowState.js';

let mainWindow: BrowserWindow | null = null;
let appTray: Tray | null = null;
let isQuitting = false;

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

async function openPathTarget(label: string, targetPath: string, ensureDirectory = false): Promise<void> {
  if (ensureDirectory) {
    fs.mkdirSync(targetPath, { recursive: true });
  }

  const errorMessage = await shell.openPath(targetPath);
  if (errorMessage) {
    dialog.showErrorBox(`Unable to open ${label}`, `${errorMessage}\n\n${targetPath}`);
    throw new Error(`${label}: ${errorMessage}`);
  }
}

function buildMenuAndTray(context: ShellContext) {
  const serviceControl = createServiceControl();
  const shellPaths = resolveShellPaths();
  const deps = {
    showWindow: () => showMainWindow(),
    openInBrowser: () => openInBrowser(context),
    copyLocalUrl: (url: string) => clipboard.writeText(url),
    openResultsFolder: () => openPathTarget('results folder', shellPaths.resultsDir, true),
    openShellDataFolder: () => openPathTarget('shell data folder', app.getPath('userData'), true),
    openApiLog: () => openPathTarget('API log', shellPaths.apiLog),
    openFrontendLog: () => openPathTarget('frontend log', shellPaths.frontendLog),
    openCoreRuntimeLog: () => openPathTarget('core runtime log', shellPaths.coreRuntimeLog),
    reloadShell: () => mainWindow?.reload(),
    hideShell: () => mainWindow?.hide(),
    quitShell: () => {
      isQuitting = true;
      app.quit();
    },
    showAbout: () => app.showAboutPanel(),
    iconPath: shellPaths.trayIconPath,
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
  const shellPaths = resolveShellPaths();
  const window = new BrowserWindow({
    ...buildBrowserWindowOptions(preloadPath),
    icon: fs.existsSync(shellPaths.appIconPath) ? shellPaths.appIconPath : undefined,
  });

  window.once('ready-to-show', () => {
    window.show();
  });

  attachExternalLinkHandler(window);
  attachCloseToTrayBehavior(window, () => isQuitting);
  void window.loadURL(context.windowUrl);

  return window;
}

async function bootstrap(): Promise<void> {
  const context = resolveShellContext();
  const { menu, tray } = buildMenuAndTray(context);

  app.setAboutPanelOptions({
    applicationName: 'BioModStack Shell',
    applicationVersion: app.getVersion(),
    version: app.getVersion(),
    iconPath: resolveShellPaths().appIconPath,
  });

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

app.on('before-quit', () => {
  isQuitting = true;
});

app.whenReady().then(() => {
  void bootstrap();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
