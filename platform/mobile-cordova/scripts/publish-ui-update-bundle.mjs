#!/usr/bin/env node
import { promises as fs } from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

import {
  buildBundleDescriptor,
  exists,
  normalizeConfig,
  readJson,
  resolveFrontendDir,
  run,
} from './prepare-bms-assets.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, '..');

function parseArgs(argv) {
  const out = {
    config: path.join(projectRoot, 'cordova.runtime.json'),
    channel: '',
    version: '',
    updatesDir: '',
  };

  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === '--config') {
      out.config = path.resolve(argv[index + 1]);
      index += 1;
      continue;
    }
    if (token === '--channel') {
      out.channel = String(argv[index + 1] || '').trim();
      index += 1;
      continue;
    }
    if (token === '--version') {
      out.version = String(argv[index + 1] || '').trim();
      index += 1;
      continue;
    }
    if (token === '--updates-dir') {
      out.updatesDir = path.resolve(argv[index + 1]);
      index += 1;
      continue;
    }
    throw new Error(`Unknown argument: ${token}`);
  }

  return out;
}

function runCapture(command, args, options = {}) {
  const result = spawnSync(command, args, {
    encoding: 'utf8',
    ...options,
  });
  if (result.status !== 0) {
    const stderr = String(result.stderr || '').trim();
    throw new Error(`${command} ${args.join(' ')} failed with exit code ${result.status ?? 'unknown'}${stderr ? `: ${stderr}` : ''}`);
  }
  return String(result.stdout || '').trim();
}

function buildDefaultVersion() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, '0');
  return [
    now.getUTCFullYear(),
    pad(now.getUTCMonth() + 1),
    pad(now.getUTCDate()),
  ].join('.') + '-' + [pad(now.getUTCHours()), pad(now.getUTCMinutes()), pad(now.getUTCSeconds())].join('');
}

async function collectRelativeFiles(rootDir, currentDir = rootDir) {
  const entries = await fs.readdir(currentDir, { withFileTypes: true });
  const relativeFiles = [];

  for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    const absolutePath = path.join(currentDir, entry.name);
    if (entry.isDirectory()) {
      relativeFiles.push(...await collectRelativeFiles(rootDir, absolutePath));
      continue;
    }
    if (!entry.isFile()) {
      continue;
    }
    relativeFiles.push(path.relative(rootDir, absolutePath).split(path.sep).join('/'));
  }

  return relativeFiles;
}

function encodePathForUrl(relativePath) {
  return relativePath
    .split('/')
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join('/');
}

async function inferApiDir(frontendCheckout, frontendDir) {
  const candidates = [
    path.resolve(frontendCheckout, 'platform', 'api'),
    path.resolve(frontendDir, '..', 'api'),
  ];

  for (const candidate of candidates) {
    if (await exists(path.join(candidate, 'paths.py'))) {
      return candidate;
    }
  }

  throw new Error(`Could not infer BioModStack API directory from ${frontendCheckout}`);
}

async function resolveUpdatesDir(explicitUpdatesDir, apiDir) {
  if (explicitUpdatesDir) {
    return explicitUpdatesDir;
  }

  return runCapture('python3', [
    '-c',
    [
      'import sys',
      `sys.path.insert(0, ${JSON.stringify(apiDir)})`,
      'from paths import get_mobile_ui_updates_dir',
      'print(get_mobile_ui_updates_dir())',
    ].join('; '),
  ]);
}

function normalizeBaseUrl(value) {
  return String(value || '').trim().replace(/\/+$/, '');
}

async function zipDirectory(sourceDir, zipPath) {
  run('python3', [
    '-c',
    [
      'import pathlib',
      'import sys',
      'import zipfile',
      'zip_path = pathlib.Path(sys.argv[1])',
      'source_dir = pathlib.Path(sys.argv[2])',
      'zip_path.parent.mkdir(parents=True, exist_ok=True)',
      'with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:',
      '    for candidate in sorted(source_dir.rglob("*")):',
      '        if candidate.is_file():',
      '            archive.write(candidate, candidate.relative_to(source_dir).as_posix())',
    ].join('\n'),
    zipPath,
    sourceDir,
  ]);
}

