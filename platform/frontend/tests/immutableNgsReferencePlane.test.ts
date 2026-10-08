import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

function readSource(relativePath: string): string {
    return readFileSync(join(process.cwd(), relativePath), 'utf8');
}

const template = readSource('src/components/NanoporeTemplate.tsx');
const barcodePanel = readSource('src/components/ngs/BarcodeUnitsPanel.tsx');
const api = readSource('src/lib/api.ts');

test('barcode mappings issue receipts then submit one all-at-once batch without unclassified', () => {
    assert.doesNotMatch(barcodePanel, /submitOntBarcodeUnit|barcode-units\/.*submit/u);
    assert.match(barcodePanel, /issueMolBioNgsReceipt\(draft\.sequenceId, \{ revision_id: draft\.revisionId \}\)/u);
    assert.match(barcodePanel, /submitOntBarcodeBatch\(jobId, \{/u);
    assert.match(barcodePanel, /idempotency_key: newIdempotencyKey\('ont-barcode-batch'\)/u);
    assert.match(barcodePanel, /drafts\.some\(\(\{ draft \}\) => !draft\.sequenceId \|\| !draft\.revisionId\)/u);
    assert.match(barcodePanel, /Every canonical barcode unit requires a saved sequence and exact revision/u);
    assert.match(barcodePanel, /sample_alias: unit\.sample_alias \|\| draft\.sampleAlias/u);
    assert.match(barcodePanel, /disabled=\{Boolean\(unit\.sample_alias\)\}/u);
    assert.match(api, /child_job_ids: string\[\]/u);
    assert.match(barcodePanel, /mappings,/u);
    assert.match(barcodePanel, /data-testid="barcode-unclassified-row"/u);
    assert.match(barcodePanel, /Not assignable and never sent in mappings/u);
    assert.doesNotMatch(barcodePanel, /unit_id: 'unclassified'/u);
});

test('pooled assignment enforces 2-96 exact revisions and review-only release copy', () => {
    assert.match(template, /selectedWorkflow === 'pooledAssignment'/u);
    assert.match(template, /targets\.length < 2 \|\| targets\.length > 96/u);
    assert.match(template, /min_mapq: minMapq/u);
    assert.match(template, /min_alignment_score_margin: minAlignmentScoreMargin/u);
    assert.match(template, /indistinguishable_group/u);
    assert.match(template, /idempotency_key: newIdempotencyKey\('pooled-reference-assignment'\)/u);
    assert.match(api, /\/api\/ont\/ngs\/pooled-reference-assignment\/submit/u);
    assert.match(template, /job stops at REVIEW and requires explicit target release before consensus/u);
});
