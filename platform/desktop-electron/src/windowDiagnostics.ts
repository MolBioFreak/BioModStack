import fs from 'node:fs';
import path from 'node:path';

import type { BrowserWindow, WebContents } from 'electron';

import type { ElectronBuildIdentity } from './buildIdentity.js';
import type { ShellContext } from './windowState.js';

export type ShellFailureKind = 'did-fail-load' | 'render-process-gone' | 'unresponsive';

export type ShellFailureDetails = {
  kind: ShellFailureKind;
  windowUrl: string;
  browserUrl: string;
  validatedURL?: string;
  errorCode?: number;
  errorDescription?: string;
  reason?: string;
  exitCode?: number;
};

export type DiagnosticsWindow = Pick<BrowserWindow, 'on' | 'removeListener' | 'loadURL' | 'show' | 'focus' | 'isDestroyed'> & {
  webContents: Pick<WebContents, 'on' | 'removeListener'>;
};

export type PersistedShellFailure = {
  timestamp: string;
  kind: ShellFailureKind;
  runtimeMode: ShellContext['runtimeMode'];
  windowUrl: string;
  browserUrl: string;
  validatedURL: string | null;
  reason: string | null;
  exitCode: number | null;
  errorCode: number | null;
  errorDescription: string | null;
  memory: Record<string, unknown>;
  build?: ElectronBuildIdentity;
};

export type PersistentDiagnosticsStore = {
  append: (details: ShellFailureDetails, context: ShellContext) => void;
};

export type PersistentDiagnosticsStoreOptions = {
  path: string;
  maxRecords?: number;
  now?: () => string;
  readFile?: (path: string) => string;
  writeFile?: (path: string, contents: string) => void;
  getMemoryContext?: () => Record<string, unknown>;
  buildIdentity?: ElectronBuildIdentity;
};

export type WindowDiagnosticsHooks = {
  logError?: (message: string) => void;
  showErrorBox?: (title: string, content: string) => void;
  diagnosticsStore?: PersistentDiagnosticsStore;
};

function boundedValue(value: unknown, maximumLength = 1024): unknown {
  if (typeof value === 'string') {
    return value.slice(0, maximumLength);
  }
  if (typeof value === 'number' || typeof value === 'boolean' || value === null) {
    return value;
  }
  if (Array.isArray(value)) {
    return value.slice(0, 20).map((item) => boundedValue(item, maximumLength));
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).slice(0, 20).map(([key, item]) => [key.slice(0, 128), boundedValue(item, maximumLength)]));
  }
  return String(value).slice(0, maximumLength);
}

function parsePersistedFailures(contents: string): PersistedShellFailure[] {
  try {
    const parsed = JSON.parse(contents);
    return Array.isArray(parsed) ? parsed.filter((item): item is PersistedShellFailure => Boolean(item && typeof item === 'object')) : [];
  } catch {
    return [];
  }
}

export function createPersistentDiagnosticsStore(options: PersistentDiagnosticsStoreOptions): PersistentDiagnosticsStore {
  const maxRecords = Math.max(1, Math.min(options.maxRecords ?? 100, 500));
  const readFile = options.readFile ?? ((diagnosticsPath: string) => fs.readFileSync(diagnosticsPath, 'utf8'));
  const writeFile = options.writeFile ?? ((diagnosticsPath: string, contents: string) => {
    fs.mkdirSync(path.dirname(diagnosticsPath), { recursive: true });
    const temporaryPath = `${diagnosticsPath}.${process.pid}.tmp`;
    fs.writeFileSync(temporaryPath, contents, 'utf8');
    fs.renameSync(temporaryPath, diagnosticsPath);
  });
  const getMemoryContext = options.getMemoryContext ?? (() => process.memoryUsage() as unknown as Record<string, unknown>);
  let records: PersistedShellFailure[] | undefined;

  return {
    append(details, context) {
      records ??= (() => {
        try {
          return parsePersistedFailures(readFile(options.path));
        } catch {
          return [];
        }
      })();
      const record: PersistedShellFailure = {
        timestamp: (options.now ?? (() => new Date().toISOString()))(),
        kind: details.kind,
        runtimeMode: context.runtimeMode,
        windowUrl: details.windowUrl,
        browserUrl: details.browserUrl,
        validatedURL: details.validatedURL ?? null,
        reason: details.reason ?? null,
        exitCode: details.exitCode ?? null,
        errorCode: details.errorCode ?? null,
        errorDescription: details.errorDescription ?? null,
        memory: boundedValue(getMemoryContext()) as Record<string, unknown>,
        ...(options.buildIdentity ? { build: options.buildIdentity } : {}),
      };
      records = [...records, record].slice(-maxRecords);
      writeFile(options.path, JSON.stringify(records));
    },
  };
}

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

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function summarizeFailure(details: ShellFailureDetails): string {
  switch (details.kind) {
    case 'did-fail-load':
      return 'The embedded BioModStack UI did not load successfully.';
    case 'render-process-gone':
      return 'The embedded BioModStack renderer exited unexpectedly.';
    case 'unresponsive':
      return 'The embedded BioModStack renderer stopped responding.';
  }
}

