import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { remotePullError, remoteResultQueryKeys, remoteResultsState, type RemoteResultsJob } from '../src/components/remoteResultsState.js';

const ready: RemoteResultsJob = {
    id: 'remote-job', execution_target_id: 'worker', status: 'awaiting_input', queue_status: 'completed',
    awaiting_input: true, awaiting_stage: 'remote_results', remote_state: 'results_available',
};

test('ready prompt derives from persisted state, including a fresh reload', () => {
    assert.deepEqual(remoteResultsState(ready), { busy: false, failed: false, received: false, label: 'Pull results' });
    assert.deepEqual(remoteResultsState(JSON.parse(JSON.stringify(ready))), remoteResultsState(ready));
    assert.equal(remoteResultsState({ ...ready, status: undefined }), null);
});

test('local, terminal, unrelated awaiting gates and remote execution never offer a pull', () => {
    for (const patch of [
        { execution_target_id: null }, { status: 'completed' }, { status: 'running' },
        { status: 'failed' }, { awaiting_input: false }, { awaiting_input: undefined },
        { awaiting_stage: 'post_rfantibody' }, { remote_state: 'running' },
    ]) assert.equal(remoteResultsState({ ...ready, ...patch }), null, JSON.stringify(patch));
});

test('returning disables the pull and persistent failure offers only explicit retry', () => {
    assert.deepEqual(remoteResultsState({ ...ready, status: 'running', queue_status: 'running', remote_state: 'returning' }),
        { busy: true, failed: false, received: false, label: 'Pulling results…' });
    assert.deepEqual(remoteResultsState({ ...ready, remote_state: 'result_pull_failed', error_message: 'Checksum mismatch' }),
        { busy: false, failed: true, received: false, label: 'Retry pull' });
    assert.equal(remotePullError({ response: { data: { detail: 'Worker identity changed' } } }), 'Worker identity changed');
    assert.equal(remotePullError({ response: { data: { detail: { message: 'Attempt mismatch' } } } }), 'Attempt mismatch');
    assert.equal(remotePullError(new Error('Network unavailable')), 'Network unavailable');
    assert.equal(remotePullError(null), null);
});

test('compact receipt projection agrees with full receipts without changing eligible states', () => {
    for (const received of [true, false]) {
        const provenance = { remote_execution_receipt: {
            result_manifest_sha256: 'current', received_manifest_sha256: received ? 'current' : 'previous',
        } };
        for (const phase of [
            { status: 'awaiting_input', remote_state: 'result_pull_failed' },
            { status: 'running', remote_state: 'returning' },
        ]) {
            const detail = { ...ready, ...phase, provenance };
            const summary = { ...ready, ...phase, remote_results_received: received };
            assert.deepEqual(remoteResultsState(summary), remoteResultsState(detail));
            assert.equal(remoteResultsState(summary)?.received, received);
        }
    }
    const failed = { ...ready, remote_state: 'result_pull_failed' };
    assert.equal(remoteResultsState({ ...failed, remote_results_received: null })?.received, false);
    // Explicit server projection takes precedence; null/omitted falls back to old payloads.
    const provenance = { remote_execution_receipt: { received_manifest_sha256: 'current', result_manifest_sha256: 'current' } };
    assert.equal(remoteResultsState({ ...failed, provenance, remote_results_received: false })?.received, false);
    assert.equal(remoteResultsState({ ...failed, provenance, remote_results_received: null })?.received, true);
    assert.equal(remoteResultsState({ ...failed, remote_results_received: true, status: 'completed' }), null);
    assert.equal(remoteResultsState({ ...failed, remote_results_received: true, awaiting_stage: 'other' }), null);
});

