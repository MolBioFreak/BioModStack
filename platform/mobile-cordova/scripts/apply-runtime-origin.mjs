#!/usr/bin/env node
// Stamp the operator's HTTPS origin into config.xml (the <access origin> and the
// BMS_API_ORIGIN preference the native updater requires) from the runtime config.
// The tracked config.xml carries a placeholder; build-apk.sh runs this per build.
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { normalizeConfig } from './prepare-bms-assets.mjs';

const __filename = fileURLToPath(import.meta.url);
const projectRoot = path.resolve(path.dirname(__filename), '..');
const PLACEHOLDER_SUFFIX = '.example.ts.net';

export function runtimeOrigin(apiBaseUrl) {
  const url = new URL(apiBaseUrl);
  if (url.protocol !== 'https:') return null;
  if (url.username || url.password || url.search || url.hash || (url.pathname && url.pathname !== '/')) {
    throw new Error('apiBaseUrl must be an exact HTTPS origin to serve as the native update origin');
  }
  return url.origin;
}

export function assertOperatorOrigin(origin, configPath) {
  if (new URL(origin).hostname.endsWith(PLACEHOLDER_SUFFIX)) {
    throw new Error(
      `${configPath} still holds the placeholder origin ${origin}; copy it to a cordova.runtime.*.local.json file and set your own HTTPS origin`,
    );
  }
}

export function stampConfigXml(xml, origin) {
  const access = /<access origin="https:\/\/(?!localhost")[^"]+"/g;
  const preference = /(<preference name="BMS_API_ORIGIN" value=")[^"]*(")/g;
  if ((xml.match(access) || []).length !== 1 || (xml.match(preference) || []).length !== 1) {
    throw new Error('config.xml must hold exactly one operator <access origin> and one BMS_API_ORIGIN preference');
  }
  return xml.replace(access, `<access origin="${origin}"`).replace(preference, `$1${origin}$2`);
}

async function main(argv = process.argv.slice(2)) {
  let configPath = path.join(projectRoot, 'cordova.runtime.json');
  let configXmlPath = path.join(projectRoot, 'config.xml');
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--config') configPath = path.resolve(argv[++index]);
    else if (argv[index] === '--config-xml') configXmlPath = path.resolve(argv[++index]);
    else throw new Error(`Unknown argument: ${argv[index]}`);
  }
  const runtime = normalizeConfig(JSON.parse(await readFile(configPath, 'utf8')));
  const origin = runtimeOrigin(runtime.apiBaseUrl);
  if (origin === null) {
    console.log('config.xml native origin left unchanged (apiBaseUrl is not HTTPS)');
    return;
  }
  assertOperatorOrigin(origin, configPath);
  const before = await readFile(configXmlPath, 'utf8');
  const after = stampConfigXml(before, origin);
  if (after !== before) await writeFile(configXmlPath, after);
  console.log(`config.xml native origin -> ${origin}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === __filename) {
  main().catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
}
