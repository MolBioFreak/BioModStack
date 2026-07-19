import { assessResidueRef, canonicalResidueRefKey, type ResidueRef } from '../../contracts/structureIdentity.js';
import { viewerOk, viewerUnsupported, type ViewerResult } from '../../contracts/viewerResults.js';

export interface GovernedSelectionSet { readonly id: string; readonly label: string; readonly residues: readonly ResidueRef[]; }

export class SelectionExtension {
    private readonly sets = new Map<string, GovernedSelectionSet>();

    replace(selection: GovernedSelectionSet): ViewerResult<GovernedSelectionSet> {
        if (!selection.id.trim() || !selection.label.trim()) return viewerUnsupported('Selection sets require id and label', 'selection');
        const seen = new Set<string>();
        for (const residue of selection.residues) {
            const valid = assessResidueRef(residue);
            if (valid.status !== 'ok') return valid;
            const key = canonicalResidueRefKey(residue);
            if (seen.has(key)) return viewerUnsupported(`Duplicate residue in selection: ${key}`, 'selection');
            seen.add(key);
        }
        this.sets.set(selection.id, selection);
        return viewerOk(selection);
    }

    remove(id: string): boolean { return this.sets.delete(id); }
    list(): readonly GovernedSelectionSet[] { return [...this.sets.values()]; }
}
