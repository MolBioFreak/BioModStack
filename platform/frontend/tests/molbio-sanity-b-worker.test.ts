import assert from 'node:assert/strict';
import test from 'node:test';
import { Worker } from 'node:worker_threads';
import { createRequire } from 'node:module';
// Use Vite's locked compiler dependency without installing into the donor.
const { buildSync } = createRequire(createRequire(import.meta.url).resolve('vite'))('esbuild');
import { searchSequence, type SearchInput } from '../src/components/MolBioToolkit/panels/sequenceSearch';
import { startSequenceSearch, type SearchWorker, type SearchReply } from '../src/components/MolBioToolkit/panels/startSequenceSearch';
const input: SearchInput = { sequence: 'ACGTACGT', query: 'GTAC', circular: true, caseSensitive: false, regex: true, bothStrands: true, sequenceType: 'dna' };
test('regex retains circular segments, zero-length progress, escapes and syntax errors', () => {
 assert.deepEqual(searchSequence(input).map(m => m.segments), [[{ start: 2, end: 6 }], [{ start: 6, end: 8 }, { start: 0, end: 2 }]]);
 assert.equal(searchSequence({ ...input, query: '(?=A)', circular: false }).length, 2);
 assert.equal(searchSequence({ ...input, query: '\\bAC', circular: false }).length, 1);
 assert.throws(() => searchSequence({ ...input, query: '[(' }), SyntaxError);
 assert.throws(() => searchSequence({ ...input, sequence: 'A'.repeat(10002), query: 'A{1}', circular: false }), /10,000/);
 assert.ok(searchSequence({ ...input, query: 'ACG', regex: false }).some(m => m.strand === -1));
});
const bundle = buildSync({ entryPoints: [new URL('../src/components/MolBioToolkit/panels/sequenceSearch.worker.ts', import.meta.url).pathname], bundle: true, write: false, format: 'iife', platform: 'browser' }).outputFiles[0].text;
function factory(counts: { started: number; terminated: number }): () => SearchWorker {
 return () => {
  const native = new Worker(`const {parentPort} = require('node:worker_threads'); globalThis.self = {postMessage: data => parentPort.postMessage(data)}; ${bundle}; parentPort.on('message', data => { parentPort.postMessage({started:true}); self.onmessage({data}); });`, { eval: true });
  const adapter: SearchWorker = { onmessage: null, onerror: null, postMessage: data => native.postMessage(data), terminate: () => { counts.terminated++; void native.terminate(); } };
  native.on('message', data => { if (data.started) counts.started++; else adapter.onmessage?.call(adapter as any, { data } as MessageEvent); });
  native.on('error', error => adapter.onerror?.call(adapter as any, { message: error.message } as ErrorEvent));
  return adapter;
 };
}
test('real worker reports valid results and terminates on completion', async () => {
 const counts = { started: 0, terminated: 0 };
 const reply = await new Promise<SearchReply>(resolve => startSequenceSearch(input, resolve, factory(counts)));
 assert.equal(reply.results?.length, 2); assert.deepEqual(counts, { started: 1, terminated: 1 });
});
test('pathological backtracking is interrupted by terminating the real worker', async () => {
 const counts = { started: 0, terminated: 0 };
 const reply = await new Promise<SearchReply>(resolve => startSequenceSearch({ ...input, sequence: 'A'.repeat(4000) + '!', query: '^(A+)+$', circular: false }, resolve, factory(counts), 1000));
 assert.match(reply.error!, /timed out/); assert.equal(reply.results, undefined);
 assert.deepEqual(counts, { started: 1, terminated: 1 });
});
test('cancel terminates resources and suppresses completion', async () => {
 const counts = { started: 0, terminated: 0 }; let calls = 0;
 const cancel = startSequenceSearch(input, () => { calls++; }, factory(counts)); cancel();
 await new Promise(resolve => setTimeout(resolve, 40));
 assert.equal(calls, 0); assert.equal(counts.terminated, 1);
});
