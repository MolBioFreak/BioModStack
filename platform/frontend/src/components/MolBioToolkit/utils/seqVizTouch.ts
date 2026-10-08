export interface SeqVizTouchCapability {
    maxTouchPoints?: number | null;
    coarsePointer?: boolean | null;
}

export type SeqVizTouchGestureMode = 'synthetic-mouse' | 'native';

export interface PointLike {
    clientX: number;
    clientY: number;
}

export const SEQVIZ_TOUCH_CONTROL_ATTRIBUTE = 'data-seqviz-touch-control';
const DEFAULT_ROTATION_WHEEL_STEP = 240;

export function shouldEnableSeqVizTouchBridge(capability: SeqVizTouchCapability): boolean {
    return Boolean((capability.maxTouchPoints ?? 0) > 0 || capability.coarsePointer);
}

export function resolveSeqVizTouchGestureMode(touchCount: number): SeqVizTouchGestureMode {
    return touchCount === 1 ? 'synthetic-mouse' : 'native';
}

export function buildSyntheticMouseEventInit(point: PointLike): MouseEventInit & { button: number; buttons: number } {
    return {
        bubbles: true,
        cancelable: true,
        composed: true,
        button: 0,
        buttons: 1,
        clientX: point.clientX,
        clientY: point.clientY,
    };
}

export function getSeqVizTouchRotationWheelDelta(direction: 'left' | 'right', step = DEFAULT_ROTATION_WHEEL_STEP): number {
    const magnitude = Math.abs(step);
    return direction === 'left' ? -magnitude : magnitude;
}

function findTouchByIdentifier(list: TouchList, identifier: number): Touch | null {
    for (let index = 0; index < list.length; index += 1) {
        const touch = list.item(index);
        if (touch && touch.identifier === identifier) {
            return touch;
        }
    }
    return null;
}

function resolveDispatchTarget(root: HTMLElement, fallback: Element | null, point: PointLike): Element {
    return document.elementFromPoint(point.clientX, point.clientY) ?? fallback ?? root;
}

function dispatchSyntheticMouseEvent(type: 'mousedown' | 'mousemove' | 'mouseup', target: Element, point: PointLike): void {
    target.dispatchEvent(new MouseEvent(type, buildSyntheticMouseEventInit(point)));
}

export function installSeqVizTouchBridge(root: HTMLElement): () => void {
    const router = root.querySelector<HTMLElement>('.la-vz-viewer-event-router');
    if (!router) {
        return () => undefined;
    }

    let activeTouchId: number | null = null;
    let activeTarget: Element | null = null;
    let lastPoint: PointLike | null = null;

    const resetState = (): void => {
        activeTouchId = null;
        activeTarget = null;
        lastPoint = null;
    };

    const shouldIgnoreEvent = (event: TouchEvent): boolean => {
        return event.target instanceof Element
            && event.target.closest(`[${SEQVIZ_TOUCH_CONTROL_ATTRIBUTE}]`) !== null;
    };

    const finishActiveTouch = (point: PointLike | null): void => {
        if (activeTouchId === null || !point) {
            resetState();
            return;
        }

        const target = resolveDispatchTarget(root, activeTarget ?? router, point);
        dispatchSyntheticMouseEvent('mouseup', target, point);
        resetState();
    };

    const handleTouchStart = (event: TouchEvent): void => {
        if (shouldIgnoreEvent(event)) {
            return;
        }

        if (resolveSeqVizTouchGestureMode(event.touches.length) === 'native') {
            finishActiveTouch(lastPoint);
            return;
        }

        const touch = event.changedTouches.item(0);
        if (!touch) {
            return;
        }

        const point = { clientX: touch.clientX, clientY: touch.clientY };
        activeTouchId = touch.identifier;
        lastPoint = point;
        activeTarget = resolveDispatchTarget(root, router, point);

        event.preventDefault();
        dispatchSyntheticMouseEvent('mousedown', activeTarget, point);
    };

    const handleTouchMove = (event: TouchEvent): void => {
        if (activeTouchId === null || shouldIgnoreEvent(event)) {
            return;
        }

        if (resolveSeqVizTouchGestureMode(event.touches.length) === 'native') {
            finishActiveTouch(lastPoint);
            return;
        }

        const touch = findTouchByIdentifier(event.changedTouches, activeTouchId)
            ?? findTouchByIdentifier(event.touches, activeTouchId);
        if (!touch) {
            return;
        }

        const point = { clientX: touch.clientX, clientY: touch.clientY };
        lastPoint = point;
        activeTarget = resolveDispatchTarget(root, activeTarget ?? router, point);

        event.preventDefault();
        dispatchSyntheticMouseEvent('mousemove', activeTarget, point);
    };

    const handleTouchEnd = (event: TouchEvent): void => {
        if (activeTouchId === null || shouldIgnoreEvent(event)) {
            return;
        }

        const touch = findTouchByIdentifier(event.changedTouches, activeTouchId);
        const point = touch
            ? { clientX: touch.clientX, clientY: touch.clientY }
            : lastPoint;

        if (!point) {
            resetState();
            return;
        }

        event.preventDefault();
        finishActiveTouch(point);
    };

    const handleTouchCancel = (): void => {
        finishActiveTouch(lastPoint);
    };

    root.addEventListener('touchstart', handleTouchStart, { capture: true, passive: false });
    root.addEventListener('touchmove', handleTouchMove, { capture: true, passive: false });
    root.addEventListener('touchend', handleTouchEnd, { capture: true, passive: false });
    root.addEventListener('touchcancel', handleTouchCancel, { capture: true, passive: false });

    return () => {
        root.removeEventListener('touchstart', handleTouchStart, true);
        root.removeEventListener('touchmove', handleTouchMove, true);
        root.removeEventListener('touchend', handleTouchEnd, true);
        root.removeEventListener('touchcancel', handleTouchCancel, true);
    };
}
