import assert from 'node:assert/strict';
import test from 'node:test';

import {
  attachWindowDiagnostics,
  buildShellFailureDataUrl,
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
    frontendOrigin: 'http://127.0.0.1:5173',
    routerBasename: '/bms/',
    windowUrl: 'http://127.0.0.1:5173/bms/',
    browserUrl: 'http://127.0.0.1:5173/bms/',
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
      },
      on: (event: string, handler: VoidHandler) => {
        if (event === 'unresponsive') {
          unresponsiveHandler = handler;
        }
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
    windowUrl: 'http://127.0.0.1:5173/bms/',
    browserUrl: 'http://127.0.0.1:5173/bms/',
    validatedURL: 'http://127.0.0.1:5173/bms/',
    errorCode: -102,
    errorDescription: 'ERR_CONNECTION_REFUSED',
  });

  assert.match(dataUrl, /^data:text\/html;charset=UTF-8,/);

  const html = decodeDataUrl(dataUrl);
  assert.match(html, /BioModStack Shell could not load the UI/);
  assert.match(html, /did-fail-load/);
  assert.match(html, /ERR_CONNECTION_REFUSED/);
  assert.match(html, /-102/);
  assert.match(html, /http:\/\/127\.0\.0\.1:5173\/bms\//);
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

  windowStub.emitDidFailLoad(-102, 'ERR_CONNECTION_REFUSED', 'http://127.0.0.1:5173/bms/');
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