function buildDetailRows(details: ShellFailureDetails): Array<[label: string, value: string]> {
  const rows: Array<[label: string, value: string]> = [
    ['Shell URL', details.windowUrl],
    ['Browser URL', details.browserUrl],
  ];

  if (details.validatedURL && details.validatedURL !== details.windowUrl) {
    rows.push(['Failed URL', details.validatedURL]);
  }
  if (typeof details.errorCode === 'number') {
    rows.push(['Error code', String(details.errorCode)]);
  }
  if (details.errorDescription) {
    rows.push(['Error', details.errorDescription]);
  }
  if (details.reason) {
    rows.push(['Renderer status', details.reason]);
  }
  if (typeof details.exitCode === 'number') {
    rows.push(['Exit code', String(details.exitCode)]);
  }

  return rows;
}

export function formatShellFailureLog(details: ShellFailureDetails): string {
  const fields = buildDetailRows(details).map(([label, value]) => `${label.toLowerCase().replaceAll(' ', '_')}=${JSON.stringify(value)}`);
  return `[BioModStack Shell] ${details.kind}: ${summarizeFailure(details)} ${fields.join(' ')}`.trim();
}

export function buildShellFailureDialog(details: ShellFailureDetails): { title: string; content: string } {
  const lines = [
    summarizeFailure(details),
    '',
    `Failure type: ${details.kind}`,
    ...buildDetailRows(details).map(([label, value]) => `${label}: ${value}`),
    '',
    'Use the Shell menu or tray to open BioModStack in your browser or inspect the service logs.',
  ];

  return {
    title: 'BioModStack Shell diagnostics',
    content: lines.join('\n'),
  };
}

