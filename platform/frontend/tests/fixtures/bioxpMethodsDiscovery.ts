import type { InternalAxiosRequestConfig } from 'axios';
/** Legacy deck-editor fixtures do not exercise the parallel Methods library.
 * Supply only its pure discovery reads; mutation/compile endpoints are never
 * swallowed here. Methods has its own real-Axios lifecycle qualification. */
export function offlineMethodsDiscovery(config: InternalAxiosRequestConfig) {
    if (config.method !== 'get') return null;
    const replies: Record<string, unknown> = {
        '/api/bioxp/methods/catalog': { actions: [] },
        '/api/bioxp/methods/schema': { type: 'object', properties: {} },
        '/api/bioxp/methods/examples': [],
        '/api/bioxp/methods/library': { rows: [] },
        '/api/bioxp/methods/presets': { rows: [] },
    };
    if (!Object.hasOwn(replies, config.url ?? '')) return null;
    return { data: replies[config.url!], status: 200, statusText: 'OK', config, headers: {} };
}
