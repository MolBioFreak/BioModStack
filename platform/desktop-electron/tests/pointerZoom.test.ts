import assert from 'node:assert/strict';
import test from 'node:test';

import { resolvePointerZoomStep } from '../src/pointerZoom.js';

test('resolvePointerZoomStep only zooms on ctrl/meta wheel gestures and maps wheel direction onto shell zoom steps', () => {
  assert.equal(resolvePointerZoomStep({ ctrlKey: true, metaKey: false, deltaY: -32 }), 1);
  assert.equal(resolvePointerZoomStep({ ctrlKey: false, metaKey: true, deltaY: -1 }), 1);
  assert.equal(resolvePointerZoomStep({ ctrlKey: true, metaKey: false, deltaY: 18 }), -1);
  assert.equal(resolvePointerZoomStep({ ctrlKey: false, metaKey: true, deltaY: 4 }), -1);
  assert.equal(resolvePointerZoomStep({ ctrlKey: false, metaKey: false, deltaY: -5 }), null);
  assert.equal(resolvePointerZoomStep({ ctrlKey: true, metaKey: false, deltaY: 0 }), null);
});
