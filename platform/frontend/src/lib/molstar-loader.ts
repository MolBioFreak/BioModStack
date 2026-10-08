/**
 * Shared PDBe Molstar loader.
 *
 * Loads the installed frontend dependency through Vite so the viewer version
 * stays aligned with package management instead of a hardcoded CDN URL.
 */

let loaded = false;
let loadPromise: Promise<void> | null = null;

const MOLSTAR_TAG_NAME = 'pdbe-molstar';

function isMolstarRegistered(): boolean {
    return typeof customElements !== 'undefined' && customElements.get(MOLSTAR_TAG_NAME) !== undefined;
}

/**
 * Ensures PDBe Molstar scripts are loaded.
 * Safe to call multiple times - will only load once.
 * 
 * @returns Promise that resolves when Molstar is ready
 */
export function ensureMolstarLoaded(): Promise<void> {
    if (loaded || isMolstarRegistered()) {
        loaded = true;
        return Promise.resolve();
    }

    if (loadPromise) {
        return loadPromise;
    }

    loadPromise = Promise.all([
        import('pdbe-molstar/build/pdbe-molstar.css'),
        import('pdbe-molstar/build/pdbe-molstar-component.js'),
    ])
        .then(() => {
            if (!isMolstarRegistered()) {
                throw new Error('PDBe Molstar custom element was not registered');
            }
            console.log('[molstar-loader] PDBe Molstar loaded from installed dependency');
            loaded = true;
        })
        .catch((error) => {
            loadPromise = null;
            console.error('[molstar-loader] Failed to load PDBe Molstar:', error);
            throw error instanceof Error ? error : new Error('Failed to load PDBe Molstar');
        });

    return loadPromise;
}

/**
 * Check if Molstar is already loaded (synchronous).
 */
export function isMolstarLoaded(): boolean {
    return loaded || isMolstarRegistered();
}

/**
 * RGB to hex color conversion utility (shared across viewers)
 */
export function rgbToHex(r: number, g: number, b: number): string {
    return '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('');
}

/**
 * Parse a hex color to RGB components
 */
export function hexToRgb(hex: string): { r: number; g: number; b: number } | null {
    const match = hex.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
    if (match) {
        return {
            r: parseInt(match[1], 16),
            g: parseInt(match[2], 16),
            b: parseInt(match[3], 16)
        };
    }
    return null;
}

/**
 * Parse residue key format "A45" to chain and residue number
 */
export function parseResidueKey(key: string): { chainId: string; resNum: number; iCode?: string } | null {
    const match = key.match(/^([A-Za-z])(-?\d+)([A-Za-z]?)$/);
    if (!match) return null;
    return { chainId: match[1], resNum: parseInt(match[2], 10), iCode: match[3] || undefined };
}

/**
 * Types for Molstar viewer instance (via web component)
 */
export interface MolstarViewerInstance {
    viewerInstance?: {
        visual: {
            select: (params: SelectParams) => Promise<void>;
            clearSelection: () => Promise<void>;
            focus: (selections: QueryParam[]) => Promise<void>;
            highlight: (params: HighlightParams) => Promise<void>;
            clearHighlight: () => Promise<void>;
        };
        canvas: {
            setBgColor: (color: { r: number; g: number; b: number }) => Promise<void>;
            toggleControls: (visible?: boolean) => void;
        };
        events?: {
            loadComplete: {
                subscribe: (callback: () => void) => { unsubscribe: () => void };
            };
        };
    };
}

export interface QueryParam {
    struct_asym_id?: string;
    auth_asym_id?: string;
    entity_id?: string;
    start_residue_number?: number;
    end_residue_number?: number;
    residue_number?: number;
    color?: string;
    focus?: boolean;
}

export interface SelectParams {
    data: QueryParam[];
    nonSelectedColor?: string;
    structureId?: string;
    structureNumber?: number;
}

export interface HighlightParams {
    data: QueryParam[];
    color?: string;
    focus?: boolean;
    structureId?: string;
    structureNumber?: number;
}

/**
 * Type for Molstar click event detail
 */
export interface MolstarClickEventDetail {
    residueNumber?: number;
    residueName?: string;
    chainId?: string;
    authChainId?: string;
    entityId?: string;
    structureId?: string;
}