test('only classified received terminal import failure offers explicit import retry', () => {
    const failed = { ...ready, status: 'failed', queue_status: 'failed', awaiting_input: false,
        awaiting_stage: null, remote_state: 'result_import_failed', remote_results_received: true };
    const expected = { busy: false, failed: true, received: true, terminal: true, label: 'Retry import' };
    assert.deepEqual(remoteResultsState(failed), expected);
    const { remote_results_received: _projection, ...detail } = failed;
    assert.deepEqual(remoteResultsState({ ...detail, provenance: { remote_execution_receipt: {
        received_manifest_sha256: 'current', result_manifest_sha256: 'current',
    } } }), expected);
    for (const patch of [
        { status: 'cancelled' }, { status: 'completed' }, { status: 'running' },
        { remote_state: 'failed' }, { remote_state: 'result_pull_failed' }, { remote_state: 'returned_ingestion_failed' }, { remote_state: null },
        { execution_target_id: null }, { remote_results_received: false }, { remote_results_received: undefined },
    ]) assert.equal(remoteResultsState({ ...failed, ...patch }), null, JSON.stringify(patch));
    for (const received_manifest_sha256 of ['', 'old']) {
        assert.equal(remoteResultsState({ ...detail, provenance: { remote_execution_receipt: {
            received_manifest_sha256, result_manifest_sha256: 'current',
        } } }), null);
    }
});

test('ingestion refreshes existing jobs and output query families', () => {
    const keys = remoteResultQueryKeys(ready.id);
    for (const key of [['queue'], ['jobs'], ['job', ready.id], ['designs'], ['structure-files', ready.id], ['docking-results', ready.id]]) {
        assert.ok(keys.some(candidate => JSON.stringify(candidate) === JSON.stringify(key)));
    }
});

// Source wiring assertions complement the pure state tests; not browser/click execution proof.
test('POST is wired only to explicit click, with no mutation retries or duplicate clicks', () => {
    const source = readFileSync('src/components/RemoteResultsPrompt.tsx', 'utf8');
    const api = readFileSync('src/lib/api.ts', 'utf8');
    assert.match(api, /api\.post<Job>\(`\/api\/jobs\/\$\{encodeURIComponent\(jobId\)\}\/remote-results\/pull`\)/u);
    assert.match(source, /mutationFn: \(\) => pullRemoteJobResults\(job\.id\)/u);
    assert.match(source, /retry: false/u);
    assert.match(source, /onClick=\{handlePull\} disabled=\{busy\}/u);
    assert.match(source, /if \(busy \|\| clickInFlight\.current \|\| queryClient\.isMutating/u);
    assert.match(source, /useIsMutating\(\{ mutationKey \}\)/u);
    assert.equal(source.match(/mutation\.mutate\(/gu)?.length, 1);
    const effect = source.slice(source.indexOf('useEffect(() =>'), source.indexOf('const mutation ='));
    assert.doesNotMatch(effect, /mutate\(|pullRemoteJobResults\(/u);
    assert.match(effect, /job\.status === 'completed'/u);
    assert.match(source, /Results reported ready on worker/u);
    assert.match(source, /job\.error_message \|\| job\.remote_waiting_reason/u);
    assert.match(source, /role="alert"/u);
    assert.match(source, /onSettled: async[\s\S]*invalidateQueries/u);
});

test('normal queue and details retain the prompt and poll only server job metadata', () => {
    const queue = readFileSync('src/components/JobQueuePanel.tsx', 'utf8');
    assert.match(queue, /const awaitingJobs = visibleQueue\.filter\(j => remoteResultsState\(j\)\)/u);
    assert.match(queue, /awaitingJobs\.map\(job =>/u);
    assert.match(queue, /previousRemoteResults\.current/u);
    for (const filename of ['JobQueuePanel.tsx', 'JobDetailsPanel.tsx', 'JobDetailPage.tsx']) {
        const source = readFileSync(`src/components/${filename}`, 'utf8');
        assert.match(source, /<RemoteResultsPrompt job=\{job\} \/>/u);
        assert.doesNotMatch(source, /pullRemoteJobResults\(/u);
    }
    const detail = readFileSync('src/components/JobDetailPage.tsx', 'utf8');
    const table = readFileSync('src/components/dashboard/JobQueueTable.tsx', 'utf8');
    assert.match(table, /job\.status === 'awaiting_input' && !\(job\.execution_target_id && job\.awaiting_stage === 'remote_results'\)/u);
    assert.match(table, /<RemoteResultsPrompt job=\{job\} \/>/u);
    assert.match(readFileSync('src/components/RemoteResultsPrompt.tsx', 'utf8'), /Execution finished; worker availability is checked when you pull/u);
    assert.match(detail, /results && !results\.terminal\) \? jobPollingInterval/u);
});
