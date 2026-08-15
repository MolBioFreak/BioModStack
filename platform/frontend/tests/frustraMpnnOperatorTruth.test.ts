import assert from 'node:assert/strict';
import test from 'node:test';

import type { Job } from '../src/lib/api.js';
import { assertTerminalSourceAuthority } from '../src/lib/frustraMpnnViewerAuthority.js';
import {
    getCompletedScientificResultStatus,
    getJobOutputSummary,
} from '../src/lib/jobOutputSummary.js';

const baseJob = {
    id: 'job-1',
    name: 'FrustraMPNN analysis job-1',
    status: 'completed',
    model_id: 'frustrampnn',
    mode: 'analyze',
    params: {},
    created_at: '2026-08-15T00:00:00Z',
    design_count: 0,
    output_dir: null,
} satisfies Job;

test('FrustraMPNN output summary uses accepted result entities instead of generic design rows', () => {
    const output = getJobOutputSummary({ ...baseJob, frustrampnn_result_count: 1 });
    assert.deepEqual(output, { count: 1, label: '1 FrustraMPNN result' });
});

test('generic output summary preserves requested design semantics', () => {
    const output = getJobOutputSummary({ ...baseJob, model_id: 'boltz2', design_count: 2, requested_design_count: 4 });
    assert.deepEqual(output, { count: 4, label: '4 designs' });
});

test('API default zero FrustraMPNN count does not relabel unrelated jobs', () => {
    const output = getJobOutputSummary({
        ...baseJob,
        model_id: 'protenix',
        design_count: 0,
        requested_design_count: 5,
        frustrampnn_result_count: 0,
    });
    assert.deepEqual(output, { count: 5, label: '5 designs' });
});

test('completed FrustraMPNN execution with zero accepted results is explicit and non-green', () => {
    assert.deepEqual(getCompletedScientificResultStatus('completed', 0), {
        styleKey: 'no_results',
        label: 'completed · no accepted results',
    });
    assert.equal(getCompletedScientificResultStatus('completed', 1), null);
});

test('omitted optional terminal source authority is accepted', () => {
    assert.doesNotThrow(() => assertTerminalSourceAuthority(null, 'a'.repeat(64)));
    assert.doesNotThrow(() => assertTerminalSourceAuthority(undefined, 'a'.repeat(64)));
});

test('supplied matching terminal source authority is accepted', () => {
    assert.doesNotThrow(() => assertTerminalSourceAuthority({ sha256: 'a'.repeat(64) }, 'a'.repeat(64)));
});

test('supplied conflicting terminal source authority fails closed', () => {
    assert.throws(
        () => assertTerminalSourceAuthority({ sha256: 'b'.repeat(64) }, 'a'.repeat(64)),
        /terminal_source_hash_conflict/,
    );
    assert.throws(
        () => assertTerminalSourceAuthority({ sha256: null }, 'a'.repeat(64)),
        /terminal_source_hash_conflict/,
    );
});
