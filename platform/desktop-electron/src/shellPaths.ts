import fs from 'node:fs';
import { createHash } from 'node:crypto';
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

// Match pathlib.resolve(strict=False): canonicalize existing ancestors while
// retaining a missing suffix. Do not resolve publication symlinks themselves.
function canonicalPath(value: string, depth = 0): string {
  if (depth > 40) throw new Error('Configuration path has too many symbolic links');
  // Do not normalize '..' before following symlinks: pathlib resolves the
  // components in order, so /alias/../config need not mean /config.
  const absolute = path.isAbsolute(value) ? value : `${process.cwd()}${path.sep}${value}`;
  const root = path.parse(absolute).root;
  let current = root;
  for (const part of absolute.slice(root.length).split(path.sep)) {
    if (!part || part === '.') continue;
    if (part === '..') { current = path.dirname(current); continue; }
    const candidate = path.join(current, part);
    try {
      if (fs.lstatSync(candidate).isSymbolicLink()) {
        const target = fs.readlinkSync(candidate);
        current = canonicalPath(path.isAbsolute(target) ? target : `${current}${path.sep}${target}`, depth + 1);
      } else current = candidate;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      current = candidate;
    }
  }
  return current;
}

function homePath(options: ShellPathOptions): string {
  return canonicalPath(options.homeDir ?? (options.env ?? process.env).HOME ?? os.homedir());
}

function resolveUserPath(value: string, homeDir: string): string {
  return canonicalPath(expandUser(value, homeDir));
}

function resolveConfigDir(options: ShellPathOptions = {}): string {
  const homeDir = homePath(options);
  const env = options.env ?? process.env;
  if (env.XDG_CONFIG_HOME?.trim()) {
    return path.join(resolveUserPath(env.XDG_CONFIG_HOME, homeDir), 'biomodstack');
  }
  return path.join(canonicalPath(path.join(homeDir, '.config')), 'biomodstack');
}

