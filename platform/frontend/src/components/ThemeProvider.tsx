/**
 * ThemeProvider - Global theme context with localStorage persistence
 */

import { useState, useEffect } from 'react';
import type { ReactNode } from 'react';
import { ThemeContext, THEMES, type ThemeId } from './themeContext';

const STORAGE_KEY = 'biomodstack-theme';

export function ThemeProvider({ children }: { children: ReactNode }) {
    const [theme, setThemeState] = useState<ThemeId>(() => {
        // Initialize from localStorage or default to midnight
        if (typeof window !== 'undefined') {
            const stored = localStorage.getItem(STORAGE_KEY);
            if (stored && THEMES.some(t => t.id === stored)) {
                return stored as ThemeId;
            }
        }
        return 'midnight';
    });

    const setTheme = (newTheme: ThemeId) => {
        setThemeState(newTheme);
        localStorage.setItem(STORAGE_KEY, newTheme);
    };

    // Apply theme to document root
    useEffect(() => {
        document.documentElement.setAttribute('data-theme', theme);
    }, [theme]);

    const themeConfig = THEMES.find(t => t.id === theme) || THEMES[0];

    return (
        <ThemeContext.Provider value={{ theme, setTheme, themeConfig }}>
            {children}
        </ThemeContext.Provider>
    );
}
