export type CloseAwareWindow = {
  on: (event: 'close', handler: (event: { preventDefault: () => void }) => void) => void;
  hide: () => void;
};

export type SingleInstanceApp = {
  requestSingleInstanceLock: () => boolean;
  on: (event: 'second-instance', handler: () => void) => void;
  quit: () => void;
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

export function enforceSingleInstanceLock(
  app: SingleInstanceApp,
  showExistingWindow: () => void,
): boolean {
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return false;
  }

  app.on('second-instance', () => {
    showExistingWindow();
  });
  return true;
}
