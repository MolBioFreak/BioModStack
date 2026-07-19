import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { createLatestAsyncResourceController } from '../src/lib/latestAsyncResource.js';

test('latest async resource controller rejects stale and disposed completions', () => {
    const controller = createLatestAsyncResourceController();
    const first = controller.begin();
    const second = controller.begin();

    assert.equal(controller.isCurrent(first), false);
    assert.equal(controller.isCurrent(second), true);

    controller.dispose();
    assert.equal(controller.isCurrent(second), false);
});

test('antibody target parsing invalidates stale success, error, and settled callbacks', () => {
    const controller = createLatestAsyncResourceController();
    const older = controller.begin();
    const newer = controller.begin();

    assert.equal(controller.isCurrent(older), false);
    assert.equal(controller.isCurrent(newer), true);

    const source = fs.readFileSync(
        path.join(process.cwd(), 'src', 'components', 'AntibodyDenovoTemplate.tsx'),
        'utf8',
    );
    const parseEffect = source.slice(
        source.indexOf('// Parse the uploaded/selected target structure'),
        source.indexOf('// Keep the active target chains/viewer content aligned'),
    );

    assert.match(parseEffect, /targetParseControllerRef\.current\.begin\(\)/);
    assert.equal(
        (parseEffect.match(/targetParseControllerRef\.current\.isCurrent\(parseToken\)/g) || []).length,
        3,
    );
    assert.match(source, /targetParseControllerRef\.current\.dispose\(\)/);
});
