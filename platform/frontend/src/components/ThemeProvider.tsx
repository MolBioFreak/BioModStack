/**
 * ThemeProvider - Global theme context with localStorage persistence
 */

import { createContext, useContext, useState, useEffect } from 'react';
import type { ReactNode } from 'react';

export type ThemeId = 'midnight' | 'light' | 'clean_light' | 'slate_dark' | 'desert' | 'solarized' | 'nord' | 'dracula' | 'forest' | 'ember' | 'retro' | 'cyberpunk' | 'ocean';

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
        id: 'clean_light',
        name: 'Clean Light',
        description: 'Bright white with blue accents',
        preview: { bg: '#fafafa', accent: '#3b82f6', text: '#1e293b' }
    },
    {
        id: 'slate_dark',
        name: 'Slate Dark',
        description: 'Dark gray with teal accents',
        preview: { bg: '#1e293b', accent: '#14b8a6', text: '#f1f5f9' }
    },
    {
        id: 'desert',
        name: 'Desert',
        description: 'Warm sandy dunes',
        preview: { bg: '#3b3022', accent: '#f0a030', text: '#fff8e7' }
    },
    {
        id: 'solarized',
        name: 'Solarized',
        description: 'Classic developer scheme',
        preview: { bg: '#002b36', accent: '#268bd2', text: '#fdf6e3' }
    },
    {
        id: 'nord',
        name: 'Nord',
        description: 'Arctic blue-gray',
        preview: { bg: '#2e3440', accent: '#88c0d0', text: '#eceff4' }
    },
    {
        id: 'dracula',
        name: 'Dracula',
        description: 'Gothic purple & pink',
        preview: { bg: '#282a36', accent: '#bd93f9', text: '#f8f8f2' }
    },
    {
        id: 'forest',
        name: 'Forest',
        description: 'Deep woodland greens',
        preview: { bg: '#1a2418', accent: '#4caf50', text: '#e8f4e5' }
    },
    {
        id: 'ember',
        name: 'Ember',
        description: 'Warm sunset oranges',
        preview: { bg: '#1f1410', accent: '#ff6b35', text: '#fff4e8' }
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
