import fs from 'node:fs';

import { Menu, type MenuItemConstructorOptions, Tray, nativeImage } from 'electron';

import type { ServiceControl } from './serviceControl.js';
import type { ShellContext } from './windowState.js';

type TrayDeps = {
  showWindow?: () => void;
  openInBrowser?: () => Promise<void> | void;
  copyLocalUrl?: (url: string) => void;
  openResultsFolder?: () => Promise<void> | void;
  openShellDataFolder?: () => Promise<void> | void;
  openApiLog?: () => Promise<void> | void;
  openFrontendLog?: () => Promise<void> | void;
  openCoreRuntimeLog?: () => Promise<void> | void;
  quitShell?: () => void;
  iconPath?: string;
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

function buildLogsSubmenu(deps: TrayDeps): MenuItemConstructorOptions[] {
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

export function buildTrayMenuTemplate(
  context: ShellContext,
  serviceControl: ServiceControl,
  deps: TrayDeps = {},
): MenuItemConstructorOptions[] {
  return [
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
    { type: 'separator' },
    {
      label: 'Quit Shell',
      click: () => deps.quitShell?.(),
    },
  ];
}

function createTrayImage(iconPath?: string) {
  if (iconPath && fs.existsSync(iconPath)) {
    const image = nativeImage.createFromPath(iconPath);
    if (!image.isEmpty()) {
      return image;
    }
  }
  return nativeImage.createEmpty();
}

export function createAppTray(
  context: ShellContext,
  serviceControl: ServiceControl,
  deps: TrayDeps = {},
): Tray {
  const tray = new Tray(createTrayImage(deps.iconPath));
  tray.setToolTip('BioModStack');
  tray.setContextMenu(Menu.buildFromTemplate(buildTrayMenuTemplate(context, serviceControl, deps)));
  return tray;
}
