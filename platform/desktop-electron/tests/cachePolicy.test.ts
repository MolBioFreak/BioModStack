import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const REPO_ROOT = resolve(process.cwd(), '../..');
const MAIN_SOURCE = resolve(process.cwd(), 'src/main.ts');
const WEB_NGINX_CONFIG = resolve(REPO_ROOT, 'docker/web/nginx.conf');
const PRELOAD_DIST = resolve(process.cwd(), 'dist/src/preload.js');

test('Electron clears its HTTP cache before loading the BioModStack frontend', () => {
  const source = readFileSync(MAIN_SOURCE, 'utf8');

  assert.match(source, /webContents\.session\.clearCache\(\)/);
  assert.match(source, /clearCache\(\)[\s\S]*loadURL\(context\.windowUrl\)/);
});

test('sandboxed Electron preload is self-contained at runtime', () => {
  const preloadSource = readFileSync(PRELOAD_DIST, 'utf8');

  assert.doesNotMatch(preloadSource, /require\(["']\.\//);
});

test('core-runtime nginx does not serve index.html for missing hashed asset chunks', () => {
  const config = readFileSync(WEB_NGINX_CONFIG, 'utf8');

  assert.match(config, /location\s+\/bms\/assets\/\s*\{[\s\S]*try_files\s+\$uri\s+=404;/);
  assert.match(config, /location\s+=\s+\/bms\/index\.html\s*\{[\s\S]*Cache-Control[\s\S]*no-store/);
  const assetLocationMatch = config.match(/location\s+\/bms\/assets\/\s*\{([\s\S]*?)\n\s*\}/);
  assert.ok(assetLocationMatch, 'expected explicit /bms/assets/ location');
  assert.doesNotMatch(assetLocationMatch[1], /add_header\s+Cache-Control[^\n]*always/);
});
