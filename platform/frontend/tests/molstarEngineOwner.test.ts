import assert from 'node:assert/strict';
import test from 'node:test';

import {
    MolstarEngineOwner,
    MolstarOwnerCancelledError,
} from '../src/structureViewer/runtime/MolstarEngineOwner.js';
import type {
    MolstarOwnedPlugin,
    MolstarOwnedUiRoot,
    MolstarPluginFactory,
} from '../src/structureViewer/runtime/MolstarEngineOwner.js';

const target = {} as HTMLElement;
const container = {} as Element;

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

function makePlugin(calls: string[], label = 'plugin'): MolstarOwnedPlugin {
    return {
        dispose() {
            calls.push(`${label}.dispose`);
        },
    };
}

function makeRoot(calls: string[], label = 'root'): MolstarOwnedUiRoot {
    return {
        render() {
            calls.push(`${label}.render`);
        },
        unmount() {
            calls.push(`${label}.unmount`);
        },
    };
}

test('terminal teardown unmounts the React UI before disposing the plugin and is idempotent', async () => {
    const calls: string[] = [];
    const plugin = makePlugin(calls);
    const factory: MolstarPluginFactory = async ({ publishPlugin, render }) => {
        publishPlugin(plugin);
        render('viewer-ui', container);
        return plugin;
    };
    const owner = new MolstarEngineOwner({
        createPlugin: factory,
        createUiRoot: () => makeRoot(calls),
    });

    const result = await owner.initialize(target);
    assert.equal(result.status, 'ok');

    owner.dispose();
    owner.dispose();

    assert.deepEqual(calls, ['root.render', 'root.unmount', 'plugin.dispose']);
    assert.equal(owner.activePlugin, undefined);
});

test('late plugin publication after dispose is cancelled and immediately torn down', async () => {
    const calls: string[] = [];
    const gate = deferred<MolstarOwnedPlugin>();
    const plugin = makePlugin(calls);
    let publishPlugin!: (plugin: MolstarOwnedPlugin) => void;
    let render!: (component: unknown, container: Element) => void;

    const owner = new MolstarEngineOwner({
        createPlugin: async (context) => {
            publishPlugin = context.publishPlugin;
            render = context.render;
            return gate.promise;
        },
        createUiRoot: () => makeRoot(calls),
    });

    const pending = owner.initialize(target);
    owner.dispose();

    publishPlugin(plugin);
    assert.throws(() => render('late-ui', container), MolstarOwnerCancelledError);
    gate.resolve(plugin);

    assert.deepEqual(await pending, { status: 'cancelled', generation: 1 });
    assert.deepEqual(calls, ['plugin.dispose']);
    assert.equal(owner.activePlugin, undefined);
});

test('replacement invalidates an older pending generation without adopting its plugin', async () => {
    const calls: string[] = [];
    const firstGate = deferred<MolstarOwnedPlugin>();
    const firstPlugin = makePlugin(calls, 'first');
    const secondPlugin = makePlugin(calls, 'second');
    let firstPublish!: (plugin: MolstarOwnedPlugin) => void;
    let invocation = 0;

    const owner = new MolstarEngineOwner({
        createPlugin: async ({ publishPlugin, render }) => {
            invocation += 1;
            if (invocation === 1) {
                firstPublish = publishPlugin;
                return firstGate.promise;
            }
            publishPlugin(secondPlugin);
            render('second-ui', container);
            return secondPlugin;
        },
        createUiRoot: () => makeRoot(calls, invocation === 1 ? 'first-root' : 'second-root'),
    });

    const firstResult = owner.initialize(target);
    const secondResult = await owner.initialize(target);
    assert.equal(secondResult.status, 'ok');
    assert.equal(owner.activePlugin, secondPlugin);

    firstPublish(firstPlugin);
    firstGate.resolve(firstPlugin);

    assert.deepEqual(await firstResult, { status: 'cancelled', generation: 1 });
    assert.equal(owner.activePlugin, secondPlugin);
    assert.equal(calls.filter((entry) => entry === 'first.dispose').length, 1);

    owner.dispose();
    assert.deepEqual(calls.slice(-2), ['second-root.unmount', 'second.dispose']);
});

test('creation failure tears down any published plugin and UI root exactly once', async () => {
    const calls: string[] = [];
    const plugin = makePlugin(calls);
    const failure = new Error('create failed');
    const owner = new MolstarEngineOwner({
        createPlugin: async ({ publishPlugin, render }) => {
            publishPlugin(plugin);
            render('viewer-ui', container);
            throw failure;
        },
        createUiRoot: () => makeRoot(calls),
    });

    const result = await owner.initialize(target);

    assert.equal(result.status, 'error');
    if (result.status === 'error') assert.equal(result.error, failure);
    assert.deepEqual(calls, ['root.render', 'root.unmount', 'plugin.dispose']);
    assert.equal(owner.activePlugin, undefined);
});

test('deferred teardown aborts tasks immediately, then unmounts before plugin disposal', async () => {
    const calls: string[] = [];
    const scheduled: Array<() => void> = [];
    const plugin: MolstarOwnedPlugin = {
        managers: {
            task: {
                requestAbortAll(reason) {
                    calls.push(`abort:${reason}`);
                },
            },
        },
        dispose() {
            calls.push('plugin.dispose');
        },
    };
    const owner = new MolstarEngineOwner({
        createPlugin: async ({ publishPlugin, render }) => {
            publishPlugin(plugin);
            render('viewer-ui', container);
            return plugin;
        },
        createUiRoot: () => makeRoot(calls),
        scheduleTeardown: (teardown) => scheduled.push(teardown),
    });

    assert.equal((await owner.initialize(target)).status, 'ok');
    owner.dispose();
    owner.dispose();

    assert.deepEqual(calls, ['root.render', 'abort:BMS Mol* viewer disposed']);
    assert.equal(scheduled.length, 1);
    scheduled[0]();
    assert.deepEqual(calls, [
        'root.render',
        'abort:BMS Mol* viewer disposed',
        'root.unmount',
        'plugin.dispose',
    ]);
});

test('plugin disposal still runs when React root unmount throws', async () => {
    const calls: string[] = [];
    const errors: unknown[] = [];
    const plugin = makePlugin(calls);
    const owner = new MolstarEngineOwner({
        createPlugin: async ({ publishPlugin, render }) => {
            publishPlugin(plugin);
            render('viewer-ui', container);
            return plugin;
        },
        createUiRoot: () => ({
            render: () => calls.push('root.render'),
            unmount: () => {
                calls.push('root.unmount');
                throw new Error('root teardown failed');
            },
        }),
        onTeardownError: (error) => errors.push(error),
    });

    assert.equal((await owner.initialize(target)).status, 'ok');
    owner.dispose();

    assert.deepEqual(calls, ['root.render', 'root.unmount', 'plugin.dispose']);
    assert.equal(errors.length, 1);
    assert.match(String(errors[0]), /root teardown failed/);
});
