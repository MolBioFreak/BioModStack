export type LatestAsyncResourceController = {
    begin: () => number;
    isCurrent: (token: number) => boolean;
    dispose: () => void;
};

/** Makes completion of an older request or an unmounted owner a no-op. */
export function createLatestAsyncResourceController(): LatestAsyncResourceController {
    let token = 0;
    let disposed = false;
    return {
        begin: () => {
            disposed = false;
            token += 1;
            return token;
        },
        isCurrent: (candidate) => !disposed && candidate === token,
        dispose: () => {
            disposed = true;
            token += 1;
        },
    };
}
