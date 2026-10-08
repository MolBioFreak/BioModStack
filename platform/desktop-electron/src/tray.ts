import { Menu, type MenuItemConstructorOptions, Tray, nativeImage } from 'electron';

import type { ServiceControl } from './serviceControl.js';
import type { ShellContext, ShellRuntimeMode } from './windowState.js';

type TrayStatus = 'checking' | 'ready' | 'offline' | 'unavailable';

type TrayStatusPresentation = {
  tooltip: string;
  color: string;
};

const TRAY_STATUS_PRESENTATIONS: Record<TrayStatus, TrayStatusPresentation> = {
  checking: { tooltip: 'BioModStack — checking status…', color: '#6b7280' },
  ready: { tooltip: 'BioModStack — Ready', color: '#22c55e' },
  offline: { tooltip: 'BioModStack — Offline', color: '#ef4444' },
  unavailable: { tooltip: 'BioModStack — Status unavailable', color: '#eab308' },
};

export function trayStatusFromPayload(
  payload: Record<string, unknown>,
  expectedRuntimeMode: ShellRuntimeMode,
): TrayStatus {
  if (payload.runtime_mode !== expectedRuntimeMode) {
    return 'unavailable';
  }
  if (payload.runtime_active === false) {
    return 'offline';
  }
  const components = typeof payload.components === 'object' && payload.components !== null
    ? payload.components as Record<string, Record<string, unknown>>
    : null;
  const requiredComponents = components
    ? Object.values(components).filter((component) => component.required === true)
    : [];
  if (
    payload.runtime_active === true
    && payload.runtime_ready === true
    && requiredComponents.length > 0
    && requiredComponents.every((component) => component.ready === true)
  ) {
    return 'ready';
  }
  const health = payload.health;
  const healthRecord = typeof health === 'object' && health !== null
    ? health as Record<string, unknown>
    : null;
  const validHealth = healthRecord !== null
    && healthRecord.api_ready === true
    && healthRecord.frontend_ready === true
    && (expectedRuntimeMode !== 'container' || healthRecord.adapter_ready === true);
  if (
    components === null
    && payload.runtime_active === true
    && payload.runtime_ready === true
    && validHealth
  ) {
    return 'ready';
  }
  return 'unavailable';
}

export function trayStatusTooltip(
  status: TrayStatus,
  payload?: Record<string, unknown>,
): string {
  const lines = [trayStatusPresentation(status).tooltip];
  const components = payload && typeof payload.components === 'object' && payload.components !== null
    ? payload.components as Record<string, Record<string, unknown>>
    : null;
  if (!components) return lines[0];
  for (const [id, component] of Object.entries(components)) {
    const listeners = Array.isArray(component.listeners) ? component.listeners : [];
    const firstListener = listeners.find((listener) => typeof listener === 'object' && listener !== null) as Record<string, unknown> | undefined;
    const label = typeof component.label === 'string' ? component.label : id;
    const state = typeof component.state === 'string' ? component.state : 'unknown';
    const owner = typeof firstListener?.owner === 'string'
      ? firstListener.owner
      : (typeof component.ownership_status === 'string' ? component.ownership_status : 'unverified');
    lines.push(`${component.ready === true ? '✓' : '✗'} ${label}: ${state}; owner=${owner}`);
    if (component.ready !== true && typeof component.log_ref === 'string') {
      lines.push(`  log: ${component.log_ref}`);
    }
  }
  return lines.join('\n');
}

export function trayStatusPresentation(status: TrayStatus): TrayStatusPresentation {
  return TRAY_STATUS_PRESENTATIONS[status];
}

const TRAY_STATUS_REFRESH_MS = 5_000;

type TrayStatusScheduler = (callback: () => void, delayMs: number) => ReturnType<typeof setInterval>;
type TrayStatusCanceller = (timer: ReturnType<typeof setInterval>) => void;

const trayStatusRefreshStops = new WeakMap<Tray, () => void>();

export function registerAppTrayStatusRefresh(tray: Tray, stopRefresh: () => void): void {
  trayStatusRefreshStops.set(tray, stopRefresh);
}

export function disposeAppTray(tray: Tray): void {
  trayStatusRefreshStops.get(tray)?.();
  trayStatusRefreshStops.delete(tray);
}

export function startTrayStatusRefresh(
  expectedRuntimeMode: ShellRuntimeMode,
  getStatus: () => Promise<Record<string, unknown>>,
  applyStatus: (status: TrayStatus, payload?: Record<string, unknown>) => void,
  schedule: TrayStatusScheduler = setInterval,
  cancel: TrayStatusCanceller = clearInterval,
): () => void {
  let latestProbe = 0;
  let probeInFlight = false;
  let disposed = false;
  const refresh = () => {
    if (disposed) {
      return;
    }
    if (probeInFlight) {
      return;
    }
    const probe = ++latestProbe;
    probeInFlight = true;
    void getStatus()
      .then((payload) => {
        if (!disposed && probe === latestProbe) {
          applyStatus(trayStatusFromPayload(payload, expectedRuntimeMode), payload);
        }
      })
      .catch(() => {
        if (!disposed && probe === latestProbe) {
          applyStatus('unavailable');
        }
      })
      .finally(() => {
        probeInFlight = false;
      });
  };
  refresh();
  const timer = schedule(refresh, TRAY_STATUS_REFRESH_MS);
  return () => {
    disposed = true;
    latestProbe += 1;
    cancel(timer);
  };
}

