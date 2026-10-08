import { Menu, type MenuItemConstructorOptions } from 'electron';

import type { ServiceControl } from './serviceControl.js';
import type { ShellContext, ShellRuntimeMode } from './windowState.js';
import { DEFAULT_ZOOM_FACTOR, ZOOM_PRESETS, formatZoomPercentage } from './zoom.js';

type MenuDeps = {
  showWindow?: () => void;
  openInBrowser?: () => Promise<void> | void;
  copyLocalUrl?: (url: string) => void;
  openResultsFolder?: () => Promise<void> | void;
  openShellDataFolder?: () => Promise<void> | void;
  openApiLog?: () => Promise<void> | void;
  openFrontendLog?: () => Promise<void> | void;
  openCoreRuntimeLog?: () => Promise<void> | void;
  reloadShell?: () => void;
  switchRuntime?: (runtimeMode: ShellRuntimeMode) => Promise<void> | void;
  hideShell?: () => void;
  quitShell?: () => void;
  showAbout?: () => void;
  getZoomFactor?: () => number;
  adjustZoom?: (deltaSteps: number) => void;
  setZoomFactor?: (zoomFactor: number) => void;
  resetZoom?: () => void;
  isAlwaysOnTop?: () => boolean;
  toggleAlwaysOnTop?: () => void;
};

function fireAndForget(action: () => Promise<void> | void): () => void {
  return () => {
    try {
      const result = action();
      if (result && typeof (result as Promise<void>).catch === 'function') {
        void (result as Promise<void>).catch((error) => {
          console.error(error);
        });
      }
    } catch (error) {
      console.error(error);
    }
  };
}

function buildLogsSubmenu(deps: MenuDeps): MenuItemConstructorOptions[] {
  return [
    {
      label: 'Open API Log',
      click: fireAndForget(() => deps.openApiLog?.()),
    },
    {
      label: 'Open Frontend Log',
      click: fireAndForget(() => deps.openFrontendLog?.()),
    },
    {
      label: 'Open Core Runtime Log',
      click: fireAndForget(() => deps.openCoreRuntimeLog?.()),
    },
  ];
}

function buildServicesSubmenu(
  context: ShellContext,
  serviceControl: ServiceControl,
  deps: MenuDeps,
): MenuItemConstructorOptions[] {
  return [
    {
      label: 'Start Services',
      click: fireAndForget(() => serviceControl.startAll(context.runtimeMode)),
    },
    {
      label: 'Start Dev + Stable Services',
      click: fireAndForget(() => serviceControl.startRuntimeTarget('both')),
    },
    {
      label: 'Stop Services',
      click: fireAndForget(() => serviceControl.stopAll(context.runtimeMode)),
    },
    {
      label: 'Restart Services',
      click: fireAndForget(() => serviceControl.restartAll(context.runtimeMode)),
    },
    {
      label: 'Restart API',
      click: fireAndForget(() => serviceControl.restartApi(context.runtimeMode)),
    },
    { type: 'separator' },
    {
      label: 'Logs',
      submenu: buildLogsSubmenu(deps),
    },
  ];
}

function currentRuntimeChannelLabel(context: ShellContext): string {
  return context.runtimeMode === 'dev' ? 'Current Channel: Vite dev' : 'Current Channel: Stable /bms/';
}

function buildRuntimeChannelSubmenu(context: ShellContext, deps: MenuDeps): MenuItemConstructorOptions[] {
  return [
    {
      label: currentRuntimeChannelLabel(context),
      enabled: false,
    },
    {
      label: 'Switch to Vite Dev',
      enabled: context.runtimeMode !== 'dev',
      click: fireAndForget(() => deps.switchRuntime?.('dev')),
    },
    {
      label: 'Switch to Stable /bms/',
      enabled: context.runtimeMode !== 'container',
      click: fireAndForget(() => deps.switchRuntime?.('container')),
    },
  ];
}