function loadInstallProfile(options: ShellPathOptions = {}): InstallProfile {
  const pathExists = options.pathExists ?? fs.existsSync;
  const readText = options.readText ?? ((target: string) => fs.readFileSync(target, 'utf8'));
  const transaction = path.join(resolveConfigDir(options), 'configuration-v1');
  const managed = pathExists(transaction);
  if (managed && !pathExists(path.join(transaction, 'active'))) throw new Error('Configuration incomplete; run recover');
  const activeIdentity = managed ? fs.readlinkSync(path.join(transaction, 'active')) : null;
  try {
    if (managed) {
      const journal = JSON.parse(readText(path.join(transaction, 'journal.json')));
      if (journal.schema_version !== 'bms.configuration-journal.v1' ||
          !pathExists(path.join(transaction, 'active'))) throw new Error('Configuration incomplete; run recover');
      const destinations = {
        profile: path.join(resolveConfigDir(options), 'install_profile.json'),
        core_runtime_env: path.join(resolveConfigDir(options), 'core-runtime.env'),
        compat_env: path.join(homePath(options), '.biomodstack', 'env.sh'),
      };
      if (fs.lstatSync(path.join(transaction, 'generation')).isSymbolicLink()) throw new Error('Invalid original generation');
      let hashes = journal.hashes;
      if (activeIdentity !== 'generation') {
        if (!/^release-[0-9a-f]{64}$/.test(activeIdentity ?? '')) throw new Error('Invalid configuration target');
        const directory = path.join(transaction, activeIdentity!);
        if (fs.lstatSync(directory).isSymbolicLink()) throw new Error('Invalid generation link');
        const manifestPath = path.join(directory, 'manifest.json');
        if (!fs.lstatSync(manifestPath).isFile() || fs.lstatSync(manifestPath).isSymbolicLink()) throw new Error('Invalid manifest');
        const manifest = JSON.parse(readText(manifestPath));
        const receipt = manifest.receipt;
        const keys = ['BMS_BUILD_ID', 'BMS_BUILD_SHA', 'BMS_BUILD_TIME', 'BMS_MANAGED_API_IMAGE_ID', 'BMS_MANAGED_WEB_IMAGE_ID'];
        if (manifest.schema_version !== 'bms.configuration-release.v1' ||
            manifest.operation_id !== journal.operation_id || manifest.generation_id !== activeIdentity ||
            'release-' + createHash('sha256').update(manifest.release_id).digest('hex') !== activeIdentity ||
            JSON.stringify(Object.keys(receipt).sort()) !== JSON.stringify(keys) ||
            !keys.every(k => typeof receipt[k] === 'string' && /^[A-Za-z0-9_.:+/@-]+$/.test(receipt[k])) ||
            !/^[0-9a-f]{40}$/.test(receipt.BMS_BUILD_SHA) ||
            !['BMS_MANAGED_API_IMAGE_ID', 'BMS_MANAGED_WEB_IMAGE_ID'].every(k => /^sha256:[0-9a-f]{64}$/.test(receipt[k]))) {
          throw new Error('Invalid release manifest');
        }
        for (const key of Object.keys(destinations)) {
          if (manifest.base_hashes[key] !== journal.hashes[key]) throw new Error('Invalid release base');
          const expected = key === 'core_runtime_env'
            ? journal.files[key].replace(/\n+$/, '') + '\n' + keys.map(k => `${k}=${receipt[k]}\n`).join('')
            : journal.files[key];
          if (createHash('sha256').update(expected).digest('hex') !== manifest.hashes[key]) throw new Error('Invalid release export');
        }
        hashes = manifest.hashes;
      }
      for (const [key, destination] of Object.entries(destinations)) {
        if (fs.readlinkSync(destination) !== path.join(transaction, 'active', key)) throw new Error('Invalid publication link');
        const staged = path.join(transaction, activeIdentity!, key);
        if (!fs.lstatSync(staged).isFile() || fs.lstatSync(staged).isSymbolicLink()) throw new Error('Invalid generation file');
        if (journal.context.destinations[key] !== destination) throw new Error('Configuration context changed');
        const content = readText(destination as string);
        if (createHash('sha256').update(content).digest('hex') !== hashes[key]) {
          throw new Error('Configuration generation mismatch; run recover');
        }
      }
    }
    const installProfilePath = path.join(resolveConfigDir(options), 'install_profile.json');
    let profile: InstallProfile = {};
    try {
      if (pathExists(installProfilePath)) {
        const parsed = JSON.parse(readText(installProfilePath));
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) profile = parsed as InstallProfile;
      }
    } catch (error) {
      if (managed) throw error;
    } finally {
      // Every exit, including missing/invalid JSON and shape errors, must reject
      // a writer that became visible during this read. Never select legacy state.
      if (managed !== pathExists(transaction)) throw new Error('Configuration changed; retry');
    }
    return profile;
  } finally {
    if (managed !== pathExists(transaction) || (managed && fs.readlinkSync(path.join(transaction, 'active')) !== activeIdentity)) throw new Error('Configuration changed; retry');
  }
}

export function resolveProjectRoot(options: ShellPathOptions = {}): string {
  const homeDir = homePath(options);
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
  const homeDir = homePath(options);
  const env = options.env ?? process.env;
  if (env.XDG_STATE_HOME?.trim()) {
    return resolveUserPath(env.XDG_STATE_HOME, homeDir);
  }
  return canonicalPath(path.join(homeDir, '.local', 'state'));
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
  const pathExists = options.pathExists ?? fs.existsSync;
  const transaction = path.join(resolveConfigDir(options), 'configuration-v1');
  const managed = pathExists(transaction);
  try {
    return resolveDataRootSnapshot(options);
  } finally {
    if (managed !== pathExists(transaction)) throw new Error('Configuration changed; retry');
  }
}

function resolveDataRootSnapshot(options: ShellPathOptions): string {
  const homeDir = homePath(options);
  const env = options.env ?? process.env;

  const installProfile = loadInstallProfile(options);
  if (env.BMS_DATA?.trim()) {
    return resolveUserPath(env.BMS_DATA, homeDir);
  }
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
