export type MolBioMobileSurface = 'map' | 'sequence' | 'details' | 'digest' | 'qc';

export type MolBioMobileBackAction = 'close-constructs' | 'show-map' | 'history';

export interface MolBioMobileLayoutEnvironment {
    cordovaShell: boolean;
    coarsePointer: boolean;
    viewportWidth: number;
    viewportHeight: number;
}

export interface MolBioMobileBackState {
    constructPickerOpen: boolean;
    hasSequence: boolean;
    surface: MolBioMobileSurface;
}

export interface MolBioMobileSequenceIntent {
    sequenceId: string;
    supersededSequenceId: string | null;
    supersededRevisionId: string | null;
}

export interface MolBioMobileSequenceIntentResolution {
    allow: boolean;
    clearIntent: boolean;
}

export function resolveMolBioMobileSequenceIntent(
    intent: MolBioMobileSequenceIntent | null,
    requestedSequenceId: string | null,
    requestedRevisionId: string | null,
): MolBioMobileSequenceIntentResolution {
    if (!intent) return { allow: true, clearIntent: false };
    const isSupersededRequest = requestedSequenceId === intent.supersededSequenceId
        && requestedRevisionId === intent.supersededRevisionId
        && !(requestedSequenceId === intent.sequenceId && requestedRevisionId === null);
    return isSupersededRequest
        ? { allow: false, clearIntent: false }
        : { allow: true, clearIntent: true };
}

const MOLBIO_TOUCH_PHONE_SHORT_AXIS_MAX = 900;

export function detectMolBioCordovaShell(target: unknown): boolean {
    if ((typeof target !== 'object' || target === null) && typeof target !== 'function') {
        return false;
    }
    const candidate = target as {
        cordova?: unknown;
        __BMS_CORDOVA_CONFIRM_READY__?: unknown;
    };
    return Boolean(candidate.cordova)
        || typeof candidate.__BMS_CORDOVA_CONFIRM_READY__ === 'function';
}

export function detectMolBioPrimaryCoarsePointer(target: unknown): boolean {
    if ((typeof target !== 'object' || target === null) && typeof target !== 'function') {
        return false;
    }
    const matchMedia = (target as { matchMedia?: unknown }).matchMedia;
    if (typeof matchMedia !== 'function') {
        return false;
    }
    try {
        return Boolean(matchMedia.call(target, '(pointer: coarse)')?.matches);
    } catch {
        return false;
    }
}

export function shouldUseMolBioMobileLayout({
    cordovaShell,
    coarsePointer,
    viewportWidth,
    viewportHeight,
}: MolBioMobileLayoutEnvironment): boolean {
    if (cordovaShell) {
        return true;
    }
    if (!coarsePointer || !Number.isFinite(viewportWidth) || !Number.isFinite(viewportHeight)) {
        return false;
    }
    return Math.min(Math.max(0, viewportWidth), Math.max(0, viewportHeight))
        <= MOLBIO_TOUCH_PHONE_SHORT_AXIS_MAX;
}

export interface MolBioMobileSequenceActivation {
    sequenceId: string;
    loadSequence: (sequenceId: string) => Promise<boolean>;
    onActivated: () => void;
}

export async function activateMobileMolBioSequence({
    sequenceId,
    loadSequence,
    onActivated,
}: MolBioMobileSequenceActivation): Promise<boolean> {
    const loaded = await loadSequence(sequenceId);
    if (!loaded) {
        return false;
    }
    onActivated();
    return true;
}

export function resolveMolBioMobileBackAction({
    constructPickerOpen,
    hasSequence,
    surface,
}: MolBioMobileBackState): MolBioMobileBackAction {
    if (constructPickerOpen && hasSequence) {
        return 'close-constructs';
    }
    if (!constructPickerOpen && hasSequence && surface !== 'map') {
        return 'show-map';
    }
    return 'history';
}
