/** Existing Results return contract, expressed relative to the mounted router. */
export function mountedApplicationBase(): string {
    const path = typeof window === 'undefined' ? '' : window.location.pathname;
    const match = path.match(/^(.*\/)(?:designs\/|submit(?:\/|$))/);
    return match?.[1] ?? import.meta.env.BASE_URL ?? '/';
}
export function sourceResultReturn(value: string | null, baseUrl = mountedApplicationBase()): string | null {
    const base = (baseUrl || '/').replace(/\/+$/, '');
    const route = base && value?.startsWith(`${base}/`) ? value.slice(base.length) : value;
    return route && /^\/designs\/[^\\\s?#]+(?:[?#][^\\\r\n]*)?$/.test(route) ? route : null;
}
