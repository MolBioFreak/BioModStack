import type { IpcMainInvokeEvent } from 'electron';

import type { ShellContext } from './windowState.js';

function normalizeRouterBasename(basename: string): string {
  const trimmed = basename.trim();
  if (!trimmed || trimmed === '/') {
    return '/';
  }
  const withLeadingSlash = trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
  const withoutTrailingSlash = withLeadingSlash.replace(/\/+$/, '');
  return withoutTrailingSlash ? `${withoutTrailingSlash}/` : '/';
}

function parseUrl(url: string): URL | null {
  try {
    return new URL(url);
  } catch {
    return null;
  }
}

function normalizeOrigin(origin: string): string | null {
  const parsed = parseUrl(origin);
  if (parsed) {
    return parsed.origin;
  }
  return origin.trim().replace(/\/+$/, '') || null;
}

function pathIsInsideRouterBasename(pathname: string, basename: string): boolean {
  if (basename === '/') {
    return pathname.startsWith('/');
  }
  const prefix = basename.slice(0, -1);
  return pathname === prefix || pathname.startsWith(basename);
}

export function isAllowedShellNavigationUrl(url: string, context: ShellContext): boolean {
  const parsed = parseUrl(url);
  const expectedOrigin = normalizeOrigin(context.frontendOrigin);
  if (!parsed || !expectedOrigin || parsed.origin !== expectedOrigin) {
    return false;
  }
  return pathIsInsideRouterBasename(parsed.pathname, normalizeRouterBasename(context.routerBasename));
}

export function isAllowedExternalUrl(url: string): boolean {
  const parsed = parseUrl(url);
  if (!parsed) {
    return false;
  }
  return parsed.protocol === 'https:' || parsed.protocol === 'http:';
}

export function assertTrustedIpcSender(event: IpcMainInvokeEvent, context: ShellContext): void {
  const senderUrl = event.senderFrame?.url || event.sender.getURL();
  if (!isAllowedShellNavigationUrl(senderUrl, context)) {
    throw new Error(`Untrusted BioModStack shell IPC sender: ${senderUrl || 'unknown'}`);
  }
}
