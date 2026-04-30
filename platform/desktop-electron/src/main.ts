import fs from 'node:fs';
import path from 'node:path';

import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, shell, Tray } from 'electron';

import { buildApplicationMenu } from './menu.js';
import { applyShellGraphicsWorkarounds } from './graphicsWorkarounds.js';
import { resolveShellPaths } from './shellPaths.js';
import { createServiceControl, type ServiceRuntimeTarget } from './serviceControl.js';
import { createAppTray } from './tray.js';
import { enforceSingleInstanceLock, attachCloseToTrayBehavior } from './windowLifecycle.js';
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
  resolveRuntimeSwitchContext,
  resolveShellContext,
  SET_ZOOM_FACTOR_CHANNEL,
  START_ALL_CHANNEL,
  START_RUNTIME_TARGET_CHANNEL,
  STOP_ALL_CHANNEL,
  SWITCH_RUNTIME_CHANNEL,
  type ShellContext,
  type ShellRuntimeMode,
} from './windowState.js';
import {
  assertTrustedIpcSender,
  isAllowedExternalUrl,
  isAllowedShellNavigationUrl,
} from './shellSecurity.js';
import {
  DEFAULT_ZOOM_FACTOR,
  adjustZoomFactor,
  clampZoomFactor,
  readPersistedZoomFactor,
  writePersistedZoomFactor,
} from './zoom.js';

let mainWindow: BrowserWindow | null = null;
let appTray: Tray | null = null;
let activeContext: ShellContext | null = null;
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

function getActiveContext(): ShellContext {
  if (!activeContext) {
    throw new Error('BioModStack shell context has not been initialized');
  }
  return activeContext;
}

function applyShellContextEnvironment(context: ShellContext): void {
  process.env.BMS_RUNTIME_MODE = context.runtimeMode;
  process.env.BMS_ACTIVE_FRONTEND_ORIGIN = context.frontendOrigin;
  process.env.BMS_ROUTER_BASENAME = context.routerBasename;
}

function normalizeRequestedRuntimeMode(runtimeMode: unknown): ShellRuntimeMode {
  if (runtimeMode === 'dev' || runtimeMode === 'container') {
    return runtimeMode;
  }
  throw new Error(`Unsupported BioModStack runtime mode: ${String(runtimeMode)}`);
}

