import assert from 'node:assert/strict';
import test from 'node:test';

import {
    chooseReturnFocusTarget,
    didFocusLeaveContainer,
    focusTrapTarget,
    moveMenuFocus,
    restoreFocusIfConnected,
    restoreFocusWithFallback,
    type FocusTarget,
} from '../src/components/MolBioToolkit/utils/focusManagement.js';

class FakeFocusTarget implements FocusTarget {
    focusCalls = 0;
    lastOptions: { preventScroll?: boolean } | undefined;
    disabled = false;

    focus(options?: { preventScroll?: boolean }): void {
        this.focusCalls += 1;
        this.lastOptions = options;
    }
}

test('Tab and Shift+Tab remain inside the open menu and wrap at its boundaries', () => {
    const first = new FakeFocusTarget();
    const middle = new FakeFocusTarget();
    const last = new FakeFocusTarget();
    const items = [first, middle, last];

    assert.equal(moveMenuFocus(items, last, 'Tab', false), true);
    assert.equal(first.focusCalls, 1);

    assert.equal(moveMenuFocus(items, first, 'Tab', true), true);
    assert.equal(last.focusCalls, 1);
});

test('menu focus departure distinguishes descendants from outside or missing targets', () => {
    const inside = {};
    const outside = {};
    const container = { contains: (target: unknown) => target === inside };

    assert.equal(didFocusLeaveContainer(container, inside), false);
    assert.equal(didFocusLeaveContainer(container, outside), true);
    assert.equal(didFocusLeaveContainer(container, null), true);
});

test('dialog return focus prefers the explicit viewer invoker over a transient menu item', () => {
    const viewerInvoker = new FakeFocusTarget();
    const menuItem = new FakeFocusTarget();

    assert.equal(chooseReturnFocusTarget(viewerInvoker, menuItem), viewerInvoker);
});

test('dialog focus trap wraps only at its first and last controls', () => {
    const first = new FakeFocusTarget();
    const middle = new FakeFocusTarget();
    const last = new FakeFocusTarget();
    const items = [first, middle, last];

    assert.equal(focusTrapTarget(items, first, true), last);
    assert.equal(focusTrapTarget(items, last, false), first);
    assert.equal(focusTrapTarget(items, middle, false), null);
    assert.equal(focusTrapTarget(items, middle, true), null);
    assert.equal(focusTrapTarget(items, new FakeFocusTarget(), false), first);
    assert.equal(focusTrapTarget(items, new FakeFocusTarget(), true), last);
});

test('focus restoration runs only for a still-connected target', () => {
    const connected = new FakeFocusTarget();
    const detached = new FakeFocusTarget();

    assert.equal(restoreFocusIfConnected(connected, (target) => target === connected), true);
    assert.equal(connected.focusCalls, 1);
    assert.deepEqual(connected.lastOptions, { preventScroll: true });

    assert.equal(restoreFocusIfConnected(detached, () => false), false);
    assert.equal(detached.focusCalls, 0);
});

test('focus restoration rejects a disabled invoker and uses the connected fallback', () => {
    const disabledInvoker = new FakeFocusTarget();
    const fallback = new FakeFocusTarget();
    disabledInvoker.disabled = true;

    assert.equal(
        restoreFocusWithFallback(disabledInvoker, fallback, () => true),
        true,
    );
    assert.equal(disabledInvoker.focusCalls, 0);
    assert.equal(fallback.focusCalls, 1);
    assert.deepEqual(fallback.lastOptions, { preventScroll: true });
});
