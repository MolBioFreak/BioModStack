import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

function read(relativePath: string): string {
    return readFileSync(join(process.cwd(), relativePath), 'utf8');
}

const api = read('src/lib/api.ts');
const panel = read('src/components/ngs/PooledAssignmentReviewPanel.tsx');
const toolkit = read('src/components/NGSToolkit.tsx');

test('pooled assignment helpers use the exact review and release contracts', () => {
    assert.match(api, /\/api\/jobs\/\$\{encodeURIComponent\(assignmentJobId\)\}\/pooled-assignment\/manifest/u);
    assert.match(api, /\/api\/jobs\/\$\{encodeURIComponent\(assignmentJobId\)\}\/pooled-assignment\/targets/u);
    assert.match(api, /\/api\/jobs\/\$\{encodeURIComponent\(assignmentJobId\)\}\/pooled-assignment\/release/u);

    const requestBlock = api.match(/export interface PooledAssignmentReleaseRequest \{([\s\S]*?)\n\}/u)?.[1] || '';
    assert.match(requestBlock, /idempotency_key: string;/u);
    assert.match(requestBlock, /target_workflow: PooledAssignmentTargetWorkflow;/u);
    assert.match(requestBlock, /name_prefix\?: string;/u);
    assert.match(requestBlock, /pinned_gpu\?: number;/u);
    assert.match(requestBlock, /target_ids: string\[\];/u);
    assert.doesNotMatch(requestBlock, /fastq_path|mappings|targets:/u);

    const responseBlock = api.match(/export interface PooledAssignmentReleaseResponse \{([\s\S]*?)\n\}/u)?.[1] || '';
    for (const field of ['release_id', 'assignment_job_id', 'reference_set_id', 'child_job_ids']) {
        assert.match(responseBlock, new RegExp(`${field}:`));
    }
});

test('review panel is explicit, review-first, atomic, and has no automatic release path', () => {
    assert.match(panel, /isPooledAssignmentJob\(mode, ontWorkflowId\)/u);
    assert.match(panel, /'pooled_reference_assignment'/u);
    assert.match(panel, /ont_pooled_reference_assignment/u);
    assert.match(panel, /useState\(newIdempotencyKey\)/u);
    assert.match(panel, /target_ids: targetIds/u);
    assert.match(panel, /Release is one explicit atomic request/u);
    assert.match(panel, /scientificStatus.*'REVIEW'/u);
    assert.match(panel, /assignment_summary/u);
    assert.match(panel, /per_read_assignment/u);
    assert.match(panel, /intended_pool/u);
    assert.match(panel, /targetId !== 'ambiguous'/u);
    assert.match(panel, /targetId !== 'unclassified'/u);
    assert.doesNotMatch(panel, /useEffect\([\s\S]*releasePooledAssignment/u);
    assert.equal((panel.match(/<button\b/gu) || []).length, 1);
});

test('NGS completed-job inspector mounts the pooled review panel with job artifacts', () => {
    assert.match(toolkit, /<PooledAssignmentReviewPanel/u);
    assert.match(toolkit, /jobStatus=\{selectedJob\.status\}/u);
    assert.match(toolkit, /mode=\{selectedJob\.mode\}/u);
    assert.match(toolkit, /ontWorkflowId=\{selectedJob\.params\?\.ont_workflow_id\}/u);
    assert.match(toolkit, /stageOutputs=\{stageOutputs\}/u);
    assert.match(toolkit, /files=\{selectedJob\.files\}/u);
    assert.match(toolkit, /results=\{selectedJob\.results\}/u);
});
