import assert from 'node:assert/strict';
import test from 'node:test';

import { attachCloseToTrayBehavior, enforceSingleInstanceLock } from '../src/windowLifecycle.js';

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

test('single-instance lock focuses the existing shell when a second launch is attempted', () => {
  let secondInstanceHandler: (() => void) | undefined;
  let focusedExistingWindow = 0;
  let quitCalls = 0;

  const app = {
    requestSingleInstanceLock: () => true,
    on: (event: 'second-instance', handler: () => void) => {
      assert.equal(event, 'second-instance');
      secondInstanceHandler = handler;
    },
    quit: () => {
      quitCalls += 1;
    },
  };

  const acquired = enforceSingleInstanceLock(app, () => {
    focusedExistingWindow += 1;
  });

  assert.equal(acquired, true);
  assert.equal(quitCalls, 0);
  secondInstanceHandler?.();
  assert.equal(focusedExistingWindow, 1);
});

test('single-instance lock quits duplicate Electron processes before bootstrapping another shell', () => {
  let registeredSecondInstanceHandler = false;
  let focusedExistingWindow = 0;
  let quitCalls = 0;

  const app = {
    requestSingleInstanceLock: () => false,
    on: (_event: 'second-instance', _handler: () => void) => {
      registeredSecondInstanceHandler = true;
    },
    quit: () => {
      quitCalls += 1;
    },
  };

  const acquired = enforceSingleInstanceLock(app, () => {
    focusedExistingWindow += 1;
  });

  assert.equal(acquired, false);
  assert.equal(quitCalls, 1);
  assert.equal(registeredSecondInstanceHandler, false);
  assert.equal(focusedExistingWindow, 0);
});