export function buildShellFailureDataUrl(details: ShellFailureDetails): string {
  const rows = buildDetailRows(details)
    .map(
      ([label, value]) =>
        `<tr><th>${escapeHtml(label)}</th><td><code>${escapeHtml(value)}</code></td></tr>`,
    )
    .join('');

  const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>BioModStack Shell diagnostics</title>
    <style>
      :root {
        color-scheme: dark;
        font-family: Inter, system-ui, sans-serif;
      }
      body {
        margin: 0;
        min-height: 100vh;
        background: #0b1020;
        color: #e5e7eb;
      }
      main {
        max-width: 860px;
        margin: 0 auto;
        padding: 48px 24px 64px;
      }
      h1 {
        margin: 0 0 12px;
        font-size: 2rem;
      }
      p {
        line-height: 1.6;
      }
      .card {
        margin-top: 24px;
        padding: 20px;
        border: 1px solid rgba(148, 163, 184, 0.25);
        border-radius: 16px;
        background: rgba(15, 23, 42, 0.9);
      }
      table {
        width: 100%;
        border-collapse: collapse;
      }
      th,
      td {
        padding: 10px 0;
        border-bottom: 1px solid rgba(148, 163, 184, 0.18);
        text-align: left;
        vertical-align: top;
      }
      th {
        width: 180px;
        color: #93c5fd;
        font-weight: 600;
      }
      code {
        word-break: break-word;
        font-family: ui-monospace, SFMono-Regular, monospace;
      }
      ul {
        line-height: 1.7;
        padding-left: 20px;
      }
      .badge {
        display: inline-block;
        margin-top: 8px;
        padding: 6px 10px;
        border-radius: 999px;
        background: rgba(239, 68, 68, 0.16);
        color: #fca5a5;
        font-weight: 700;
        letter-spacing: 0.02em;
      }
    </style>
  </head>
  <body>
    <main>
      <div class="badge">${escapeHtml(details.kind)}</div>
      <h1>BioModStack Shell could not load the UI</h1>
      <p>${escapeHtml(summarizeFailure(details))}</p>
      <div class="card">
        <table>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div class="card">
        <h2>What to try next</h2>
        <ul>
          <li>Open the same BioModStack UI in your browser at <code>${escapeHtml(details.browserUrl)}</code>.</li>
          <li>Use the Shell menu or tray to inspect the API/frontend/core-runtime logs.</li>
          <li>Restart the BioModStack services if the local web UI is unavailable.</li>
        </ul>
      </div>
    </main>
  </body>
</html>`;

  return `data:text/html;charset=UTF-8,${encodeURIComponent(html)}`;
}

export function attachWindowDiagnostics(
  window: DiagnosticsWindow,
  contextSource: ShellContext | (() => ShellContext),
  hooks: WindowDiagnosticsHooks = {},
): () => void {
  // BrowserWindow.webContents throws "Object has been destroyed" when read from
  // a `closed` listener. Retain the EventEmitter reference while the window is
  // alive so teardown never re-enters Electron's destroyed-object getter.
  const webContents = window.webContents;
  const getContext = typeof contextSource === 'function' ? contextSource : () => contextSource;
  const logError = hooks.logError ?? ((message: string) => console.error(message));
  const showErrorBox = hooks.showErrorBox ?? (() => undefined);
  let loadingDiagnosticsPage = false;
  let disposed = false;

  const isWindowAlive = () => {
    try {
      return !window.isDestroyed();
    } catch {
      return false;
    }
  };

  const reportFailure = (details: ShellFailureDetails, options: { loadFallbackPage: boolean }) => {
    if (disposed) return;
    const message = formatShellFailureLog(details);
    const dialog = buildShellFailureDialog(details);
    try {
      hooks.diagnosticsStore?.append(details, getContext());
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      logError(`[BioModStack Shell] failed to persist diagnostics: ${reason}`);
    }
    logError(message);

    // Renderer failure events can be queued immediately before the native
    // BrowserWindow closes. Preserve the diagnostic record above, but never
    // touch the native wrapper after Electron has destroyed it.
    if (!isWindowAlive()) return;
    showErrorBox(dialog.title, dialog.content);

    if (!options.loadFallbackPage || loadingDiagnosticsPage) {
      return;
    }

    loadingDiagnosticsPage = true;
    try {
      window.show();
      window.focus();
      void Promise.resolve(window.loadURL(buildShellFailureDataUrl(details)))
        .catch((error: unknown) => {
          const reason = error instanceof Error ? error.message : String(error);
          logError(`[BioModStack Shell] failed to load diagnostic page: ${reason}`);
        })
        .finally(() => {
          loadingDiagnosticsPage = false;
        });
    } catch (error) {
      loadingDiagnosticsPage = false;
      const reason = error instanceof Error ? error.message : String(error);
      logError(`[BioModStack Shell] failed to show diagnostic page: ${reason}`);
    }
  };

  const onDidFailLoad = (
    _event: unknown,
    errorCode: number,
    errorDescription: string,
    validatedURL: string,
    isMainFrame: boolean,
  ) => {
    if (!isMainFrame || validatedURL.startsWith('data:text/html')) return;
    const context = getContext();
    reportFailure({
      kind: 'did-fail-load',
      windowUrl: context.windowUrl,
      browserUrl: context.browserUrl,
      validatedURL,
      errorCode,
      errorDescription,
    }, { loadFallbackPage: true });
  };

  const onRenderProcessGone = (_event: unknown, details: { reason: string; exitCode: number }) => {
    const context = getContext();
    reportFailure({
      kind: 'render-process-gone',
      windowUrl: context.windowUrl,
      browserUrl: context.browserUrl,
      reason: details.reason,
      exitCode: details.exitCode,
    }, { loadFallbackPage: true });
  };

  const onUnresponsive = () => {
    const context = getContext();
    reportFailure({
      kind: 'unresponsive',
      windowUrl: context.windowUrl,
      browserUrl: context.browserUrl,
    }, { loadFallbackPage: false });
  };

  webContents.on('did-fail-load', onDidFailLoad);
  webContents.on('render-process-gone', onRenderProcessGone);
  window.on('unresponsive', onUnresponsive);

  return () => {
    if (disposed) return;
    disposed = true;
    try {
      webContents.removeListener('did-fail-load', onDidFailLoad);
      webContents.removeListener('render-process-gone', onRenderProcessGone);
      window.removeListener('unresponsive', onUnresponsive);
    } catch {
      // Electron owns destruction of these EventEmitters. Shutdown must remain
      // idempotent even if native teardown wins the race with listener cleanup.
    }
  };
}
