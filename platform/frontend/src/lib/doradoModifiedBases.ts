import doradoLock from '../../../../config/ngs/dorado_v2.1.2.lock.json';

/** One pinned Dorado modification model from the shipped lock. */
export interface DoradoModificationModel {
    id: string;
    molecule: 'dna' | 'rna';
    base_quality: 'fast' | 'hac' | 'sup';
    modification: string;
    canonical_base: string;
    motif: string;
    long_names: string[];
}

const ALL_MODELS = Object.values(
    (doradoLock.models as unknown as { modified_bases: Record<string, DoradoModificationModel> }).modified_bases,
);

/** Pinned modification models that extend the selected basecaller. */
export function modificationCatalog(molecule: string, quality: string): DoradoModificationModel[] {
    return ALL_MODELS
        .filter(model => model.molecule === molecule && model.base_quality === quality)
        .sort((a, b) => a.canonical_base.localeCompare(b.canonical_base) || a.modification.localeCompare(b.modification));
}

export function parseModifiedBases(value: string | null | undefined): string[] {
    const names = String(value || 'none').split(/\s+/).filter(Boolean);
    return names.length === 1 && names[0] === 'none' ? [] : names;
}

export function serializeModifiedBases(names: readonly string[]): string {
    return names.length ? [...names].sort().join(' ') : 'none';
}

export function describeModification(model: DoradoModificationModel): string {
    const context = model.motif.length > 1 ? `${model.motif} context` : `all ${model.canonical_base}`;
    return `${model.long_names.join(' + ')} · ${context}`;
}

/**
 * Toggle one modification. Dorado allows one model per canonical base, so
 * choosing a model replaces any other selection on the same base.
 */
export function toggleModification(selected: readonly string[], catalog: readonly DoradoModificationModel[], name: string): string[] {
    if (selected.includes(name)) return selected.filter(item => item !== name);
    const base = catalog.find(model => model.modification === name)?.canonical_base;
    const kept = selected.filter(item => catalog.find(model => model.modification === item)?.canonical_base !== base);
    return [...kept, name];
}

/** Explain why a selection is not runnable, mirroring the API resolver. */
export function modifiedBasesBlocker(selected: readonly string[], molecule: string, quality: string, mode: string): string | null {
    if (!selected.length) return null;
    if (mode !== 'simplex') return 'Modified-base calling requires one-strand (simplex) basecalling.';
    const catalog = modificationCatalog(molecule, quality);
    const missing = selected.filter(name => !catalog.some(model => model.modification === name));
    if (missing.length) return `${missing.join(', ')} ${missing.length === 1 ? 'is' : 'are'} not available for ${molecule.toUpperCase()} ${quality.toUpperCase()} basecalling.`;
    const bases = selected.map(name => catalog.find(model => model.modification === name)?.canonical_base);
    if (new Set(bases).size !== bases.length) return 'Choose at most one modification model per canonical base.';
    return null;
}

export function modificationModelIds(selected: readonly string[], molecule: string, quality: string): string[] {
    const catalog = modificationCatalog(molecule, quality);
    return selected.map(name => catalog.find(model => model.modification === name)?.id).filter((id): id is string => Boolean(id));
}
