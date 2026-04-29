export type UiSurfaceProbe = {
  viteDev: boolean;
  electronShell: boolean;
  cordovaShell: boolean;
};

export type UiShellContext = {
  runtimeMode?: string;
  frontendOrigin?: string;
  routerBasename?: string;
  windowUrl?: string;
  browserUrl?: string;
};

export type UiDiagnosticsInput = {
  surfaceLabel: string;
  origin: string;
  href: string;
  routerBasename: string;
  viteMode: string;
  viteBaseUrl: string;
  apiHealth: string;
  shellContext?: UiShellContext | null;
  userAgent?: string;
};

export type UiDiagnosticsPayload = {
  text: string;
  fields: Record<string, string>;
};

const UNKNOWN_VALUE = 'unknown';

function printable(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return UNKNOWN_VALUE;
  }
  return String(value);
}

function addField(fields: Record<string, string>, label: string, value: unknown): void {
  fields[label] = printable(value);
}

export function resolveUiSurfaceLabel(surface: UiSurfaceProbe): string {
  if (surface.cordovaShell) {
    return 'APK/Cordova stable shell';
  }
  if (surface.electronShell) {
    return 'Electron stable shell';
  }
  if (surface.viteDev) {
    return 'Vite dev web';
  }
  return 'Stable hosted web';
}

export function buildUiDiagnosticsPayload(input: UiDiagnosticsInput): UiDiagnosticsPayload {
  const fields: Record<string, string> = {};

  addField(fields, 'Surface', input.surfaceLabel);
  addField(fields, 'Origin', input.origin);
  addField(fields, 'Location', input.href);
  addField(fields, 'Router basename', input.routerBasename);
  addField(fields, 'Vite mode', input.viteMode);
  addField(fields, 'Vite base URL', input.viteBaseUrl);
  addField(fields, 'API health', input.apiHealth);

  if (input.shellContext) {
    addField(fields, 'Shell runtime', input.shellContext.runtimeMode);
    addField(fields, 'Shell frontend origin', input.shellContext.frontendOrigin);
    addField(fields, 'Shell router basename', input.shellContext.routerBasename);
    addField(fields, 'Shell window URL', input.shellContext.windowUrl);
    addField(fields, 'Shell browser URL', input.shellContext.browserUrl);
  }

  if (input.userAgent) {
    addField(fields, 'User agent', input.userAgent);
  }

  return {
    fields,
    text: Object.entries(fields).map(([label, value]) => `${label}: ${value}`).join('\n'),
  };
}
