import path from 'node:path';

import {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  shell,
  type MenuItemConstructorOptions,
} from 'electron';

import {
  buildBrowserWindowOptions,
  GET_SHELL_CONTEXT_CHANNEL,
  OPEN_IN_BROWSER_CHANNEL,
  resolveShellContext,
  type ShellContext,
} from './windowState';

let mainWindow: BrowserWindow | null = null;

function buildApplicationMenu(context: ShellContext): Menu {
  const template: MenuItemConstructorOptions[] = [
    {
      label: 'BioModStack',
      submenu: [
        {
          label: 'Open in Browser',
          click: () => {
            void shell.openExternal(context.browserUrl);
          },
        },
        { type: 'separator' },
        {
          label: 'Reload Shell',
          click: () => {
            mainWindow?.reload();
          },
        },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
      ],
    },
    {
      role: 'window',
      submenu: [{ role: 'minimize' }, { role: 'close' }],
    },
  ];

  return Menu.buildFromTemplate(template);
}

function registerIpcHandlers(context: ShellContext): void {
  ipcMain.handle(GET_SHELL_CONTEXT_CHANNEL, async () => context);
  ipcMain.handle(OPEN_IN_BROWSER_CHANNEL, async () => {
    await shell.openExternal(context.browserUrl);
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
  Menu.setApplicationMenu(buildApplicationMenu(context));
  registerIpcHandlers(context);
  mainWindow = createMainWindow(context);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createMainWindow(context);
    }
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
