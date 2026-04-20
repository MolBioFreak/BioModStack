import assert from 'node:assert/strict';
import test from 'node:test';

import type { MenuItemConstructorOptions } from 'electron';

import { buildApplicationMenuTemplate } from '../src/menu.js';
import { buildTrayMenuTemplate } from '../src/tray.js';
import type { ServiceControl } from '../src/serviceControl.js';
import type { ShellContext } from '../src/windowState.js';

function flattenLabels(items: MenuItemConstructorOptions[]): string[] {
  const labels: string[] = [];
  for (const item of items) {
    if (typeof item.label === 'string') {
      labels.push(item.label);
    }
    if (Array.isArray(item.submenu)) {
      labels.push(...flattenLabels(item.submenu));
    }
  }
  return labels;
}

function createControlStub(): ServiceControl {
  return {
    getStatus: async () => ({ runtime_mode: 'container' }),
    startAll: async () => undefined,
    stopAll: async () => undefined,
    restartAll: async () => undefined,
    restartApi: async () => undefined,
  };
}

const context: ShellContext = {
  runtimeMode: 'container',
  frontendOrigin: 'http://127.0.0.1:5173',
  routerBasename: '/bms/',
  windowUrl: 'http://127.0.0.1:5173/bms/',
  browserUrl: 'http://127.0.0.1:5173/bms/',
};

test('application menu exposes polished shell navigation, logs, data folders, and service actions', () => {
  const labels = flattenLabels(buildApplicationMenuTemplate(context, createControlStub()));

  assert.deepEqual(labels, [
    'BioModStack',
    'Open BioModStack',
    'Open in Browser',
    'Copy Local URL',
    'Open Results Folder',
    'Open Shell Data Folder',
    'Logs',
    'Open API Log',
    'Open Frontend Log',
    'Open Core Runtime Log',
    'Services',
    'Start Services',
    'Stop Services',
    'Restart Services',
    'Restart API',
    'Hide to Tray',
    'Quit Shell',
    'View',
    'Reload Shell',
    'Force Reload',
    'Toggle Developer Tools',
    'Reset Zoom',
    'Zoom In',
    'Zoom Out',
    'Toggle Full Screen',
    'Window',
    'Minimize Window',
    'Close Window',
    'Help',
    'About BioModStack Shell',
  ]);
});

test('tray menu mirrors the most important shell actions without forcing browser-only workflows', () => {
  const labels = flattenLabels(buildTrayMenuTemplate(context, createControlStub()));

  assert.deepEqual(labels, [
    'Open BioModStack',
    'Open in Browser',
    'Copy Local URL',
    'Open Results Folder',
    'Open Shell Data Folder',
    'Logs',
    'Open API Log',
    'Open Frontend Log',
    'Open Core Runtime Log',
    'Services',
    'Start Services',
    'Stop Services',
    'Restart Services',
    'Restart API',
    'Quit Shell',
  ]);
});
