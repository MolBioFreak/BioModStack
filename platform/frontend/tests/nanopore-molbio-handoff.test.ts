import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../src/components/NanoporeTemplate.tsx', import.meta.url), 'utf8');

test('ordinary Nanopore UI exposes no raw comparison-panel path control', () => {
  assert.doesNotMatch(source, /comparisonPanelSnapshot|comparison_panel_snapshot|comparison-panel snapshot path/i);
});

// Architectural source policy, not receipt issuance or submission acceptance.
test('Nanopore template source does not use file upload for molecular handoff', () => {
  assert.doesNotMatch(source, /uploadFile/);
});
test('comparison selection lists server-approved opaque ids and obtains a bound receipt', () => {
  assert.match(source, /\/api\/molbio\/ngs-comparison-panels/);
  assert.match(source, /ngs_comparison_panel_receipt_id: comparisonPanelReceiptId/);
  assert.doesNotMatch(source, /comparison.*(path|url|download)/i);
});
