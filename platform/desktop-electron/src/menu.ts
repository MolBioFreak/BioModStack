import { Menu, type MenuItemConstructorOptions } from 'electron';

import type { ServiceControl } from './serviceControl';
import type { ShellContext } from './windowState';

type MenuDeps = {
  openInBrowser?: () => Promise<void> | void;
  copyLocalUrl?: (url: string) => void;
  reloadShell?: () => void;
  quitShell?: () => void;
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
          label: 'Open in Browser',
          click: fireAndForget(() => deps.openInBrowser?.()),
        },
        {
          label: 'Copy Local URL',
          click: () => deps.copyLocalUrl?.(context.browserUrl),
        },
        { type: 'separator' },
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
        { type: 'separator' },
        {
          role: 'quit',
          click: () => deps.quitShell?.(),
        },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload', click: () => deps.reloadShell?.() },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
      ],
    },
    {
      label: 'Window',
      role: 'window',
      submenu: [{ role: 'minimize' }, { role: 'close' }],
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
