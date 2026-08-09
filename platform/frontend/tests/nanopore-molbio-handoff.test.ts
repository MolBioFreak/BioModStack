import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../src/components/NanoporeTemplate.tsx', import.meta.url), 'utf8');

test('ordinary Nanopore UI exposes no raw comparison-panel path control', () => {
  assert.doesNotMatch(source, /comparisonPanelSnapshot|comparison_panel_snapshot|comparison-panel snapshot path/i);
});

test('MolBio handoff obtains a server-issued receipt and submits only its id', () => {
  const api = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8');
  assert.match(source, /issueMolBioNgsReceipt/);
  assert.match(api, /\/ngs-receipts/u);
  assert.match(api, /request: MolBioNgsReceiptRequest/u);
  assert.match(source, /revision_id: selectedMolbioRevisionId/);
  assert.match(source, /molbio_ngs_receipt_id: molbioNgsReceiptId/);
  assert.doesNotMatch(source, /localStorage|uploadFile|referencePath|reference_fasta/);
});

test('comparison selection lists server-approved opaque ids and obtains a bound receipt', () => {
  assert.match(source, /\/api\/molbio\/ngs-comparison-panels/);
  assert.match(source, /ngs_comparison_panel_receipt_id: comparisonPanelReceiptId/);
  assert.match(source, /No approved comparison panels are available\./);
  assert.doesNotMatch(source, /comparison.*(path|url|download)/i);
});
