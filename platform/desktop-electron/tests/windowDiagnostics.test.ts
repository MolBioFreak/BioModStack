import assert from 'node:assert/strict';
import test from 'node:test';

import {
  attachWindowDiagnostics,
  buildShellFailureDataUrl,
  createPersistentDiagnosticsStore,
} from '../src/windowDiagnostics.js';
import type { ShellContext } from '../src/windowState.js';

type DidFailLoadHandler = (
  event: unknown,
  errorCode: number,
  errorDescription: string,
  validatedURL: string,
  isMainFrame: boolean,
) => void;
type RenderProcessGoneHandler = (
  event: unknown,
  details: { reason: string; exitCode: number },
) => void;
type VoidHandler = () => void;

function createShellContext(): ShellContext {
  return {
    runtimeMode: 'container',
    frontendOrigin: 'http://127.0.0.1:18080',
    routerBasename: '/bms/',
    windowUrl: 'http://127.0.0.1:18080/bms/',
    browserUrl: 'http://127.0.0.1:18080/bms/',
  };
}

function decodeDataUrl(dataUrl: string): string {
  const [, payload = ''] = dataUrl.split(',', 2);
  return decodeURIComponent(payload);
}

function createWindowStub() {
  let didFailLoadHandler: DidFailLoadHandler | undefined;
  let renderProcessGoneHandler: RenderProcessGoneHandler | undefined;
  let unresponsiveHandler: VoidHandler | undefined;
  const loadedUrls: string[] = [];
  let shown = 0;
  let focused = 0;
  let destroyed = false;

  return {
    window: {
      webContents: {
        on: (event: string, handler: DidFailLoadHandler | RenderProcessGoneHandler) => {
          if (event === 'did-fail-load') {
            didFailLoadHandler = handler as DidFailLoadHandler;
          }
          if (event === 'render-process-gone') {
            renderProcessGoneHandler = handler as RenderProcessGoneHandler;
          }
        },
        removeListener: (event: string, handler: DidFailLoadHandler | RenderProcessGoneHandler) => {
          if (event === 'did-fail-load' && didFailLoadHandler === handler) didFailLoadHandler = undefined;
          if (event === 'render-process-gone' && renderProcessGoneHandler === handler) renderProcessGoneHandler = undefined;
        },
      },
      isDestroyed: () => destroyed,
      on: (event: string, handler: VoidHandler) => {
        if (event === 'unresponsive') {
          unresponsiveHandler = handler;
        }
      },
      removeListener: (event: string, handler: VoidHandler) => {
        if (event === 'unresponsive' && unresponsiveHandler === handler) unresponsiveHandler = undefined;
      },
      loadURL: async (url: string) => {
        loadedUrls.push(url);
      },
      show: () => {
        shown += 1;
      },
      focus: () => {
        focused += 1;
      },
    },
    loadedUrls,
    get shown() {
      return shown;
    },
    get focused() {
      return focused;
    },
    destroy() {
      destroyed = true;
    },
    emitDidFailLoad(errorCode: number, errorDescription: string, validatedURL: string, isMainFrame = true) {
      didFailLoadHandler?.({}, errorCode, errorDescription, validatedURL, isMainFrame);
    },
    emitRenderProcessGone(reason: string, exitCode: number) {
      renderProcessGoneHandler?.({}, { reason, exitCode });
    },
    emitUnresponsive() {
      unresponsiveHandler?.();
    },
  };
}

