import assert from 'node:assert/strict';
import test from 'node:test';

import type { MenuItemConstructorOptions } from 'electron';

import { buildApplicationMenuTemplate } from '../src/menu.js';
import { buildTrayMenuTemplate } from '../src/tray.js';
import type { ServiceControl } from '../src/serviceControl.js';
import type { ShellContext, ShellRuntimeMode } from '../src/windowState.js';

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

function findMenuItem(items: MenuItemConstructorOptions[], label: string): MenuItemConstructorOptions | null {
  for (const item of items) {
    if (item.label === label) {
      return item;
    }
    if (Array.isArray(item.submenu)) {
      const found = findMenuItem(item.submenu, label);
      if (found) {
        return found;
      }
    }
  }
  return null;
}

function createControlStub(): ServiceControl {
  return {
    getStatus: async () => ({ runtime_mode: 'container' }),
    startAll: async () => undefined,
    startRuntimeTarget: async () => undefined,
    stopAll: async () => undefined,
    restartAll: async () => undefined,
    restartApi: async () => undefined,
  };
}

const context: ShellContext = {
  runtimeMode: 'container',
  frontendOrigin: 'http://127.0.0.1:18080',
  routerBasename: '/bms/',
  windowUrl: 'http://127.0.0.1:18080/bms/',
  browserUrl: 'http://127.0.0.1:18080/bms/',
};

test('application menu exposes shell navigation, service controls, runtime switching, and shell zoom/system settings', () => {
  const labels = flattenLabels(buildApplicationMenuTemplate(context, createControlStub(), {
    getZoomFactor: () => 1,
    isAlwaysOnTop: () => false,
  }));

  assert.deepEqual(labels, [
    'BioModStack',
    'Open BioModStack',
    'Open in Browser',
    'Copy Local URL',
    'Open Results Folder',
    'Open Shell Data Folder',
    'Hide to Tray',
    'Quit Shell',
    'Services',
    'Start Services',
    'Start Dev + Stable Services',
    'Stop Services',
    'Restart Services',
    'Restart API',
    'Logs',
    'Open API Log',
    'Open Frontend Log',
    'Open Core Runtime Log',
    'View',
    'Runtime Channel',
    'Current Channel: Stable /bms/',
    'Switch to Vite Dev',
    'Switch to Stable /bms/',
    'Reload Shell',
    'Toggle Developer Tools',
    'Current Zoom: 100%',
    'Zoom Out',
    'Reset Zoom',
    'Zoom In',
    'Zoom Presets',
    '80%',
    '90%',
    '100%',
    '110%',
    '125%',
    '150%',
    'Toggle Full Screen',
    'Window',
    'Minimize Window',
    'Close Window',
    'Always on Top',
    'Help',
    'About BioModStack Shell',
  ]);
});

test('runtime switch menu actions invoke the requested channel without arbitrary urls', () => {
  const switches: ShellRuntimeMode[] = [];
  const template = buildApplicationMenuTemplate(context, createControlStub(), {
    switchRuntime: async (mode) => {
      switches.push(mode);
    },
  });
  const devItem = findMenuItem(template, 'Switch to Vite Dev');
  const stableItem = findMenuItem(template, 'Switch to Stable /bms/');

  devItem?.click?.({} as never, {} as never, {} as never);
  stableItem?.click?.({} as never, {} as never, {} as never);

  assert.deepEqual(switches, ['dev', 'container']);
});

test('tray menu mirrors important shell actions including runtime switching without forcing browser-only workflows', () => {
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
    'Start Dev + Stable Services',
    'Stop Services',
    'Restart Services',
    'Restart API',
    'Runtime Channel',
    'Current Channel: Stable /bms/',
    'Switch to Vite Dev',
    'Switch to Stable /bms/',
    'Quit Shell',
  ]);
});
