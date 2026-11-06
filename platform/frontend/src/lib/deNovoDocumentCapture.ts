import { createContext } from 'react';
import { uploadImmutableFile, type StructureSourceSelection } from './api';

/** The mounted native form owns preparation and its scientific serializer. */
export type DeNovoDraftPreparation = { current: (() => Promise<void>) | null };
export const DeNovoDraftPreparationContext = createContext<DeNovoDraftPreparation | null>(null);
export const DocumentCaptureContext = createContext<DocumentCaptureRegistry | null>(null);

/** Durable editor companion, kept outside native scientific params. */
export interface CapturedAnnotatedDocument {
    format: 'cif';
    source: StructureSourceSelection;
    sha256: string;
    size: number;
    original_source?: StructureSourceSelection;
    provenance?: Record<string, unknown>;
}

/** Publish exactly the CIF from one frozen export; never refetch mutable /cif. */
export async function adoptAnnotatedCif(cif: string, options: {
    originalSource?: StructureSourceSelection;
    modelNumber?: number;
    provenance?: Record<string, unknown>;
} = {}): Promise<CapturedAnnotatedDocument> {
    const bytes = new TextEncoder().encode(cif);
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const sha256 = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
    const file = new File([bytes], `edited-${sha256}.cif`, { type: 'chemical/x-mmcif' });
    const { data } = await uploadImmutableFile('inputs/annotated_structures', file, sha256);
    if (data.sha256 !== sha256 || data.size !== bytes.byteLength) {
        throw new Error('The saved editor export does not match the frozen CIF bytes. Your editor remains open.');
    }
    return {
        format: 'cif', sha256, size: bytes.byteLength,
        source: { path: data.path, expected_sha256: sha256, output_format: 'native',
            ...(options.modelNumber !== undefined ? { model_number: options.modelNumber } : {}) },
        ...(options.originalSource !== undefined ? { original_source: structuredClone(options.originalSource) } : {}),
        ...(options.provenance !== undefined ? { provenance: structuredClone(options.provenance) } : {}),
    };
}

export interface DocumentCaptureRegistration {
    /** Monotonic document generation, including A→B→A edits/source replacement. */
    generation: () => number;
    capture: () => Promise<CapturedAnnotatedDocument | undefined>;
}

/** Mount-local callbacks only, not a document store or a scientific authority. */
export class DocumentCaptureRegistry {
    private owners = new Map<string, DocumentCaptureRegistration>();
    private generation = 0;
    private listeners = new Set<() => void>();
    /** Mount-local edit notifications invalidate inspection without authoring science. */
    changed = (): void => {
        this.generation++;
        for (const listener of this.listeners) listener();
    };
    getSnapshot = (): number => this.generation;
    subscribe = (listener: () => void): (() => void) => {
        this.listeners.add(listener);
        return () => { this.listeners.delete(listener); };
    };

    register(owner: string, registration: DocumentCaptureRegistration): () => void {
        this.owners.set(owner, registration);
        this.changed();
        return () => {
            if (this.owners.get(owner) === registration) {
                this.owners.delete(owner);
                this.changed();
            }
        };
    }

    async capture(): Promise<Record<string, CapturedAnnotatedDocument>> {
        const generation = this.generation;
        const owners = [...this.owners].map(([owner, registration]) => ({
            owner, registration, generation: registration.generation(),
        }));
        const documents: Record<string, CapturedAnnotatedDocument> = {};
        for (const item of owners) {
            const captured = await item.registration.capture();
            if (captured !== undefined) documents[item.owner] = captured;
        }
        if (this.generation !== generation || owners.some(item =>
            this.owners.get(item.owner) !== item.registration || item.registration.generation() !== item.generation)) {
            throw new Error('The molecular document changed during capture. Your edits are retained; save the current document again.');
        }
        return documents;
    }
}
