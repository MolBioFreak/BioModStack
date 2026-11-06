import { api } from '../../src/lib/api';
import { vi, expect } from 'vitest';

// Scientific/provider leaves are never invoked. Unexpected HTTP is a fixture
// failure, not a real localhost request or a generic fabricated success.
export function isolateDeNovoTransport() {
    const previous = api.defaults.adapter;
    const unexpected: string[] = [];
    api.defaults.adapter = async config => {
        const url = config.url ?? '';
        // This legacy parent fixture deliberately has unavailable discovery.
        // The real SQLite HTTP parent suite covers successful native discovery.
        if (url === '/api/models/protein_modification_experimental/generation-settings') throw new Error('Fixture: native discovery unavailable');
        const known: Record<string, unknown> = {
            '/api/system/status': { gpus: [], gpu_error: null },
            '/api/execution-targets': [],
            '/api/shape/geometries': { geometries: [] },
            '/api/models': [],
            '/api/models/protein_modification_experimental': { engine_references: {} },
            '/api/input-presets': [],
            '/api/inputs/presets': [],
            '/api/shape-blueprint/geometries': { geometries: [] },
            '/api/shape-blueprint/settings': { rfd3: { initial_values: {}, params: [] }, validators: {} },
            '/api/shape-blueprint/sequence-settings/proteinmpnn': { engine: 'proteinmpnn', initial_values: {}, contextual_defaults: {}, params: [] },
        };
        if (!Object.hasOwn(known, url)) {
            unexpected.push(`${config.method} ${url}`);
            throw new Error(`Unmocked de novo HTTP: ${config.method} ${url}`);
        }
        return { config, status: 200, statusText: 'OK', headers: {}, data: known[url] };
    };
    const xhr = vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => { unexpected.push('XHR bypassed adapter'); throw new Error('Network disabled'); });
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { unexpected.push('fetch bypassed adapter'); throw new Error('Network disabled'); });
    return () => { api.defaults.adapter = previous; xhr.mockRestore(); fetch.mockRestore(); expect(unexpected).toEqual([]); };
}
