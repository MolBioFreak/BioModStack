Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true });
Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
    configurable: true,
    value: () => new Proxy({}, {
        get: (_target, property) => property === 'measureText' ? (() => ({ width: 0 })) : (() => undefined),
        set: () => true,
    }),
});
if (!URL.createObjectURL) URL.createObjectURL = () => 'blob:vitest';
