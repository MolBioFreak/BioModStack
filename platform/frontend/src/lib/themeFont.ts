/** Fonts belong to the global theme (index.css). Canvas, Plotly and iframe documents
 *  cannot inherit CSS, so they read the theme's value here instead of naming a font. */
export function themeFont(kind: 'sans' | 'mono'): string {
    if (kind === 'mono') {
        return getComputedStyle(document.documentElement).getPropertyValue('--font-mono').trim() || 'monospace';
    }
    return getComputedStyle(document.body).fontFamily || 'sans-serif';
}

export function themeFontDeclaration(kind: 'sans' | 'mono'): string {
    return `font-family:${themeFont(kind)}`;
}
