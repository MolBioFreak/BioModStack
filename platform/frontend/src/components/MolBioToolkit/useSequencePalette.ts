import { useEffect, useMemo, useState } from 'react';
import { readableTextColor } from '../../lib/readableColors';
import { COLOR_PALETTES } from './sequenceViewerConstants';
import type { ColorPaletteName } from './SequenceViewer';

function surface() {
    return typeof document === 'undefined' ? '#1e293b' : getComputedStyle(document.documentElement).getPropertyValue('--bg-secondary').trim() || '#1e293b';
}
export function useSequencePalette(palette: ColorPaletteName) {
    const [background, setBackground] = useState(surface);
    useEffect(() => {
        const refresh = () => setBackground(surface());
        const observer = new MutationObserver(refresh);
        observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style'] });
        refresh();
        return () => observer.disconnect();
    }, []);
    // Leave enough contrast for the lightly tinted selection background too.
    // Palette choice remains unchanged; these are display colors only.
    return useMemo(() => Object.fromEntries(Object.entries(COLOR_PALETTES[palette].colors).map(([base, color]) => [base, color ? readableTextColor(color, background, 6.5) : color])), [palette, background]);
}
