import { api } from '../../src/lib/api';
import { vi, expect } from 'vitest';

// Scientific/provider leaves are never invoked. Unexpected HTTP is a fixture
// failure, not a real localhost request or a generic fabricated success.
export function isolateDeNovoTransport() {
    const previous = api.defaults.adapter;
    const unexpected: string[] = [];
    api.defaults.adapter = async config => {
        const url = config.url ?? '';
        const known: Record<string, unknown> = {
            '/api/system/status': { gpus: [], gpu_error: null },
            '/api/execution-targets': [],
            '/api/shape/geometries': { geometries: [] },
            '/api/models': [],
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
