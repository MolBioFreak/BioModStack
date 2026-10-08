export function jobPollingInterval(intervalMs: number): number | false {
    if (typeof document !== 'undefined' && document.hidden) return false;
    if (typeof navigator !== 'undefined' && navigator.onLine === false) return false;
    return intervalMs;
}
