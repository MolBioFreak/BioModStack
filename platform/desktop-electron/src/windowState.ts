import type { BrowserWindowConstructorOptions } from 'electron';

export type ShellRuntimeMode = 'dev' | 'container';

export type ShellContext = {
  runtimeMode: ShellRuntimeMode;
  frontendOrigin: string;
  routerBasename: string;
  windowUrl: string;
  browserUrl: string;
};

export type BiomodstackDesktopApi = {
  getShellContext: () => Promise<ShellContext>;
  openInBrowser: () => Promise<void>;
};

export const GET_SHELL_CONTEXT_CHANNEL = 'biomodstack:get-shell-context';
export const OPEN_IN_BROWSER_CHANNEL = 'biomodstack:open-in-browser';
export const EXPOSED_BIOMODSTACK_API_KEYS = ['getShellContext', 'openInBrowser'] as const;

function normalizeOrigin(origin: string): string {
  return origin.trim().replace(/\/+$/, '');
}

function normalizeRouterBasename(basename: string): string {
  const trimmed = basename.trim();
  if (!trimmed || trimmed === '/') {
    return '/';
  }

  const withLeadingSlash = trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
  const withoutTrailingSlash = withLeadingSlash.replace(/\/+$/, '');
  return withoutTrailingSlash ? `${withoutTrailingSlash}/` : '/';
}

function buildHostedUrl(frontendOrigin: string, routerBasename: string): string {
  if (routerBasename === '/') {
    return `${frontendOrigin}/`;
  }
  return `${frontendOrigin}${routerBasename}`;
}

export function resolveShellContext(options: Partial<ShellContext> = {}): ShellContext {
  const runtimeMode = options.runtimeMode ?? (process.env.BMS_RUNTIME_MODE === 'dev' ? 'dev' : 'container');
  const frontendOrigin = normalizeOrigin(
    options.frontendOrigin ?? process.env.BMS_FRONTEND_ORIGIN ?? 'http://127.0.0.1:5173',
  );
  const routerBasename = normalizeRouterBasename(
    options.routerBasename
      ?? process.env.BMS_ROUTER_BASENAME
      ?? (runtimeMode === 'dev' ? '/' : '/bms/'),
  );
  const hostedUrl = buildHostedUrl(frontendOrigin, routerBasename);

  return {
    runtimeMode,
    frontendOrigin,
    routerBasename,
    windowUrl: options.windowUrl ?? hostedUrl,
    browserUrl: options.browserUrl ?? hostedUrl,
  };
}

export function buildBrowserWindowOptions(preloadPath: string): BrowserWindowConstructorOptions {
  return {
    title: 'BioModStack',
    width: 1600,
    height: 1024,
    minWidth: 1200,
    minHeight: 800,
    show: false,
    autoHideMenuBar: false,
    backgroundColor: '#0b1020',
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  };
}
