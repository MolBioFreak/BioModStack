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
  frontendBuildRevision?: string;
  apiBuildRevision?: string;
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

function knownRevision(value: unknown): string | null {
  const normalized = printable(value).trim();
  if (!normalized || normalized.toLowerCase() === UNKNOWN_VALUE) {
    return null;
  }
  return normalized;
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

  const frontendRevision = knownRevision(input.frontendBuildRevision);
  const apiRevision = knownRevision(input.apiBuildRevision);
  addField(fields, 'Frontend build revision', frontendRevision);
  addField(fields, 'API build revision', apiRevision);
  addField(
    fields,
    'Revision skew',
    frontendRevision && apiRevision
      ? (frontendRevision === apiRevision ? 'none detected' : 'detected')
      : 'indeterminate',
  );

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
