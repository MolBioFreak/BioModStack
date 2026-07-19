import assert from 'node:assert/strict';
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
