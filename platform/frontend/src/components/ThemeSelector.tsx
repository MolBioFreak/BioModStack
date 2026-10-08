/**
 * ThemeSelector - Dropdown component for switching color schemes
 */

import { useState } from 'react';
import { useTheme, THEMES } from './themeContext';
import type { ThemeId } from './themeContext';

export function ThemeSelector() {
    const { theme, setTheme, themeConfig } = useTheme();
    const [isOpen, setIsOpen] = useState(false);

    const handleSelect = (themeId: ThemeId) => {
        setTheme(themeId);
        setIsOpen(false);
    };

    return (
        <div className="relative shrink-0">
            {/* Trigger Button */}
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-all border border-[var(--border-primary)] bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]"
                title="Change color scheme"
            >
                {/* Color swatch preview */}
                <div className="flex items-center gap-0.5">
                    <div
                        className="w-3 h-3 rounded-sm"
                        style={{ backgroundColor: themeConfig.preview.bg, border: '1px solid var(--border-primary)' }}
                    />
                    <div
                        className="w-3 h-3 rounded-sm"
                        style={{ backgroundColor: themeConfig.preview.accent }}
                    />
                </div>
                <span className="hidden sm:inline">{themeConfig.name}</span>
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
            </button>

            {isOpen && (
                <>
                    {/* Backdrop */}
                    <div
                        data-bms-drag-scroll-ignore="true"
                        className="fixed inset-0 z-40"
                        onClick={() => setIsOpen(false)}
                    />

                    {/* Dropdown */}
                    <div
                        className="absolute right-0 top-full mt-2 w-56 max-w-[calc(100vw-1rem)] bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-xl shadow-2xl z-50 py-2 overflow-hidden"
                        data-bms-drag-scroll-ignore="true"
                    >
                        <div className="px-3 py-2 border-b border-[var(--border-primary)]">
                            <p className="text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider mb-2">
                                Quick Mode
                            </p>
                            <div className="flex gap-2">
                                <button
                                    onClick={() => handleSelect('clean_light')}
                                    className={`flex-1 px-3 py-1.5 text-xs font-medium rounded-lg transition-all flex items-center justify-center gap-1.5 ${theme === 'clean_light' || theme === 'light'
                                        ? 'bg-[var(--accent-primary)]/20 ring-1 ring-[var(--accent-primary)]/50 text-[var(--accent-primary)]'
                                        : 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                                        }`}
                                >
                                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                                        <path fillRule="evenodd" d="M10 2a1 1 0 011 1v1a1 1 0 11-2 0V3a1 1 0 011-1zm4 8a4 4 0 11-8 0 4 4 0 018 0zm-.464 4.95l.707.707a1 1 0 001.414-1.414l-.707-.707a1 1 0 00-1.414 1.414zm2.12-10.607a1 1 0 010 1.414l-.706.707a1 1 0 11-1.414-1.414l.707-.707a1 1 0 011.414 0zM17 11a1 1 0 100-2h-1a1 1 0 100 2h1zm-7 4a1 1 0 011 1v1a1 1 0 11-2 0v-1a1 1 0 011-1zM5.05 6.464A1 1 0 106.465 5.05l-.708-.707a1 1 0 00-1.414 1.414l.707.707zm1.414 8.486l-.707.707a1 1 0 01-1.414-1.414l.707-.707a1 1 0 011.414 1.414zM4 11a1 1 0 100-2H3a1 1 0 000 2h1z" clipRule="evenodd" />
                                    </svg>
                                    Light
                                </button>
                                <button
                                    onClick={() => handleSelect('black')}
                                    className={`flex-1 px-3 py-1.5 text-xs font-medium rounded-lg transition-all flex items-center justify-center gap-1.5 ${theme === 'black'
                                        ? 'bg-[var(--accent-primary)]/20 ring-1 ring-[var(--accent-primary)]/50 text-[var(--accent-primary)]'
                                        : 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                                        }`}
                                >
                                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                                        <path d="M17.293 13.293A8 8 0 016.707 2.707a8.001 8.001 0 1010.586 10.586z" />
                                    </svg>
                                    Dark
                                </button>
                            </div>
                        </div>
                        <div className="px-3 py-2 border-b border-[var(--border-primary)]">
                            <p className="text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider">
                                All Themes
                            </p>
                        </div>

                        <div className="p-1.5 space-y-0.5 max-h-80 overflow-y-auto">
                            {THEMES.map((t) => (
                                <button
                                    key={t.id}
                                    onClick={() => handleSelect(t.id)}
                                    className={`w-full px-3 py-2.5 text-left rounded-lg transition-all flex items-center gap-3 ${theme === t.id
                                        ? 'bg-[var(--accent-primary)]/20 ring-1 ring-[var(--accent-primary)]/50'
                                        : 'hover:bg-[var(--bg-tertiary)]'
                                        }`}
                                >
                                    {/* Theme color preview */}
                                    <div className="flex items-center gap-0.5 shrink-0">
                                        <div
                                            className="w-5 h-5 rounded"
                                            style={{
                                                backgroundColor: t.preview.bg,
                                                border: '1px solid rgba(255,255,255,0.1)'
                                            }}
                                        />
                                        <div
                                            className="w-5 h-5 rounded"
                                            style={{ backgroundColor: t.preview.accent }}
                                        />
                                        <div
                                            className="w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold"
                                            style={{
                                                backgroundColor: t.preview.bg,
                                                color: t.preview.text,
                                                border: '1px solid rgba(255,255,255,0.1)'
                                            }}
                                        >
                                            Aa
                                        </div>
                                    </div>

                                    <div className="min-w-0">
                                        <p className={`text-sm font-medium truncate ${theme === t.id
                                            ? 'text-[var(--accent-primary)]'
                                            : 'text-[var(--text-primary)]'
                                            }`}>
                                            {t.name}
                                        </p>
                                        <p className="text-xs text-[var(--text-muted)] truncate">
                                            {t.description}
                                        </p>
                                    </div>

                                    {/* Checkmark for selected */}
                                    {theme === t.id && (
                                        <svg className="w-4 h-4 text-[var(--accent-primary)] ml-auto shrink-0" fill="currentColor" viewBox="0 0 20 20">
                                            <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                                        </svg>
                                    )}
                                </button>
                            ))}
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}
