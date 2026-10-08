import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const readSource = (...parts: string[]) => fs.readFileSync(path.join(process.cwd(), ...parts), 'utf8');

test('antibody launcher does not dynamically import pdbUtils after static import', () => {
  const source = readSource('src', 'components', 'AntibodyDenovoTemplate.tsx');
  assert.match(source, /from '\.\.\/utils\/pdbUtils'/u);
  assert.doesNotMatch(source, /import\(['"]\.\.\/utils\/pdbUtils['"]\)/u);
  assert.doesNotMatch(source, /import\(['"]\.\.\/utils\/pdbUtils['"]\)\.Chain/u);
});

test('vite suppresses only the pinned PDBe Molstar vendor eval warning', () => {
  const source = readSource('vite.config.ts');
  assert.match(source, /isExpectedPdbeMolstarEvalWarning/u);
  assert.match(source, /warning\.code\s*===\s*['"]EVAL['"]/u);
  assert.match(source, /pdbe-molstar/u);
  assert.match(source, /pdbe-molstar-component\.js/u);
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
    ['AssayAnalytics', './components/AssayAnalytics'],
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
