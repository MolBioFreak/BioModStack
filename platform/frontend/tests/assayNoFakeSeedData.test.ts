import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const root = process.cwd();
const assayFiles = [
  'src/components/qpcr/DeltaCqPanels.tsx',
  'src/components/qpcr/QuantificationPanels.tsx',
  'src/components/hplc/ChromatogramAnalysis.tsx',
  'src/components/statistics/ControlChart.tsx',
  'src/components/statistics/DoEPanel.tsx',
  'src/components/statistics/HypothesisTesting.tsx',
  'src/components/statistics/ProcessCapability.tsx',
  'src/components/statistics/RegressionAnalysis.tsx',
];

const bannedSeedPatterns = [
  /const\s+SAMPLE_[A-Z_]+\s*=/,
  /useState\('1e6\\n1e5/,
  /useState\('15\.2\\n18\.5/,
  /useState\('Sample_A\\nSample_B/,
  /useState\('Caffeine'/,
  /useState\('Sample_1\\nSample_2/,
  /useState\('\[\[10\.2, 9\.8/,
  /useState\('-25\\n-30/,
  /useState\('10\\n15\\n20/,
  /useState\('52\\n68\\n85/,
  /Example SPC dataset/,
  /Example dataset/,
];

test('assay analytics UI does not pre-seed viewers with fake/demo data', () => {
  const violations: string[] = [];
  for (const relativePath of assayFiles) {
    const source = readFileSync(join(root, relativePath), 'utf8');
    for (const pattern of bannedSeedPatterns) {
      if (pattern.test(source)) {
        violations.push(`${relativePath}: ${pattern}`);
      }
    }
  }
  assert.deepEqual(violations, []);
});
