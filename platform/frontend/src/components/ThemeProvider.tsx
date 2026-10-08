/**
 * ThemeProvider - Global theme context with localStorage persistence
 */

import { createContext, useContext, useState, useEffect } from 'react';
import type { ReactNode } from 'react';

export type ThemeId = 'midnight' | 'light' | 'desert' | 'retro' | 'cyberpunk' | 'ocean';

export interface ThemeConfig {
    id: ThemeId;
    name: string;
    description: string;
    preview: {
        bg: string;
        accent: string;
        text: string;
    };
}

export const THEMES: ThemeConfig[] = [
    {
        id: 'midnight',
        name: 'Midnight',
        description: 'Deep slate with purple accents',
        preview: { bg: '#0f172a', accent: '#a855f7', text: '#f1f5f9' }
    },
    {
        id: 'light',
        name: 'Light',
        description: 'Clean white with violet accents',
        preview: { bg: '#ffffff', accent: '#7c3aed', text: '#0f172a' }
    },
    {
        id: 'desert',
        name: 'Desert Sand',
        description: 'Warm sandy earth tones',
        preview: { bg: '#2a1f1a', accent: '#e8a849', text: '#f5e6d3' }
    },
    {
        id: 'retro',
        name: 'Retro Terminal',
        description: 'Classic green phosphor',
        preview: { bg: '#0a0a0a', accent: '#22c55e', text: '#4ade80' }
    },
    {
        id: 'cyberpunk',
        name: 'Cyberpunk',
        description: 'Neon pink & cyan',
        preview: { bg: '#1a0a2e', accent: '#f0abfc', text: '#22d3ee' }
    },
    {
        id: 'ocean',
        name: 'Ocean Depths',
        description: 'Deep blues with teal',
        preview: { bg: '#0c1929', accent: '#14b8a6', text: '#e0f2fe' }
    }
];

interface ThemeContextValue {
    theme: ThemeId;
    setTheme: (theme: ThemeId) => void;
    themeConfig: ThemeConfig;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

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

export function useTheme() {
    const context = useContext(ThemeContext);
    if (!context) {
        throw new Error('useTheme must be used within a ThemeProvider');
    }
    return context;
}
