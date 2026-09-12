import { searchSequence, type SearchInput } from './sequenceSearch';
self.onmessage = (event: MessageEvent<SearchInput>) => {
    try { self.postMessage({ results: searchSequence(event.data) }); }
    catch (error) { self.postMessage({ error: error instanceof Error ? error.message : String(error) }); }
};
