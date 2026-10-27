import { createContext, useContext } from 'react';

export type ThemeId = 'midnight' | 'black' | 'light' | 'clean_light' | 'slate_dark' | 'desert' | 'solarized' | 'nord' | 'dracula' | 'forest' | 'ember' | 'retro' | 'cyberpunk' | 'ocean';

export interface ThemeConfig {
    id: ThemeId;
    name: string;
    description: string;
}

export const THEMES: ThemeConfig[] = [
    {
        id: 'midnight',
        name: 'Midnight',
        description: 'Ink navy with blue-violet accents'
    },
    {
        id: 'black',
        name: 'Black',
        description: 'True black with neutral grays'
    },
    {
        id: 'light',
        name: 'Light',
        description: 'Clean white with violet accents'
    },
    {
        id: 'clean_light',
        name: 'Clean Light',
        description: 'Bright white with blue accents'
    },
    {
        id: 'slate_dark',
        name: 'Slate Dark',
        description: 'Dark gray with teal accents'
    },
    {
        id: 'desert',
        name: 'Desert',
        description: 'Warm adobe parchment with clay and mineral accents'
    },
    {
        id: 'solarized',
        name: 'Solarized',
        description: 'Classic developer scheme'
    },
    {
        id: 'nord',
        name: 'Nord',
        description: 'Arctic blue-gray'
    },
    {
        id: 'dracula',
        name: 'Dracula',
        description: 'Gothic purple & pink'
    },
    {
        id: 'forest',
        name: 'Forest',
        description: 'Deep woodland greens'
    },
    {
        id: 'ember',
        name: 'Ember',
        description: 'Warm sunset oranges'
    },
    {
        id: 'retro',
        name: 'Retro Terminal',
        description: 'CRT workstation with phosphor mint and amber'
    },
    {
        id: 'cyberpunk',
        name: 'Cyberpunk',
        description: 'Neon pink & cyan'
    },
    {
        id: 'ocean',
        name: 'Ocean Depths',
        description: 'Deep blues with teal'
    }
];

export interface ThemeContextValue {
    theme: ThemeId;
    setTheme: (theme: ThemeId) => void;
    themeConfig: ThemeConfig;
}

export const ThemeContext = createContext<ThemeContextValue | null>(null);

export function useTheme() {
    const context = useContext(ThemeContext);
    if (!context) {
        throw new Error('useTheme must be used within a ThemeProvider');
    }
    return context;
}
