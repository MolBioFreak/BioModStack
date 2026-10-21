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
const DOUBLE_TAP_INTERVAL_MS = 350;
const DOUBLE_TAP_DISTANCE_PX = 24;
const TAP_MOVEMENT_PX = 10;
const TAP_DURATION_MS = 300;

export function shouldEnableSeqVizTouchBridge(capability: SeqVizTouchCapability): boolean {
    return Boolean((capability.maxTouchPoints ?? 0) > 0 || capability.coarsePointer);
}

export function resolveSeqVizTouchGestureMode(touchCount: number, doubleTap = false): SeqVizTouchGestureMode {
    return touchCount === 1 && doubleTap ? 'synthetic-mouse' : 'native';
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
    const hit = document.elementFromPoint(point.clientX, point.clientY);
    return hit && root.contains(hit) ? hit : fallback ?? root;
}

function dispatchSyntheticMouseEvent(type: 'mousedown' | 'mousemove' | 'mouseup', target: Element, point: PointLike): void {
    target.dispatchEvent(new MouseEvent(type, {
        ...buildSyntheticMouseEventInit(point),
        buttons: type === 'mouseup' ? 0 : 1,
    }));
}

export function installSeqVizTouchBridge(root: HTMLElement): () => void {
    const router = root.querySelector<HTMLElement>('.la-vz-viewer-event-router');
    if (!router) return () => undefined;

    let activeTouchId: number | null = null;
    let activeTarget: Element | null = null;
    let lastPoint: PointLike | null = null;
    let startPoint: PointLike | null = null;
    let startedAt = 0;
    let moved = false;
    let selecting = false;
    let previousTap: { point: PointLike; endedAt: number } | null = null;

    const resetState = (): void => {
        activeTouchId = null;
        activeTarget = null;
        lastPoint = null;
        startPoint = null;
        moved = false;
        selecting = false;
    };

    const shouldIgnoreEvent = (event: TouchEvent): boolean => {
        return event.target instanceof Element
            && event.target.closest(`[${SEQVIZ_TOUCH_CONTROL_ATTRIBUTE}]`) !== null;
    };

    const finishActiveTouch = (point: PointLike | null): void => {
        if (selecting && point) {
            const target = resolveDispatchTarget(root, activeTarget ?? router, point);
            dispatchSyntheticMouseEvent('mouseup', target, point);
        }
        resetState();
    };

    const handleTouchStart = (event: TouchEvent): void => {
        if (shouldIgnoreEvent(event) || event.touches.length !== 1) {
            finishActiveTouch(lastPoint);
            previousTap = null;
            return;
        }

        const touch = event.changedTouches.item(0);
        if (!touch) return;

        const point = { clientX: touch.clientX, clientY: touch.clientY };
        const doubleTap = previousTap !== null
            && event.timeStamp - previousTap.endedAt >= 0
            && event.timeStamp - previousTap.endedAt <= DOUBLE_TAP_INTERVAL_MS
            && Math.hypot(point.clientX - previousTap.point.clientX, point.clientY - previousTap.point.clientY) <= DOUBLE_TAP_DISTANCE_PX;
        previousTap = null;
        activeTouchId = touch.identifier;
        startPoint = lastPoint = point;
        startedAt = event.timeStamp;
        moved = false;
        selecting = resolveSeqVizTouchGestureMode(event.touches.length, doubleTap) === 'synthetic-mouse';
        activeTarget = resolveDispatchTarget(root, router, point);

        // Single-finger panning and momentum belong to the native scroller.
        // Only the second tap starts SeqViz's mouse-based range selection.
        if (selecting) {
            event.preventDefault();
            dispatchSyntheticMouseEvent('mousedown', activeTarget, point);
        }
    };

    const handleTouchMove = (event: TouchEvent): void => {
        if (activeTouchId === null) return;
        if (event.touches.length !== 1) {
            finishActiveTouch(lastPoint);
            previousTap = null;
            return;
        }

        const touch = findTouchByIdentifier(event.changedTouches, activeTouchId)
            ?? findTouchByIdentifier(event.touches, activeTouchId);
        if (!touch) return;

        const point = { clientX: touch.clientX, clientY: touch.clientY };
        lastPoint = point;
        if (startPoint && Math.hypot(point.clientX - startPoint.clientX, point.clientY - startPoint.clientY) > TAP_MOVEMENT_PX) {
            moved = true;
        }
        if (selecting) {
            activeTarget = resolveDispatchTarget(root, activeTarget ?? router, point);
            event.preventDefault();
            dispatchSyntheticMouseEvent('mousemove', activeTarget, point);
        }
    };

    const handleTouchEnd = (event: TouchEvent): void => {
        if (activeTouchId === null) return;
        const touch = findTouchByIdentifier(event.changedTouches, activeTouchId);
        if (!touch) return;
        const point = { clientX: touch.clientX, clientY: touch.clientY };
        if (selecting) {
            event.preventDefault();
        } else if (!moved && startPoint
            && Math.hypot(point.clientX - startPoint.clientX, point.clientY - startPoint.clientY) <= TAP_MOVEMENT_PX
            && event.timeStamp - startedAt <= TAP_DURATION_MS) {
            previousTap = { point, endedAt: event.timeStamp };
        }
        finishActiveTouch(point);
    };

    const cancelGesture = (): void => {
        finishActiveTouch(lastPoint);
        previousTap = null;
    };

    root.addEventListener('touchstart', handleTouchStart, { capture: true, passive: false });
    root.addEventListener('touchmove', handleTouchMove, { capture: true, passive: false });
    root.addEventListener('touchend', handleTouchEnd, { capture: true, passive: false });
    root.addEventListener('touchcancel', cancelGesture, { capture: true, passive: false });
    window.addEventListener('blur', cancelGesture);

    return () => {
        cancelGesture();
        root.removeEventListener('touchstart', handleTouchStart, true);
        root.removeEventListener('touchmove', handleTouchMove, true);
        root.removeEventListener('touchend', handleTouchEnd, true);
        root.removeEventListener('touchcancel', cancelGesture, true);
        window.removeEventListener('blur', cancelGesture);
    };
}
