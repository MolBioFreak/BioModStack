import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

function readSource(relativePath: string): string {
    return readFileSync(join(process.cwd(), relativePath), 'utf8');
}

const barcodePanel = readSource('src/components/ngs/BarcodeUnitsPanel.tsx');

// Source policy only: this does not exercise receipt issuance or batch submission.
test('barcode panel source forbids per-unit submit and literal unclassified mappings', () => {
    assert.doesNotMatch(barcodePanel, /submitOntBarcodeUnit|barcode-units\/.*submit/u);
    assert.doesNotMatch(barcodePanel, /unit_id: 'unclassified'/u);
});