function buildZoomPresetsSubmenu(deps: MenuDeps): MenuItemConstructorOptions[] {
  const currentZoomFactor = deps.getZoomFactor?.() ?? DEFAULT_ZOOM_FACTOR;

  return ZOOM_PRESETS.map((zoomPreset) => ({
    label: formatZoomPercentage(zoomPreset),
    type: 'radio',
    checked: Math.abs(currentZoomFactor - zoomPreset) < 0.001,
    click: () => deps.setZoomFactor?.(zoomPreset),
  }));
}

export function buildApplicationMenuTemplate(
  context: ShellContext,
  serviceControl: ServiceControl,
  deps: MenuDeps = {},
): MenuItemConstructorOptions[] {
  const currentZoomLabel = formatZoomPercentage(deps.getZoomFactor?.() ?? DEFAULT_ZOOM_FACTOR);

  return [
    {
      label: 'BioModStack',
      submenu: [
        {
          label: 'Open BioModStack',
          click: () => deps.showWindow?.(),
        },
        {
          label: 'Open in Browser',
          click: fireAndForget(() => deps.openInBrowser?.()),
        },
        {
          label: 'Copy Local URL',
          click: () => deps.copyLocalUrl?.(context.browserUrl),
        },
        { type: 'separator' },
        {
          label: 'Open Results Folder',
          click: fireAndForget(() => deps.openResultsFolder?.()),
        },
        {
          label: 'Open Shell Data Folder',
          click: fireAndForget(() => deps.openShellDataFolder?.()),
        },
        { type: 'separator' },
        {
          label: 'Hide to Tray',
          click: () => deps.hideShell?.(),
        },
        {
          label: 'Quit Shell',
          click: () => deps.quitShell?.(),
        },
      ],
    },
    {
      label: 'Services',
      submenu: buildServicesSubmenu(context, serviceControl, deps),
    },
    {
      label: 'View',
      submenu: [
        {
          label: 'Runtime Channel',
          submenu: buildRuntimeChannelSubmenu(context, deps),
        },
        { type: 'separator' },
        {
          label: 'Reload Shell',
          accelerator: 'CommandOrControl+R',
          click: () => deps.reloadShell?.(),
        },
        {
          label: 'Toggle Developer Tools',
          role: 'toggleDevTools',
        },
        { type: 'separator' },
        {
          label: `Current Zoom: ${currentZoomLabel}`,
          enabled: false,
        },
        {
          label: 'Zoom Out',
          accelerator: 'CommandOrControl+-',
          click: () => deps.adjustZoom?.(-1),
        },
        {
          label: 'Reset Zoom',
          accelerator: 'CommandOrControl+0',
          click: () => deps.resetZoom?.(),
        },
        {
          label: 'Zoom In',
          accelerator: 'CommandOrControl+=',
          click: () => deps.adjustZoom?.(1),
        },
        {
          label: 'Zoom Presets',
          submenu: buildZoomPresetsSubmenu(deps),
        },
        { type: 'separator' },
        {
          label: 'Toggle Full Screen',
          role: 'togglefullscreen',
        },
      ],
    },
    {
      label: 'Window',
      role: 'window',
      submenu: [
        {
          label: 'Minimize Window',
          role: 'minimize',
        },
        {
          label: 'Close Window',
          role: 'close',
        },
        {
          label: 'Always on Top',
          type: 'checkbox',
          checked: deps.isAlwaysOnTop?.() ?? false,
          click: () => deps.toggleAlwaysOnTop?.(),
        },
      ],
    },
    {
      label: 'Help',
      submenu: [
        {
          label: 'About BioModStack Shell',
          click: () => deps.showAbout?.(),
        },
      ],
    },
  ];
}

export function buildApplicationMenu(
  context: ShellContext,
  serviceControl: ServiceControl,
  deps: MenuDeps = {},
): Menu {
  return Menu.buildFromTemplate(buildApplicationMenuTemplate(context, serviceControl, deps));
}