test('buildShellFailureDataUrl renders actionable diagnostics for window-load failures', () => {
  const dataUrl = buildShellFailureDataUrl({
    kind: 'did-fail-load',
    windowUrl: 'http://127.0.0.1:18080/bms/',
    browserUrl: 'http://127.0.0.1:18080/bms/',
    validatedURL: 'http://127.0.0.1:18080/bms/',
    errorCode: -102,
    errorDescription: 'ERR_CONNECTION_REFUSED',
  });

  assert.match(dataUrl, /^data:text\/html;charset=UTF-8,/);

  const html = decodeDataUrl(dataUrl);
  assert.match(html, /BioModStack Shell could not load the UI/);
  assert.match(html, /did-fail-load/);
  assert.match(html, /ERR_CONNECTION_REFUSED/);
  assert.match(html, /-102/);
  assert.match(html, /http:\/\/127\.0\.0\.1:18080\/bms\//);
});

test('attachWindowDiagnostics shows a visible fallback page for main-frame load failures', async () => {
  const windowStub = createWindowStub();
  const errors: string[] = [];
  const dialogs: Array<{ title: string; content: string }> = [];

  attachWindowDiagnostics(windowStub.window as never, createShellContext(), {
    logError: (message: string) => {
      errors.push(message);
    },
    showErrorBox: (title: string, content: string) => {
      dialogs.push({ title, content });
    },
  });

  windowStub.emitDidFailLoad(-102, 'ERR_CONNECTION_REFUSED', 'http://127.0.0.1:18080/bms/');
  await Promise.resolve();

  assert.equal(windowStub.loadedUrls.length, 1);
  assert.match(windowStub.loadedUrls[0] ?? '', /^data:text\/html;charset=UTF-8,/);
  assert.equal(windowStub.shown, 1);
  assert.equal(windowStub.focused, 1);
  assert.equal(dialogs.length, 1);
  assert.match(dialogs[0]?.content ?? '', /ERR_CONNECTION_REFUSED/);
  assert.equal(errors.length, 1);
  assert.match(errors[0] ?? '', /did-fail-load/);
});

test('attachWindowDiagnostics ignores subframe failures and fallback-page reload noise', async () => {
  const windowStub = createWindowStub();
  const dialogs: string[] = [];

  attachWindowDiagnostics(windowStub.window as never, createShellContext(), {
    showErrorBox: (_title: string, content: string) => {
      dialogs.push(content);
    },
  });

  windowStub.emitDidFailLoad(-102, 'ERR_CONNECTION_REFUSED', 'http://127.0.0.1:5173/healthz', false);
  windowStub.emitDidFailLoad(-3, 'ERR_ABORTED', 'data:text/html;charset=UTF-8,%3Chtml%3E', true);
  await Promise.resolve();

  assert.deepEqual(windowStub.loadedUrls, []);
  assert.deepEqual(dialogs, []);
});

test('persistent diagnostics retain fresh runtime context with bounded records and normalized unavailable crash fields', async () => {
  const writes: string[] = [];
  const store = createPersistentDiagnosticsStore({
    path: '/tmp/biomodstack-shell-diagnostics.json',
    maxRecords: 2,
    now: () => '2026-07-15T23:00:00.000Z',
    readFile: () => JSON.stringify([{ timestamp: 'old', kind: 'unresponsive' }]),
    writeFile: (_path, value) => {
      writes.push(value);
    },
    getMemoryContext: () => ({ rssBytes: 1234, externalNoise: 'x'.repeat(4096) }),
    buildIdentity: {
      layer: 'electron',
      revision: '0123456789abcdef0123456789abcdef01234567',
      buildId: 'release-17',
      buildTime: '2026-07-18T04:00:00Z',
      appVersion: '0.2.0',
    },
  });
  const nextContext = {
    ...createShellContext(),
    runtimeMode: 'dev' as const,
    windowUrl: 'http://127.0.0.1:5173/designs',
    browserUrl: 'http://127.0.0.1:5173/designs',
  };
  const windowStub = createWindowStub();

  attachWindowDiagnostics(windowStub.window as never, () => nextContext, { diagnosticsStore: store });
  windowStub.emitUnresponsive();
  windowStub.emitRenderProcessGone('crashed', 137);
  await Promise.resolve();

  assert.equal(writes.length, 2);
  const retained = JSON.parse(writes[1] ?? '[]') as Array<Record<string, unknown>>;
  assert.equal(retained.length, 2);
  assert.deepEqual(retained[0], {
    timestamp: '2026-07-15T23:00:00.000Z',
    kind: 'unresponsive',
    runtimeMode: 'dev',
    windowUrl: 'http://127.0.0.1:5173/designs',
    browserUrl: 'http://127.0.0.1:5173/designs',
    validatedURL: null,
    reason: null,
    exitCode: null,
    errorCode: null,
    errorDescription: null,
    memory: { rssBytes: 1234, externalNoise: 'x'.repeat(1024) },
    build: {
      layer: 'electron',
      revision: '0123456789abcdef0123456789abcdef01234567',
      buildId: 'release-17',
      buildTime: '2026-07-18T04:00:00Z',
      appVersion: '0.2.0',
    },
  });
  assert.equal(retained[1]?.runtimeMode, 'dev');
  assert.equal(retained[1]?.exitCode, 137);
});

test('attachWindowDiagnostics reports renderer crashes and unresponsive renderers immediately', async () => {
  const windowStub = createWindowStub();
  const errors: string[] = [];
  const dialogs: Array<{ title: string; content: string }> = [];

  attachWindowDiagnostics(windowStub.window as never, createShellContext(), {
    logError: (message: string) => {
      errors.push(message);
    },
    showErrorBox: (title: string, content: string) => {
      dialogs.push({ title, content });
    },
  });

  windowStub.emitRenderProcessGone('crashed', 133);
  await Promise.resolve();
  windowStub.emitUnresponsive();

  assert.equal(windowStub.loadedUrls.length, 1);
  assert.match(decodeDataUrl(windowStub.loadedUrls[0] ?? ''), /render-process-gone/);
  assert.equal(dialogs.length, 2);
  assert.match(dialogs[0]?.content ?? '', /crashed/);
  assert.match(dialogs[1]?.content ?? '', /unresponsive/);
  assert.equal(errors.length, 2);
  assert.match(errors[0] ?? '', /render-process-gone/);
  assert.match(errors[1] ?? '', /unresponsive/);
});

test('attachWindowDiagnostics disposer removes all owned listeners idempotently', async () => {
  const windowStub = createWindowStub();
  const errors: string[] = [];
  const dispose = attachWindowDiagnostics(windowStub.window as never, createShellContext(), {
    logError: (message: string) => errors.push(message),
  });

  dispose();
  dispose();
  windowStub.emitDidFailLoad(-102, 'ERR_CONNECTION_REFUSED', 'http://127.0.0.1:18080/bms/');
  windowStub.emitRenderProcessGone('crashed', 133);
  windowStub.emitUnresponsive();
  await Promise.resolve();

  assert.deepEqual(errors, []);
  assert.deepEqual(windowStub.loadedUrls, []);
});

test('attachWindowDiagnostics never touches a BrowserWindow destroyed before a queued failure event', async () => {
  const windowStub = createWindowStub();
  const errors: string[] = [];
  const dialogs: string[] = [];
  attachWindowDiagnostics(windowStub.window as never, createShellContext(), {
    logError: (message: string) => errors.push(message),
    showErrorBox: (_title: string, content: string) => dialogs.push(content),
  });

  windowStub.destroy();
  assert.doesNotThrow(() => windowStub.emitRenderProcessGone('crashed', 133));
  await Promise.resolve();

  assert.equal(errors.length, 1);
  assert.deepEqual(dialogs, []);
  assert.deepEqual(windowStub.loadedUrls, []);
  assert.equal(windowStub.shown, 0);
  assert.equal(windowStub.focused, 0);
});

test('attachWindowDiagnostics cleanup remains safe after BrowserWindow destruction', () => {
  const webContentsListeners = new Map<string, Set<(...args: never[]) => void>>();
  const windowListeners = new Map<string, Set<(...args: never[]) => void>>();
  const webContents = {
    on(event: string, handler: (...args: never[]) => void) {
      const handlers = webContentsListeners.get(event) ?? new Set();
      handlers.add(handler);
      webContentsListeners.set(event, handlers);
    },
    removeListener(event: string, handler: (...args: never[]) => void) {
      webContentsListeners.get(event)?.delete(handler);
    },
  };
  let destroyed = false;
  const window = {
    get webContents() {
      if (destroyed) throw new TypeError('Object has been destroyed');
      return webContents;
    },
    on(event: string, handler: (...args: never[]) => void) {
      const handlers = windowListeners.get(event) ?? new Set();
      handlers.add(handler);
      windowListeners.set(event, handlers);
    },
    removeListener(event: string, handler: (...args: never[]) => void) {
      windowListeners.get(event)?.delete(handler);
    },
    isDestroyed: () => destroyed,
    async loadURL() {},
    show() {},
    focus() {},
  };

  const dispose = attachWindowDiagnostics(window as never, createShellContext());
  destroyed = true;

  assert.doesNotThrow(dispose);
  assert.doesNotThrow(dispose);
  assert.equal(webContentsListeners.get('did-fail-load')?.size, 0);
  assert.equal(webContentsListeners.get('render-process-gone')?.size, 0);
  assert.equal(windowListeners.get('unresponsive')?.size, 0);
});
