import type { BpColors, ColorPaletteName, SequenceData, VisibilityState } from './SequenceViewer';

export const COLOR_PALETTES: Record<ColorPaletteName, { name: string; description: string; colors: BpColors }> = {
    classic: {
        name: 'Classic',
        description: 'Traditional 4-color scheme (A=green, T=red, G=amber, C=blue)',
        colors: { A: '#22c55e', T: '#ef4444', G: '#f59e0b', C: '#3b82f6', U: '#ec4899' }
    },
    gc_at: {
        name: 'GC vs AT',
        description: 'Group by base pairing: GC (blue/cyan) vs AT (red/orange)',
        colors: { A: '#ef4444', T: '#f97316', G: '#3b82f6', C: '#06b6d4', U: '#f97316' }
    },
    purine_pyrimidine: {
        name: 'Purine/Pyrimidine',
        description: 'Purines A+G (warm) vs Pyrimidines C+T (cool)',
        colors: { A: '#f97316', T: '#3b82f6', G: '#eab308', C: '#8b5cf6', U: '#8b5cf6' }
    },
    muted: {
        name: 'Muted',
        description: 'Softer colors, easier on eyes for long sessions',
        colors: { A: '#6ee7b7', T: '#fca5a5', G: '#fcd34d', C: '#93c5fd', U: '#f9a8d4' }
    },
    vivid: {
        name: 'Vivid',
        description: 'High saturation for maximum contrast',
        colors: { A: '#00ff00', T: '#ff0000', G: '#ffff00', C: '#0088ff', U: '#ff00ff' }
    },
    monochrome: {
        name: 'Monochrome',
        description: 'Grayscale for printing or colorblind accessibility',
        colors: { A: '#404040', T: '#808080', G: '#c0c0c0', C: '#606060', U: '#909090' }
    },
    colorblind: {
        name: 'Colorblind Safe',
        description: 'Optimized for deuteranopia (red-green colorblindness)',
        colors: { A: '#e69f00', T: '#56b4e9', G: '#cc79a7', C: '#0072b2', U: '#f0e442' }
    },
    rasmol: {
        name: 'RasMol',
        description: 'Traditional molecular visualization colors',
        colors: { A: '#a0a0ff', T: '#ff8c4b', G: '#ff7070', C: '#ffc832', U: '#ff8080' }
    }
};

// ═══════════════════════════════════════════════════════════════════════════════
// DEFAULT STATES
// ═══════════════════════════════════════════════════════════════════════════════

export const EMPTY_SEQUENCE: SequenceData = {
    name: "Untitled Sequence",
    description: "",
    sequence: "",
    circular: false,
    sequenceType: "dna",
    features: [],
    primers: [],
    translations: [],
    analysisTracks: [],
    parentId: null,
    operation: null,
    operationParams: null,
    version: null,
};

export const DEFAULT_VISIBILITY: VisibilityState = {
    features: true,
    primers: true,
    cutsites: false,
    translations: false,
    reverseComplement: true
};