type TrayDeps = {
  showWindow?: () => void;
  openInBrowser?: () => Promise<void> | void;
  copyLocalUrl?: (url: string) => void;
  openResultsFolder?: () => Promise<void> | void;
  openShellDataFolder?: () => Promise<void> | void;
  openApiLog?: () => Promise<void> | void;
  openFrontendLog?: () => Promise<void> | void;
  openCoreRuntimeLog?: () => Promise<void> | void;
  switchRuntime?: (runtimeMode: ShellRuntimeMode) => Promise<void> | void;
  quitShell?: () => void;
  reportActionError?: (error: unknown) => void;
  iconPath?: string;
};

function fireAndForget(action: () => Promise<void> | void, reportError?: (error: unknown) => void): () => void {
  return () => {
    try {
      const result = action();
      if (result && typeof (result as Promise<void>).catch === 'function') {
        void (result as Promise<void>).catch((error) => {
          reportError?.(error);
          console.error(error);
        });
      }
    } catch (error) {
      reportError?.(error);
      console.error(error);
    }
  };
}

function buildLogsSubmenu(deps: TrayDeps): MenuItemConstructorOptions[] {
  return [
    {
      label: 'Open API Log',
      click: fireAndForget(() => deps.openApiLog?.()),
    },
    {
      label: 'Open Frontend Log',
      click: fireAndForget(() => deps.openFrontendLog?.()),
    },
    {
      label: 'Open Core Runtime Log',
      click: fireAndForget(() => deps.openCoreRuntimeLog?.()),
    },
  ];
}

function buildServicesSubmenu(
  context: ShellContext,
  serviceControl: ServiceControl,
  reportActionError?: (error: unknown) => void,
): MenuItemConstructorOptions[] {
  return [
    {
      label: 'Start Services',
      click: fireAndForget(() => serviceControl.startAll(context.runtimeMode), reportActionError),
    },
    {
      label: 'Start Dev + Stable Services',
      click: fireAndForget(() => serviceControl.startRuntimeTarget('both'), reportActionError),
    },
    {
      label: 'Stop Services',
      click: fireAndForget(() => serviceControl.stopAll(context.runtimeMode), reportActionError),
    },
    {
      label: 'Restart Services',
      click: fireAndForget(() => serviceControl.restartAll(context.runtimeMode), reportActionError),
    },
    {
      label: 'Restart API',
      click: fireAndForget(() => serviceControl.restartApi(context.runtimeMode), reportActionError),
    },
  ];
}

function currentRuntimeChannelLabel(context: ShellContext): string {
  return context.runtimeMode === 'dev' ? 'Current Channel: Vite dev' : 'Current Channel: Stable /bms/';
}

function buildRuntimeChannelSubmenu(context: ShellContext, deps: TrayDeps): MenuItemConstructorOptions[] {
  return [
    {
      label: currentRuntimeChannelLabel(context),
      enabled: false,
    },
    {
      label: 'Switch to Vite Dev',
      enabled: context.runtimeMode !== 'dev',
      click: fireAndForget(() => deps.switchRuntime?.('dev')),
    },
    {
      label: 'Switch to Stable /bms/',
      enabled: context.runtimeMode !== 'container',
      click: fireAndForget(() => deps.switchRuntime?.('container')),
    },
  ];
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
    {
      label: 'Open Results Folder',
      click: fireAndForget(() => deps.openResultsFolder?.()),
    },
    {
      label: 'Open Shell Data Folder',
      click: fireAndForget(() => deps.openShellDataFolder?.()),
    },
    {
      label: 'Logs',
      submenu: buildLogsSubmenu(deps),
    },
    {
      label: 'Services',
      submenu: buildServicesSubmenu(context, serviceControl, deps.reportActionError),
    },
    {
      label: 'Runtime Channel',
      submenu: buildRuntimeChannelSubmenu(context, deps),
    },
    { type: 'separator' },
    {
      label: 'Quit Shell',
      click: () => deps.quitShell?.(),
    },
  ];
}

function createTrayStatusImage(status: TrayStatus) {
  const { color } = trayStatusPresentation(status);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
    <circle cx="32" cy="32" r="29" fill="${color}" stroke="#111827" stroke-width="3"/>
    <text x="32" y="39" text-anchor="middle" font-family="sans-serif" font-size="20" font-weight="700" fill="#ffffff">BMS</text>
  </svg>`;
  return nativeImage.createFromDataURL(`data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`);
}

function applyTrayStatus(tray: Tray, status: TrayStatus, payload?: Record<string, unknown>): void {
  tray.setImage(createTrayStatusImage(status));
  tray.setToolTip(trayStatusTooltip(status, payload));
}

export function createAppTray(
  context: ShellContext,
  serviceControl: ServiceControl,
  deps: TrayDeps = {},
): Tray {
  // Initial state is deliberately non-green until the read-only status bridge
  // confirms that the selected runtime is active.
  const tray = new Tray(createTrayStatusImage('checking'));
  applyTrayStatus(tray, 'checking');
  tray.setContextMenu(Menu.buildFromTemplate(buildTrayMenuTemplate(context, serviceControl, deps)));

  // Re-check continuously: a green tray is evidence from a current read-only
  // probe, not a cached startup result. Out-of-order probe results are ignored.
  const stopRefresh = startTrayStatusRefresh(
    context.runtimeMode,
    () => serviceControl.getStatus(context.runtimeMode),
    (status, payload) => applyTrayStatus(tray, status, payload),
  );
  registerAppTrayStatusRefresh(tray, stopRefresh);
  return tray;
}
