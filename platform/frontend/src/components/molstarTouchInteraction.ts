export interface MolstarTouchCapability {
    maxTouchPoints?: number | null;
    coarsePointer?: boolean | null;
}

export const MOLSTAR_TOUCH_INTERACTION_SELECTOR = [
    'canvas',
    '.msp-plugin',
    '.msp-layout-standard',
    '.msp-layout-expanded',
    '.msp-plugin-content',
    '.msp-viewport',
    '.msp-viewport-controls',
].join(', ');

export function shouldEnableMolstarTouchInteractionOverride(capability: MolstarTouchCapability): boolean {
    return Boolean((capability.maxTouchPoints ?? 0) > 0 || capability.coarsePointer);
}

export function resolveMolstarTouchAction(capability: MolstarTouchCapability): 'none' | 'auto' {
    return shouldEnableMolstarTouchInteractionOverride(capability) ? 'none' : 'auto';
}
