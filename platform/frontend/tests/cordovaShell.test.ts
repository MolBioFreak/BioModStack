import assert from 'node:assert/strict';
import test from 'node:test';

import { signalCordovaAppReady } from '../src/runtime/cordovaShell.js';

test('signalCordovaAppReady calls the shell readiness hook exactly once when present', () => {
    let callCount = 0;
    const target = {
        __BMS_CORDOVA_CONFIRM_READY__: () => {
            callCount += 1;
        },
    };

    signalCordovaAppReady(target);
    signalCordovaAppReady(target);

    assert.equal(callCount, 1);
});

test('signalCordovaAppReady quietly skips hosts without the Cordova shell hook', () => {
    assert.doesNotThrow(() => signalCordovaAppReady({}));
});
