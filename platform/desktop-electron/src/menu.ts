import { Menu, type MenuItemConstructorOptions } from 'electron';

import type { ServiceControl } from './serviceControl.js';
import type { ShellContext } from './windowState.js';

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
  hideShell?: () => void;
  quitShell?: () => void;
  showAbout?: () => void;
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
): MenuItemConstructorOptions[] {
  return [
    {
      label: 'Start Services',
      click: fireAndForget(() => serviceControl.startAll(context.runtimeMode)),
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
  ];
}

export function buildApplicationMenuTemplate(
  context: ShellContext,
  serviceControl: ServiceControl,
  deps: MenuDeps = {},
): MenuItemConstructorOptions[] {
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
        {
          label: 'Open Results Folder',
          click: fireAndForget(() => deps.openResultsFolder?.()),
        },
        {
          label: 'Open Shell Data Folder',
          click: fireAndForget(() => deps.openShellDataFolder?.()),
        },
        {
          label: 'Logs',
          submenu: buildLogsSubmenu(deps),
        },
        {
          label: 'Services',
          submenu: buildServicesSubmenu(context, serviceControl),
        },
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
      label: 'View',
      submenu: [
        {
          label: 'Reload Shell',
          role: 'reload',
          click: () => deps.reloadShell?.(),
        },
        {
          label: 'Force Reload',
          role: 'forceReload',
        },
        {
          label: 'Toggle Developer Tools',
          role: 'toggleDevTools',
        },
        { type: 'separator' },
        {
          label: 'Reset Zoom',
          role: 'resetZoom',
        },
        {
          label: 'Zoom In',
          role: 'zoomIn',
        },
        {
          label: 'Zoom Out',
          role: 'zoomOut',
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
