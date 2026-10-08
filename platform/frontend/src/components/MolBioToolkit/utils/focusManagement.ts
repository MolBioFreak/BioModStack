export interface FocusTarget {
    focus(options?: { preventScroll?: boolean }): void;
}

export type MenuNavigationKey = 'ArrowDown' | 'ArrowUp' | 'Home' | 'End' | 'Tab';

export function moveMenuFocus(
    items: readonly FocusTarget[],
    activeItem: FocusTarget | null,
    key: MenuNavigationKey,
    shiftKey = false,
): boolean {
    if (items.length === 0) {
        return false;
    }
    const currentIndex = items.indexOf(activeItem as FocusTarget);
    const nextIndex = key === 'Home'
        ? 0
        : key === 'End'
            ? items.length - 1
            : key === 'ArrowUp' || (key === 'Tab' && shiftKey)
                ? (currentIndex <= 0 ? items.length - 1 : currentIndex - 1)
                : (currentIndex + 1) % items.length;
    items[nextIndex].focus();
    return true;
}

export function didFocusLeaveContainer(
    container: { contains(target: unknown): boolean },
    nextTarget: unknown,
): boolean {
    return nextTarget === null || !container.contains(nextTarget);
}

export function chooseReturnFocusTarget<T extends FocusTarget>(
    explicitTarget: T | null | undefined,
    activeTarget: T | null | undefined,
): T | null {
    return explicitTarget ?? activeTarget ?? null;
}

export function focusTrapTarget<T extends FocusTarget>(
    items: readonly T[],
    activeItem: T | null | undefined,
    shiftKey: boolean,
): T | null {
    if (items.length === 0) return null;
    const first = items[0];
    const last = items[items.length - 1];
    if (shiftKey && activeItem === first) return last;
    if (!shiftKey && activeItem === last) return first;
    return null;
}

export function restoreFocusIfConnected<T extends FocusTarget>(
    target: T | null | undefined,
    contains: (target: T) => boolean,
): boolean {
    if (!target || !contains(target)) {
        return false;
    }
    target.focus({ preventScroll: true });
    return true;
}
