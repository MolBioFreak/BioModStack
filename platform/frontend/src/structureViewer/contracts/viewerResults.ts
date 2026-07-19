export type ViewerResult<T = void> =
    | { readonly status: 'ok'; readonly value: T }
    | { readonly status: 'unsupported'; readonly reason: string; readonly capability?: string }
    | { readonly status: 'ambiguous'; readonly reason: string; readonly candidates?: readonly string[] }
    | { readonly status: 'cancelled'; readonly reason: string }
    | { readonly status: 'error'; readonly error: Error };

export const viewerOk = <T>(value: T): ViewerResult<T> => ({ status: 'ok', value });
export const viewerUnsupported = <T = never>(reason: string, capability?: string): ViewerResult<T> => ({
    status: 'unsupported',
    reason,
    ...(capability ? { capability } : {}),
});
export const viewerAmbiguous = <T = never>(reason: string, candidates?: readonly string[]): ViewerResult<T> => ({
    status: 'ambiguous',
    reason,
    ...(candidates ? { candidates } : {}),
});
export const viewerCancelled = <T = never>(reason: string): ViewerResult<T> => ({ status: 'cancelled', reason });
export const viewerError = <T = never>(error: unknown): ViewerResult<T> => ({
    status: 'error',
    error: error instanceof Error ? error : new Error(String(error)),
});
