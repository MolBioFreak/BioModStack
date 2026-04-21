export type CloseAwareWindow = {
  on: (event: 'close', handler: (event: { preventDefault: () => void }) => void) => void;
  hide: () => void;
};

export function attachCloseToTrayBehavior(
  window: CloseAwareWindow,
  isExplicitQuit: () => boolean,
): void {
  window.on('close', (event) => {
    if (isExplicitQuit()) {
      return;
    }
    event.preventDefault();
    window.hide();
  });
}
