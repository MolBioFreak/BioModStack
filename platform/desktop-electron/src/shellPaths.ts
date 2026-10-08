import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const SHELL_STORAGE_PARTITION = 'persist:biomodstack-shell';

const DATA_ROOT_MARKERS = [
  'biomodstack.db',
  'bms_results',
  'work',
  'analysis_cache',
] as const;

export type ShellPathOptions = {
  env?: NodeJS.ProcessEnv;
  homeDir?: string;
  projectRoot?: string;
  pathExists?: (target: string) => boolean;
  readText?: (target: string) => string;
};

export type ShellPaths = {
  projectRoot: string;
  dataRoot: string;
  resultsDir: string;
  configDir: string;
  stateDir: string;
  logsDir: string;
  apiLog: string;
  frontendLog: string;
  coreRuntimeLog: string;
  trayIconPath: string;
  appIconPath: string;
};

type InstallProfile = {
  data_root?: string;
};

function expandUser(value: string, homeDir: string): string {
  if (value === '~') {
    return homeDir;
  }
  if (value.startsWith('~/')) {
    return path.join(homeDir, value.slice(2));
  }
  return value;
}

function resolveUserPath(value: string, homeDir: string): string {
  return path.resolve(expandUser(value, homeDir));
}

function resolveConfigDir(options: ShellPathOptions = {}): string {
  const homeDir = options.homeDir ?? os.homedir();
  const env = options.env ?? process.env;
  if (env.XDG_CONFIG_HOME?.trim()) {
    return path.join(resolveUserPath(env.XDG_CONFIG_HOME, homeDir), 'biomodstack');
  }
  return path.join(homeDir, '.config', 'biomodstack');
}

function loadInstallProfile(options: ShellPathOptions = {}): InstallProfile {
  const pathExists = options.pathExists ?? fs.existsSync;
  const readText = options.readText ?? ((target: string) => fs.readFileSync(target, 'utf8'));
  const installProfilePath = path.join(resolveConfigDir(options), 'install_profile.json');
  if (!pathExists(installProfilePath)) {
    return {};
  }
  try {
    const parsed = JSON.parse(readText(installProfilePath));
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {};
    }
    return parsed as InstallProfile;
  } catch {
    return {};
  }
}

export function resolveProjectRoot(options: ShellPathOptions = {}): string {
  const homeDir = options.homeDir ?? os.homedir();
  const env = options.env ?? process.env;

  if (env.BMS_HOME?.trim()) {
    return resolveUserPath(env.BMS_HOME, homeDir);
  }
  if (options.projectRoot?.trim()) {
    return resolveUserPath(options.projectRoot, homeDir);
  }
  return path.resolve(__dirname, '..', '..', '..', '..');
}

function resolveStateHome(options: ShellPathOptions): string {
  const homeDir = options.homeDir ?? os.homedir();
  const env = options.env ?? process.env;
  if (env.XDG_STATE_HOME?.trim()) {
    return resolveUserPath(env.XDG_STATE_HOME, homeDir);
  }
  return path.join(homeDir, '.local', 'state');
}

function candidateDataRoots(homeDir: string): string[] {
  return [
    path.resolve('/mnt/BioModStack'),
    path.join(homeDir, '.biomodstack'),
  ];
}

function looksLikeDataRoot(candidate: string, pathExists: (target: string) => boolean): boolean {
  return DATA_ROOT_MARKERS.some((marker) => pathExists(path.join(candidate, marker)));
}

export function resolveDataRoot(options: ShellPathOptions = {}): string {
  const homeDir = options.homeDir ?? os.homedir();
  const env = options.env ?? process.env;

  if (env.BMS_DATA?.trim()) {
    return resolveUserPath(env.BMS_DATA, homeDir);
  }

  const installProfile = loadInstallProfile(options);
  if (installProfile.data_root?.trim()) {
    return resolveUserPath(installProfile.data_root, homeDir);
  }

  const pathExists = options.pathExists ?? (() => false);
  for (const candidate of candidateDataRoots(homeDir)) {
    if (looksLikeDataRoot(candidate, pathExists)) {
      return candidate;
    }
  }

  return resolveProjectRoot(options);
}

export function resolveShellPaths(options: ShellPathOptions = {}): ShellPaths {
  const projectRoot = resolveProjectRoot(options);
  const dataRoot = resolveDataRoot({
    ...options,
    projectRoot,
  });
  const stateDir = path.join(resolveStateHome(options), 'biomodstack');
  const logsDir = path.join(stateDir, 'logs');
  const configDir = resolveConfigDir(options);

  return {
    projectRoot,
    dataRoot,
    resultsDir: path.join(dataRoot, 'bms_results'),
    configDir,
    stateDir,
    logsDir,
    apiLog: path.join(logsDir, 'api.log'),
    frontendLog: path.join(logsDir, 'frontend.log'),
    coreRuntimeLog: path.join(logsDir, 'core-runtime.log'),
    trayIconPath: path.join(projectRoot, 'platform', 'assets', 'icons', 'biomodstack_tray.png'),
    appIconPath: path.join(projectRoot, 'platform', 'assets', 'icons', 'biomodstack_256.png'),
  };
}
