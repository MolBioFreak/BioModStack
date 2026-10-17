/** Existing Results return contract: only local exact Results routes. */
export function sourceResultReturn(value: string | null): string | null {
    return value && /^\/designs\/[^\\\s?#]+(?:[?#][^\\\r\n]*)?$/.test(value) ? value : null;
}
