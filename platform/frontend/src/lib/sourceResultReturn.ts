/** Existing Results return contract, expressed relative to the mounted router. */
export function sourceResultReturn(value: string | null, baseUrl = import.meta.env.BASE_URL): string | null {
    const base = (baseUrl || '/').replace(/\/+$/, '');
    const route = base && value?.startsWith(`${base}/`) ? value.slice(base.length) : value;
    return route && /^\/designs\/[^\\\s?#]+(?:[?#][^\\\r\n]*)?$/.test(route) ? route : null;
}
