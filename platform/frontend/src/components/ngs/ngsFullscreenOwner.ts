export function isOwnedFullscreen(
    element: HTMLElement | null,
    documentLike: Document = document,
): boolean {
    return element !== null && documentLike.fullscreenElement === element;
}

export async function toggleOwnedFullscreen(
    element: HTMLElement | null,
    documentLike: Document = document,
): Promise<void> {
    if (!element || !documentLike.fullscreenEnabled || typeof element.requestFullscreen !== 'function') {
        throw new Error('Fullscreen is unavailable for this viewer.');
    }
    if (isOwnedFullscreen(element, documentLike)) {
        await documentLike.exitFullscreen();
        return;
    }
    if (documentLike.fullscreenElement) {
        throw new Error('Another surface already owns fullscreen.');
    }
    await element.requestFullscreen();
}