async function sha256File(filePath) {
  const buffer = await fs.readFile(filePath);
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  const runtimeConfig = normalizeConfig(await readJson(args.config));
  const frontendDir = await resolveFrontendDir(runtimeConfig.frontendCheckout);
  const apiDir = await inferApiDir(runtimeConfig.frontendCheckout, frontendDir);
  const updatesDir = await resolveUpdatesDir(args.updatesDir, apiDir);
  const channel = args.channel || runtimeConfig.uiUpdateChannel || 'phone';
  const version = args.version || buildDefaultVersion();
  const apiBaseUrl = normalizeBaseUrl(runtimeConfig.apiBaseUrl);

  if (!apiBaseUrl) {
    throw new Error('Runtime config must define apiBaseUrl before publishing a UI update.');
  }

  const outDir = path.join(projectRoot, '.cache', 'bms-ui-update-dist', channel, version);
  const filesOutputDir = path.join(updatesDir, 'files', channel, version);
  const bundleOutputPath = path.join(updatesDir, 'bundles', channel, `${version}.zip`);
  const manifestPath = path.join(updatesDir, 'channels', channel, 'manifest.json');
  const indexPath = path.join(outDir, 'index.html');
  const encodedChannel = encodeURIComponent(channel);
  const encodedVersion = encodeURIComponent(version);

  await fs.rm(outDir, { recursive: true, force: true });
  await fs.rm(filesOutputDir, { recursive: true, force: true });
  await fs.mkdir(path.dirname(bundleOutputPath), { recursive: true });
  await fs.mkdir(path.dirname(manifestPath), { recursive: true });

  console.log(`Using BioModStack frontend at: ${frontendDir}`);
  console.log(`Publishing mobile UI update into: ${updatesDir}`);
  console.log(`Channel: ${channel}`);
  console.log(`Version: ${version}`);

  run('pnpm', ['exec', 'vite', 'build', '--base', './', '--outDir', outDir, '--emptyOutDir'], {
    cwd: frontendDir,
  });

  const descriptor = buildBundleDescriptor(await fs.readFile(indexPath, 'utf8'), {
    version,
    shellApiVersion: runtimeConfig.shellApiVersion,
  });
  await fs.writeFile(path.join(outDir, 'descriptor.json'), `${JSON.stringify(descriptor, null, 2)}\n`, 'utf8');

  const relativeFiles = await collectRelativeFiles(outDir);
  await fs.cp(outDir, filesOutputDir, { recursive: true });
  await zipDirectory(outDir, bundleOutputPath);
  const sha256 = await sha256File(bundleOutputPath);
  const assetBaseUrl = `${apiBaseUrl}/api/mobile-ui/files/${encodedChannel}/${encodedVersion}/`;
  const manifest = {
    channel,
    version,
    download_url: `${apiBaseUrl}/api/mobile-ui/bundles/${encodedChannel}/${encodedVersion}.zip`,
    asset_base_url: assetBaseUrl,
    sha256,
    shell_api_version: descriptor.shellApiVersion,
    published_at: new Date().toISOString(),
    descriptor,
    files: relativeFiles.map((relativePath) => ({
      path: relativePath,
      url: `${assetBaseUrl}${encodePathForUrl(relativePath)}`,
    })),
  };

  await fs.writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');

  const stats = await fs.stat(bundleOutputPath);
  console.log(JSON.stringify({
    channel,
    version,
    updatesDir,
    manifestPath,
    filesOutputDir,
    bundleOutputPath,
    bundleSize: stats.size,
    sha256,
    assetCount: relativeFiles.length,
  }, null, 2));
}

if (process.argv[1] && path.resolve(process.argv[1]) === __filename) {
  main().catch((error) => {
    console.error(error.stack || String(error));
    process.exit(1);
  });
}
