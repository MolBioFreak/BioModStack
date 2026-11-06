import { describe, expect, it, vi } from 'vitest';
import { MolstarDirectAdapter } from '../../src/structureViewer/adapters/MolstarDirectAdapter';

function adapterWith(results: Array<any>) {
    const adapter = new MolstarDirectAdapter();
    const owner = (adapter as any).owner;
    let i = 0;
    owner.initialize = vi.fn(async () => results[Math.min(i++, results.length - 1)]);
    owner.dispose = vi.fn();
    adapter.setBackground = async () => {};
    return { adapter, owner };
}
const plugin = (canvas3d: unknown) => ({ canvas3d, managers: { interactivity: { setProps() {} } }, behaviors: { interaction: { click: { subscribe: () => ({ unsubscribe() {} }) } } } });

describe('Mol* WebGL initialization', () => {
    it('retries a transient missing canvas3d and then mounts', async () => {
        const { adapter, owner } = adapterWith([{ status: 'ok', plugin: plugin(undefined) }, { status: 'ok', plugin: plugin(undefined) }, { status: 'ok', plugin: plugin({}) }]);
        await adapter.mount({} as HTMLElement);
        expect(owner.initialize).toHaveBeenCalledTimes(3);
        expect(owner.dispose).toHaveBeenCalledTimes(2);
    });
    it('still reports the WebGL error after the bounded retries', async () => {
        const { adapter, owner } = adapterWith([{ status: 'ok', plugin: plugin(undefined) }]);
        await expect(adapter.mount({} as HTMLElement)).rejects.toThrow('Mol* WebGL canvas failed to initialize');
        expect(owner.initialize).toHaveBeenCalledTimes(4);
    });
    it('does not retry a healthy canvas', async () => {
        const { adapter, owner } = adapterWith([{ status: 'ok', plugin: plugin({}) }]);
        await adapter.mount({} as HTMLElement);
        expect(owner.initialize).toHaveBeenCalledTimes(1);
    });
});
