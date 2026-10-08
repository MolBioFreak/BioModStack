import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { createLatestAsyncResourceController } from '../src/lib/latestAsyncResource.js';

const componentSource = readFileSync(
    fileURLToPath(new URL('../src/components/AntibodyDenovoTemplate.tsx', import.meta.url)),
    'utf8',
);

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

test('target parsing is newest-wins for success, error, and loading commits', async () => {
    const controller = createLatestAsyncResourceController();
    const older = deferred<string>();
    const newer = deferred<string>();
    const commits: string[] = [];

    const run = async (source: Promise<string>) => {
        const token = controller.begin();
        commits.push('loading:true');
        try {
            const value = await source;
            if (controller.isCurrent(token)) commits.push(`success:${value}`);
        } catch (error) {
            if (controller.isCurrent(token)) commits.push(`error:${String(error)}`);
        } finally {
            if (controller.isCurrent(token)) commits.push('loading:false');
        }
    };

    const olderRun = run(older.promise);
    const newerRun = run(newer.promise);
    newer.resolve('newest');
    await newerRun;
    older.reject(new Error('stale'));
    await olderRun;

    assert.deepEqual(commits, [
        'loading:true',
        'loading:true',
        'success:newest',
        'loading:false',
    ]);
});

test('target parse effect runs only for targetPdb and guards every completion path', () => {
    const start = componentSource.indexOf('// Parse the uploaded/selected target structure');
    const end = componentSource.indexOf('// Keep the active target chains/viewer content', start);
    assert.ok(start >= 0 && end > start, 'target parse effect must remain identifiable');
    const effect = componentSource.slice(start, end);

    assert.match(componentSource, /targetParseControllerRef\s*=\s*useRef\(createLatestAsyncResourceController\(\)\)/u);
    assert.match(effect, /targetParseControllerRef\.current\.begin\(\)/u);
    assert.equal((effect.match(/targetParseControllerRef\.current\.isCurrent\(/gu) || []).length, 3);
    assert.match(effect, /setSelectedTargetModel\(\(currentModel\)\s*=>/u);
    assert.match(effect, /\}, \[targetPdb\]\);/u);
    assert.doesNotMatch(effect, /\}, \[[^\]]*selectedTargetModel/u);
});

test('unmount disposal prevents a pending target parse from committing', async () => {
    const controller = createLatestAsyncResourceController();
    const pending = deferred<string>();
    const commits: string[] = [];
    const token = controller.begin();
    const completion = pending.promise.finally(() => {
        if (controller.isCurrent(token)) commits.push('commit');
    });

    controller.dispose();
    pending.resolve('late');
    await completion;

    assert.deepEqual(commits, []);
    assert.match(componentSource, /targetParseControllerRef\.current\.dispose\(\)/u);
});