function normalizeRequestedRuntimeTarget(target: unknown): ServiceRuntimeTarget {
  if (target === 'dev' || target === 'prod' || target === 'both') {
    return target;
  }
  throw new Error(`Unsupported BioModStack runtime target: ${String(target)}`);
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
  if (!isAllowedExternalUrl(context.browserUrl)) {
    return Promise.reject(new Error(`Refusing to open unsafe BioModStack browser URL: ${context.browserUrl}`));
  }
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

async function switchShellRuntime(requestedRuntimeMode: ShellRuntimeMode): Promise<ShellContext> {
  const runtimeMode = normalizeRequestedRuntimeMode(requestedRuntimeMode);
  const currentContext = getActiveContext();
  const currentUrl = mainWindow?.webContents.getURL() || currentContext.windowUrl;
  const nextContext = resolveRuntimeSwitchContext({
    currentContext,
    currentUrl,
    targetRuntimeMode: runtimeMode,
  });

  activeContext = nextContext;
  applyShellContextEnvironment(nextContext);
  installMenuAndTray(nextContext);

  if (mainWindow) {
    await mainWindow.webContents.session.clearCache().catch((error: unknown) => {
      const details = error instanceof Error ? error.message : String(error);
      console.warn(`[BioModStack Shell] Failed to clear HTTP cache before switching runtime: ${details}`);
    });
    await mainWindow.loadURL(nextContext.windowUrl);
  }

  return nextContext;
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
    switchRuntime: (runtimeMode: ShellRuntimeMode) => switchShellRuntime(runtimeMode).then(() => undefined),
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

function attachTrayHandlers(tray: Tray): void {
  tray.on('double-click', () => {
    showMainWindow();
  });
}

function installMenuAndTray(context: ShellContext): void {
  const { tray, refreshApplicationMenu } = buildMenuAndTray(context);
  if (appTray) {
    appTray.destroy();
  }
  appTray = tray;
  attachTrayHandlers(appTray);
  refreshApplicationMenu();
}

function registerIpcHandlers(getContext: () => ShellContext): void {
  const serviceControl = createServiceControl();
  const trustedHandler = <Args extends unknown[], Result>(
    channel: string,
    handler: (...args: Args) => Promise<Result> | Result,
  ) => {
    ipcMain.handle(channel, async (event, ...args: Args) => {
      assertTrustedIpcSender(event, getContext());
      return await handler(...args);
    });
  };

  trustedHandler(GET_SHELL_CONTEXT_CHANNEL, async () => getContext());
  trustedHandler(GET_STATUS_CHANNEL, async (requestedRuntimeMode?: ShellRuntimeMode) => {
    return await serviceControl.getStatus(requestedRuntimeMode ?? getContext().runtimeMode);
  });
  trustedHandler(START_ALL_CHANNEL, async (requestedRuntimeMode?: ShellRuntimeMode) => {
    await serviceControl.startAll(requestedRuntimeMode ?? getContext().runtimeMode);
  });
  trustedHandler(STOP_ALL_CHANNEL, async (requestedRuntimeMode?: ShellRuntimeMode) => {
    await serviceControl.stopAll(requestedRuntimeMode ?? getContext().runtimeMode);
  });
  trustedHandler(RESTART_ALL_CHANNEL, async (requestedRuntimeMode?: ShellRuntimeMode) => {
    await serviceControl.restartAll(requestedRuntimeMode ?? getContext().runtimeMode);
  });
  trustedHandler(RESTART_API_CHANNEL, async (requestedRuntimeMode?: ShellRuntimeMode) => {
    await serviceControl.restartApi(requestedRuntimeMode ?? getContext().runtimeMode);
  });
  trustedHandler(SWITCH_RUNTIME_CHANNEL, async (requestedRuntimeMode: ShellRuntimeMode) => {
    return await switchShellRuntime(requestedRuntimeMode);
  });
  trustedHandler(START_RUNTIME_TARGET_CHANNEL, async (requestedTarget: ServiceRuntimeTarget) => {
    await serviceControl.startRuntimeTarget(normalizeRequestedRuntimeTarget(requestedTarget));
  });
  trustedHandler(OPEN_IN_BROWSER_CHANNEL, async () => {
    await openInBrowser(getContext());
  });
  trustedHandler(GET_ZOOM_FACTOR_CHANNEL, async () => readCurrentZoomFactor());
  trustedHandler(SET_ZOOM_FACTOR_CHANNEL, async (requestedZoomFactor: number) => {
    const zoomFactor = setShellZoomFactor(requestedZoomFactor);
    refreshApplicationMenuState();
    return zoomFactor;
  });
  trustedHandler(ADJUST_ZOOM_CHANNEL, async (deltaSteps: number) => {
    const zoomFactor = adjustShellZoom(deltaSteps);
    refreshApplicationMenuState();
    return zoomFactor;
  });
  trustedHandler(RESET_ZOOM_CHANNEL, async () => {
    const zoomFactor = resetShellZoom();
    refreshApplicationMenuState();
    return zoomFactor;
  });
}

function attachShellNavigationGuard(window: BrowserWindow, getContext: () => ShellContext): void {
  window.webContents.on('will-navigate', (event, url) => {
    if (!isAllowedShellNavigationUrl(url, getContext())) {
      event.preventDefault();
      console.warn(`[BioModStack Shell] Blocked navigation outside trusted surface: ${url}`);
    }
  });
}

function attachExternalLinkHandler(window: BrowserWindow): void {
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (isAllowedExternalUrl(url)) {
      void shell.openExternal(url);
    } else {
      console.warn(`[BioModStack Shell] Blocked external URL: ${url}`);
    }
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
  attachShellNavigationGuard(window, getActiveContext);
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
  activeContext = context;
  applyShellContextEnvironment(context);

  app.setAboutPanelOptions({
    applicationName: 'BioModStack Shell',
    applicationVersion: app.getVersion(),
    version: app.getVersion(),
    iconPath: resolveShellPaths().appIconPath,
  });

  registerIpcHandlers(getActiveContext);
  mainWindow = createMainWindow(context);
  installMenuAndTray(context);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      const currentContext = getActiveContext();
      mainWindow = createMainWindow(currentContext);
      installMenuAndTray(currentContext);
      return;
    }
    showMainWindow();
  });
}

app.on('before-quit', () => {
  isQuitting = true;
});

const singleInstanceLockAcquired = enforceSingleInstanceLock(app, () => showMainWindow());

if (singleInstanceLockAcquired) {
  app.whenReady().then(() => {
    return bootstrap();
  }).catch((error: unknown) => {
    reportFatalShellError('Failed to bootstrap the shell', error);
    app.quit();
  });
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
