import type { SearchInput, SearchMatch } from './sequenceSearch';
export type SearchReply = { results?: SearchMatch[]; error?: string };
export type SearchWorker = Pick<Worker, 'postMessage' | 'terminate' | 'onmessage' | 'onerror'>;
/** Termination interrupts even pathological native RegExp backtracking. */
export function startSequenceSearch(
    input: SearchInput,
    complete: (reply: SearchReply) => void,
    createWorker: () => SearchWorker = () => new Worker(new URL('./sequenceSearch.worker.ts', import.meta.url), { type: 'module' }),
    timeoutMs = 1000,
): () => void {
    let worker: SearchWorker;
    try { worker = createWorker(); }
    catch (error) { complete({ error: `Search worker unavailable: ${String(error)}` }); return () => {}; }
    let finished = false;
    const stop = () => { if (finished) return; finished = true; clearTimeout(timer); worker.terminate(); };
    const finish = (reply: SearchReply) => { if (finished) return; stop(); complete(reply); };
    const timer = setTimeout(() => finish({ error: 'Search timed out. Narrow the pattern and try again; no partial result was applied.' }), timeoutMs);
    worker.onmessage = (event: MessageEvent<SearchReply>) => finish(event.data);
    worker.onerror = (event: ErrorEvent) => finish({ error: event.message || 'Search worker failed' });
    try { worker.postMessage(input); }
    catch (error) { finish({ error: `Search could not start: ${String(error)}` }); }
    return stop;
}
