import { Menu, type MenuItemConstructorOptions, Tray, nativeImage } from 'electron';

import type { ServiceControl } from './serviceControl';
import type { ShellContext } from './windowState';

type TrayDeps = {
  showWindow?: () => void;
  openInBrowser?: () => Promise<void> | void;
  copyLocalUrl?: (url: string) => void;
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
      label: 'Quit Shell',
      click: () => deps.quitShell?.(),
    },
  ];
}

export function createAppTray(
  context: ShellContext,
  serviceControl: ServiceControl,
  deps: TrayDeps = {},
): Tray {
  const tray = new Tray(nativeImage.createEmpty());
  tray.setToolTip('BioModStack');
  tray.setContextMenu(Menu.buildFromTemplate(buildTrayMenuTemplate(context, serviceControl, deps)));
  return tray;
}
