/**
 * ThemeSelector - Dropdown component for switching color schemes
 */

import { useState } from 'react';
import { useTheme, THEMES } from './ThemeProvider';
import type { ThemeId } from './ThemeProvider';

export function ThemeSelector() {
    const { theme, setTheme, themeConfig } = useTheme();
    const [isOpen, setIsOpen] = useState(false);

    const handleSelect = (themeId: ThemeId) => {
        setTheme(themeId);
        setIsOpen(false);
    };

    return (
        <div className="relative">
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
                        className="fixed inset-0 z-40"
                        onClick={() => setIsOpen(false)}
                    />

                    {/* Dropdown */}
                    <div className="absolute right-0 top-full mt-2 w-56 bg-[var(--bg-secondary)] border border-[var(--border-primary)] rounded-xl shadow-2xl z-50 py-2 overflow-hidden">
                        <div className="px-3 py-2 border-b border-[var(--border-primary)]">
                            <p className="text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider">
                                Color Scheme
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
