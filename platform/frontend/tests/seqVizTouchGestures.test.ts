import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { installSeqVizTouchBridge } from '../src/components/MolBioToolkit/utils/seqVizTouch.js';

function fixture() {
    const dom = new JSDOM('<div id="root"><div class="la-vz-viewer-event-router"><span id="bases">ACGT</span></div><button data-seqviz-touch-control="true">Rotate</button></div><aside id="outside"></aside>');
    const previous = new Map(['window', 'document', 'Element', 'MouseEvent'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    for (const key of previous.keys()) Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key as keyof typeof dom.window] });
    const root = dom.window.document.querySelector<HTMLElement>('#root')!;
    const bases = root.querySelector<HTMLElement>('#bases')!;
    Object.defineProperty(dom.window.document, 'elementFromPoint', { configurable: true, value: () => bases });
    const mouse: Array<{ type: string; x: number; y: number; buttons: number }> = [];
    for (const type of ['mousedown', 'mousemove', 'mouseup']) root.addEventListener(type, event => {
        const e = event as MouseEvent;
        mouse.push({ type, x: e.clientX, y: e.clientY, buttons: e.buttons });
    });
    const dispose = installSeqVizTouchBridge(root);
    function touch(type: string, time: number, x = 40, y = 60, count = type === 'touchend' || type === 'touchcancel' ? 0 : 1, target: Element = bases) {
        const point = { identifier: 1, clientX: x, clientY: y };
        const list = (items: unknown[]) => Object.assign(items, { item: (index: number) => items[index] ?? null });
        const event = new dom.window.Event(type, { bubbles: true, cancelable: true });
        Object.defineProperties(event, {
            timeStamp: { value: time },
            changedTouches: { value: list([point]) },
            touches: { value: list(Array.from({ length: count }, (_, i) => ({ ...point, identifier: i + 1 }))) },
        });
        target.dispatchEvent(event);
        return event.defaultPrevented;
    }
    function tap(time = 0) { touch('touchstart', time); touch('touchend', time + 40); }
    function cleanup() {
        dispose();
        for (const [key, descriptor] of previous) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else Reflect.deleteProperty(globalThis, key);
        }
        dom.window.close();
    }
    return { dom, root, mouse, touch, tap, dispose, cleanup };
}

test('single drag leaves scrolling native and never starts mouse selection', () => {
    const f = fixture();
    try {
        assert.equal(f.touch('touchstart', 0), false);
        assert.equal(f.touch('touchmove', 50, 40, 20), false);
        assert.equal(f.touch('touchend', 100, 40, 20), false);
        assert.deepEqual(f.mouse, []);
        assert.equal(f.touch('touchstart', 150, 40, 20), false, 'a swipe must not arm double-tap selection');
    } finally { f.cleanup(); }
});

test('tap then second-tap-and-drag selects with exact coordinates and releases the mouse', () => {
    const f = fixture();
    try {
        f.tap();
        assert.equal(f.touch('touchstart', 140), true);
        assert.equal(f.touch('touchmove', 180, 140, 160), true);
        assert.equal(f.touch('touchend', 220, 140, 160), true);
        assert.deepEqual(f.mouse, [
            { type: 'mousedown', x: 40, y: 60, buttons: 1 },
            { type: 'mousemove', x: 140, y: 160, buttons: 1 },
            { type: 'mouseup', x: 140, y: 160, buttons: 0 },
        ]);
        assert.equal(f.touch('touchstart', 250, 140, 160), false, 'selection must not arm the next drag');
    } finally { f.cleanup(); }
});

test('late taps, far taps and long presses do not arm range selection', () => {
    for (const scenario of ['late', 'far', 'hold']) {
        const f = fixture();
        try {
            if (scenario === 'hold') { f.touch('touchstart', 0); f.touch('touchend', 700); }
            else f.tap();
            assert.equal(f.touch('touchstart', scenario === 'late' ? 500 : scenario === 'hold' ? 800 : 140, scenario === 'far' ? 150 : 40), false);
            assert.deepEqual(f.mouse, []);
        } finally { f.cleanup(); }
    }
});

test('multi-touch ends selection and hands the gesture back to native pinch handling', () => {
    const f = fixture();
    try {
        f.tap(); f.touch('touchstart', 140);
        assert.equal(f.touch('touchstart', 180, 40, 60, 2), false);
        assert.deepEqual(f.mouse.map(e => e.type), ['mousedown', 'mouseup']);
        assert.equal(f.touch('touchmove', 200, 70, 70, 2), false);
        f.touch('touchend', 230);
        assert.equal(f.touch('touchstart', 250), false);
    } finally { f.cleanup(); }
});

test('cancellation, focus loss and unmount cannot leave an active selection or armed tap', () => {
    for (const ending of ['touchcancel', 'blur', 'unmount']) {
        const f = fixture();
        try {
            f.tap(); f.touch('touchstart', 140);
            if (ending === 'touchcancel') f.touch('touchcancel', 180);
            else if (ending === 'blur') f.dom.window.dispatchEvent(new f.dom.window.Event('blur'));
            else f.dispose();
            assert.deepEqual(f.mouse.map(e => e.type), ['mousedown', 'mouseup']);
            assert.equal(f.touch('touchstart', 220), false);
        } finally { f.cleanup(); }
    }
});

test('rotation controls remain native and break the double-tap chain', () => {
    const f = fixture();
    try {
        f.tap();
        const control = f.root.querySelector('button')!;
        assert.equal(f.touch('touchstart', 100, 40, 60, 1, control), false);
        assert.equal(f.touch('touchend', 120, 40, 60, 0, control), false);
        assert.equal(f.touch('touchstart', 150), false);
        assert.deepEqual(f.mouse, []);
    } finally { f.cleanup(); }
});

test('selection release outside the viewer stays with the viewer owner', () => {
    const f = fixture();
    try {
        f.tap(); f.touch('touchstart', 140);
        Object.defineProperty(f.dom.window.document, 'elementFromPoint', { value: () => f.dom.window.document.querySelector('#outside') });
        f.touch('touchend', 180, 400, 600);
        assert.equal(f.mouse.at(-1)?.type, 'mouseup');
    } finally { f.cleanup(); }
});
