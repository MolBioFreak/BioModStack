import fs from 'node:fs';
import path from 'node:path';

import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, shell, Tray } from 'electron';

import { buildApplicationMenu } from './menu.js';
import { applyShellGraphicsWorkarounds } from './graphicsWorkarounds.js';
import { resolveShellPaths } from './shellPaths.js';
import { createServiceControl } from './serviceControl.js';
import { createAppTray } from './tray.js';
import { attachCloseToTrayBehavior } from './windowLifecycle.js';
import { attachWindowDiagnostics } from './windowDiagnostics.js';
import {
  ADJUST_ZOOM_CHANNEL,
  buildBrowserWindowOptions,
  GET_SHELL_CONTEXT_CHANNEL,
  GET_STATUS_CHANNEL,
  GET_ZOOM_FACTOR_CHANNEL,
  OPEN_IN_BROWSER_CHANNEL,
  RESET_ZOOM_CHANNEL,
  RESTART_ALL_CHANNEL,
  RESTART_API_CHANNEL,
  resolveShellContext,
  SET_ZOOM_FACTOR_CHANNEL,
  START_ALL_CHANNEL,
  STOP_ALL_CHANNEL,
  type ShellContext,
  type ShellRuntimeMode,
} from './windowState.js';
import {
  DEFAULT_ZOOM_FACTOR,
  adjustZoomFactor,
  clampZoomFactor,
  readPersistedZoomFactor,
  writePersistedZoomFactor,
} from './zoom.js';

let mainWindow: BrowserWindow | null = null;
let appTray: Tray | null = null;
let isQuitting = false;
let refreshApplicationMenuState = () => undefined;

const gpuAccelerationDisabled = applyShellGraphicsWorkarounds(app);
if (gpuAccelerationDisabled) {
  console.warn(
    '[BioModStack Shell] Disabled GPU acceleration on Linux/X11 to avoid Electron renderer blank-screen crashes. Set BMS_ELECTRON_DISABLE_GPU=0 to opt out.',
  );
}

function getZoomSettingsPath(): string {
  return path.join(app.getPath('userData'), 'shell-zoom.json');
}

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

function readCurrentZoomFactor(): number {
  if (mainWindow) {
    return clampZoomFactor(mainWindow.webContents.getZoomFactor());
  }
  return readPersistedZoomFactor(getZoomSettingsPath());
}

function setShellZoomFactor(zoomFactor: number): number {
  const normalizedZoomFactor = clampZoomFactor(zoomFactor);
  if (mainWindow) {
    mainWindow.webContents.setZoomFactor(normalizedZoomFactor);
  }
  writePersistedZoomFactor(getZoomSettingsPath(), normalizedZoomFactor);
  return normalizedZoomFactor;
}

function adjustShellZoom(deltaSteps: number): number {
  return setShellZoomFactor(adjustZoomFactor(readCurrentZoomFactor(), deltaSteps));
}

function resetShellZoom(): number {
  return setShellZoomFactor(DEFAULT_ZOOM_FACTOR);
}

function buildMenuAndTray(context: ShellContext) {
  const serviceControl = createServiceControl();
  const shellPaths = resolveShellPaths();
  let refreshApplicationMenu = () => undefined;

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
    getZoomFactor: () => readCurrentZoomFactor(),
    adjustZoom: (deltaSteps: number) => {
      adjustShellZoom(deltaSteps);
      refreshApplicationMenu();
    },
    setZoomFactor: (zoomFactor: number) => {
      setShellZoomFactor(zoomFactor);
      refreshApplicationMenu();
    },
    resetZoom: () => {
      resetShellZoom();
      refreshApplicationMenu();
    },
    isAlwaysOnTop: () => mainWindow?.isAlwaysOnTop() ?? false,
    toggleAlwaysOnTop: () => {
      if (!mainWindow) {
        return;
      }
      mainWindow.setAlwaysOnTop(!mainWindow.isAlwaysOnTop());
      refreshApplicationMenu();
    },
    iconPath: shellPaths.trayIconPath,
  };

  refreshApplicationMenu = () => {
    Menu.setApplicationMenu(buildApplicationMenu(context, serviceControl, deps));
  };
  refreshApplicationMenuState = refreshApplicationMenu;

  const tray = createAppTray(context, serviceControl, deps);
  return {
    tray,
    refreshApplicationMenu,
  };
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
  ipcMain.handle(GET_ZOOM_FACTOR_CHANNEL, async () => readCurrentZoomFactor());
  ipcMain.handle(SET_ZOOM_FACTOR_CHANNEL, async (_event, requestedZoomFactor: number) => {
    const zoomFactor = setShellZoomFactor(requestedZoomFactor);
    refreshApplicationMenuState();
    return zoomFactor;
  });
  ipcMain.handle(ADJUST_ZOOM_CHANNEL, async (_event, deltaSteps: number) => {
    const zoomFactor = adjustShellZoom(deltaSteps);
    refreshApplicationMenuState();
    return zoomFactor;
  });
  ipcMain.handle(RESET_ZOOM_CHANNEL, async () => {
    const zoomFactor = resetShellZoom();
    refreshApplicationMenuState();
    return zoomFactor;
  });
}

function attachExternalLinkHandler(window: BrowserWindow): void {
  window.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: 'deny' };
  });
}

function reportFatalShellError(scope: string, error: unknown): void {
  const details = error instanceof Error ? error.stack ?? error.message : String(error);
  console.error(`[BioModStack Shell] ${scope}: ${details}`);
  dialog.showErrorBox('BioModStack Shell diagnostics', `${scope}\n\n${details}`);
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

  window.webContents.setZoomFactor(readPersistedZoomFactor(getZoomSettingsPath()));
  attachExternalLinkHandler(window);
  attachCloseToTrayBehavior(window, () => isQuitting);
  attachWindowDiagnostics(window, context, {
    showErrorBox: (title, content) => {
      dialog.showErrorBox(title, content);
    },
  });
  window.webContents.session.clearCache().catch((error: unknown) => {
    const details = error instanceof Error ? error.message : String(error);
    console.warn(`[BioModStack Shell] Failed to clear HTTP cache before loading frontend: ${details}`);
  }).finally(() => {
    void window.loadURL(context.windowUrl);
  });

  return window;
}

async function bootstrap(): Promise<void> {
  const context = resolveShellContext();
  const { tray, refreshApplicationMenu } = buildMenuAndTray(context);

  app.setAboutPanelOptions({
    applicationName: 'BioModStack Shell',
    applicationVersion: app.getVersion(),
    version: app.getVersion(),
    iconPath: resolveShellPaths().appIconPath,
  });

  appTray = tray;
  appTray.on('double-click', () => {
    showMainWindow();
  });

  registerIpcHandlers(context, context.runtimeMode);
  mainWindow = createMainWindow(context);
  refreshApplicationMenu();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createMainWindow(context);
      refreshApplicationMenu();
      return;
    }
    showMainWindow();
  });
}

app.on('before-quit', () => {
  isQuitting = true;
});

app.whenReady().then(() => {
  return bootstrap();
}).catch((error: unknown) => {
  reportFatalShellError('Failed to bootstrap the shell', error);
  app.quit();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
