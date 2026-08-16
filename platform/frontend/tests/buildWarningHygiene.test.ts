import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { sanitizeMolstarCss } from '../build/molstarCssHygiene.js';

const readSource = (...parts: string[]) => fs.readFileSync(path.join(process.cwd(), ...parts), 'utf8');


test('Molstar CSS sanitizer removes Firefox parser warnings without changing valid rules', () => {
  const css = [
    '.msp-plugin button::-moz-focus-inner,.msp-plugin input::-moz-focus-inner{border:0;padding:0}',
    '.msp-plugin .field:-ms-input-placeholder{color:#9c835f}',
    '.msp-plugin .icon{display:block-inline;font-weight:light}',
    '.msp-plugin .track{background-color:tint(rgb(51, 43, 31), 60%)}',
    '.msp-plugin .dot{border-color:tint(rgb(51, 43, 31), 50%)}',
    '.msp-plugin .file{filter:alpha(opacity=0);opacity:0}',
    '.msp-plugin .valid{display:grid;grid-template-columns:repeat(6, auto)}',
  ].join('');

  const sanitized = sanitizeMolstarCss(css);
  for (const obsolete of ['::-moz-focus-inner', ':-ms-input-placeholder', 'block-inline', 'font-weight:light', 'tint(', 'filter:alpha(']) {
    assert.equal(sanitized.includes(obsolete), false, `${obsolete} must be absent`);
  }
  assert.match(sanitized, /display:inline-block/u);
  assert.match(sanitized, /font-weight:300/u);
  assert.match(sanitized, /background-color:#adaaa5/u);
  assert.match(sanitized, /border-color:#99958f/u);
  assert.match(sanitized, /opacity:0/u);
  assert.match(sanitized, /display:grid;grid-template-columns:repeat\(6, auto\)/u);
});

test('vite applies the sanitizer only to the Molstar viewer stylesheet', () => {
  const source = readSource('vite.config.ts');
  assert.match(source, /molstarCssHygienePlugin/u);
  assert.match(source, /molstar\/build\/viewer\/molstar\.css/u);
  assert.match(source, /sanitizeMolstarCss/u);
});

test('vite no longer carries a PDBe eval-warning suppression', () => {
  const source = readSource('vite.config.ts');
  assert.doesNotMatch(source, /isExpectedPdbeMolstarEvalWarning|pdbe-molstar-component|warning\.code\s*===\s*['"]EVAL['"]/u);
  assert.match(source, /chunkSizeWarningLimit:\s*6500/u, 'chunk budget should be explicit and paired with manual scientific-vendor chunking');
});

test('top-level scientific workstation pages are route-lazy loaded', () => {
  const source = readSource('src', 'App.tsx');
  const heavyRoutes = [
    ['Dashboard', './components/Dashboard'],
    ['JobSubmission', './components/JobSubmission'],
    ['ResultsViewer', './components/ResultsViewer'],
    ['JobDetailPage', './components/JobDetailPage'],
    ['MolBioToolkitV2', './components/MolBioToolkit/indexV2'],
    ['NGSToolkit', './components/NGSToolkit'],
    ['BioXpCockpit', './components/BioXpCockpit'],
    ['InfraMonitorPage', './components/InfraMonitorPage'],
  ] as const;

  assert.match(source, /import\s*\{\s*lazy\s*,\s*Suspense\s*\}\s*from ['"]react['"]/u);
  assert.match(source, /<Suspense\s+fallback=/u);

  for (const [symbol, modulePath] of heavyRoutes) {
    const escapedModulePath = modulePath.replaceAll('/', '\\/');
    assert.doesNotMatch(
      source,
      new RegExp(`import\\s*\\{[^}]*\\b${symbol}\\b[^}]*\\}\\s*from ['"]${escapedModulePath}['"]`, 'u'),
      `${symbol} should not be statically imported into the initial app chunk`,
    );
    assert.match(
      source,
      new RegExp(`const\\s+${symbol}\\s*=\\s*lazy\\(\\s*\\(\\)\\s*=>\\s*import\\(['"]${escapedModulePath}['"]\\)`, 'u'),
      `${symbol} should be loaded through React.lazy`,
    );
  }
});

test('vite build has explicit manual chunks and a post-split chunk-size budget', () => {
  const source = readSource('vite.config.ts');
  assert.match(source, /manualChunks\s*\(/u);
  for (const chunkName of ['vendor-react', 'vendor-blueprint', 'vendor-plotly', 'vendor-seqviz', 'vendor-igv', 'vendor-molstar']) {
    assert.match(source, new RegExp(`['"]${chunkName}['"]`, 'u'), `${chunkName} manual chunk is required`);
  }
  assert.match(source, /commonjsHelpers\.js/u, 'CommonJS helpers should stay out of the generic vendor chunk to avoid React/Plotly circular init crashes');
  assert.match(source, /\/node_modules\/@plotly\//u, 'Plotly subpackages should not fall through into the generic vendor chunk');
  assert.match(source, /\/node_modules\/seqviz\//u, 'SeqViz should not fall through into the generic vendor chunk');
  assert.match(source, /demoConstructs\.generated/u, 'generated MolBio demo data should stay outside the initial app chunk');
  assert.match(source, /chunkSizeWarningLimit:\s*6500/u, 'Vite should only raise the warning budget after manual scientific-vendor chunking');
});
