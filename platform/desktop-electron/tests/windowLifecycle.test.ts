import assert from 'node:assert/strict';
import test from 'node:test';

import { attachCloseToTrayBehavior } from '../src/windowLifecycle.js';

type CloseEvent = { preventDefault: () => void };
type CloseHandler = (event: CloseEvent) => void;
type TestWindow = {
  on: (event: 'close', handler: CloseHandler) => void;
  hide: () => void;
};

test('window close hides the shell instead of destroying in-memory state unless the user explicitly quits', () => {
  let closeHandler: CloseHandler | undefined;
  let hidden = 0;
  let prevented = 0;

  const window: TestWindow = {
    on: (_event: 'close', handler: CloseHandler) => {
      closeHandler = handler;
    },
    hide: () => {
      hidden += 1;
    },
  };

  attachCloseToTrayBehavior(window, () => false);

  closeHandler?.({
    preventDefault: () => {
      prevented += 1;
    },
  });

  assert.equal(prevented, 1);
  assert.equal(hidden, 1);
});

test('window close is allowed through when the shell is explicitly quitting', () => {
  let closeHandler: CloseHandler | undefined;
  let hidden = 0;
  let prevented = 0;

  const window: TestWindow = {
    on: (_event: 'close', handler: CloseHandler) => {
      closeHandler = handler;
    },
    hide: () => {
      hidden += 1;
    },
  };

  attachCloseToTrayBehavior(window, () => true);

  closeHandler?.({
    preventDefault: () => {
      prevented += 1;
    },
  });

  assert.equal(prevented, 0);
  assert.equal(hidden, 0);
});
