import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it } from 'vitest';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import { ThemeSelector } from '../../src/components/ThemeSelector';
import { THEMES } from '../../src/components/themeContext';

describe('mounted color scheme selector', () => {
    it('applies and persists each scheme through the real selector and previews its CSS palette', async () => {
        const container = document.createElement('div'); document.body.appendChild(container);
        const root = createRoot(container);
        localStorage.clear();
        try {
            await act(async () => root.render(<ThemeProvider><ThemeSelector /></ThemeProvider>));
            for (const theme of THEMES) {
                await act(async () => container.querySelector<HTMLButtonElement>('button[aria-label="Color scheme"]')!.click());
                const menu = container.querySelector('#color-scheme-options')!;
                const choice = menu.querySelector<HTMLButtonElement>(`button[aria-label="${theme.name} color scheme"]`)!;
                const swatch = choice.querySelector(`[data-theme="${theme.id}"]`)!;
                expect(swatch.querySelector<HTMLElement>('div')!.style.backgroundColor).toBe('var(--bg-primary)');
                await act(async () => choice.click());
                expect(document.documentElement.dataset.theme).toBe(theme.id);
                expect(localStorage.getItem('biomodstack-theme')).toBe(theme.id);
                expect(container.querySelector('#color-scheme-options')).toBeNull();
                expect(container.querySelector('button[aria-label="Color scheme"]')!.getAttribute('aria-expanded')).toBe('false');
            }
        } finally {
            act(() => root.unmount()); container.remove(); localStorage.clear(); document.documentElement.removeAttribute('data-theme');
        }
    });
});
