import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { useMolBioBodyScrollLock } from '../../src/components/MolBioToolkit/useMolBioBodyScrollLock';

type State = { fullscreen: boolean; mobile: boolean };
function Harness(props: State) {
  useMolBioBodyScrollLock(props.fullscreen, props.mobile);
  return null;
}

describe('useMolBioBodyScrollLock', () => {
  let root: Root | undefined;
  let host: HTMLDivElement | undefined;

  afterEach(async () => {
    if (root) await act(async () => root?.unmount());
    host?.remove();
    root = undefined;
    host = undefined;
    document.body.style.overflow = '';
  });

  async function render(state: State) {
    if (!root) {
      host = document.createElement('div');
      document.body.appendChild(host);
      root = createRoot(host);
    }
    await act(async () => root?.render(<Harness {...state} />));
  }

  it('keeps one lock through fullscreen to mobile transitions', async () => {
    document.body.style.overflow = 'auto';
    await render({ fullscreen: false, mobile: false });
    expect(document.body.style.overflow).toBe('auto');
    await render({ fullscreen: true, mobile: false });
    expect(document.body.style.overflow).toBe('hidden');
    await render({ fullscreen: true, mobile: true });
    expect(document.body.style.overflow).toBe('hidden');
    await render({ fullscreen: false, mobile: true });
    expect(document.body.style.overflow).toBe('hidden');
    await render({ fullscreen: false, mobile: false });
    expect(document.body.style.overflow).toBe('auto');
  });

  it('restores the prior body overflow when its owner unmounts', async () => {
    document.body.style.overflow = 'scroll';
    await render({ fullscreen: true, mobile: false });
    expect(document.body.style.overflow).toBe('hidden');
    await act(async () => root?.unmount());
    root = undefined;
    expect(document.body.style.overflow).toBe('scroll');
  });
});
