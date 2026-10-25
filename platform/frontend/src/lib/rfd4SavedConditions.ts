import { CIF } from 'molstar/lib/mol-io/reader/cif';
import type { AtomRef } from '../structureViewer/contracts/structureIdentity';
import type { MetricLayer, MetricValue } from '../structureViewer/metrics/metricContracts';
import metadata from './rfd4ConditionMetadata.json';

type SavedIdentity = AtomRef & { modelNumber: number };
export type SavedCondition = { family: string; identity: SavedIdentity; value: string | string[] };
/** Read retained AtomWorks annotations, not query evaluation or current Cartn_xyz targets. */
export async function readSavedConditions(text: string, documentId: string, modelNumber?: number) {
    const parsed = await CIF.parseText(text).run();
    if (parsed.isError) throw new Error(parsed.message);
    const rows: SavedCondition[] = [];
    for (const block of parsed.result.blocks) {
        const atoms = block.categories.atom_site;
        if (!atoms) continue;
        const field = (key: string, i: number) => atoms.getField(key)?.str(i);
        const present = (v?: string) => v && !['.', '?', ''].includes(v) ? v : undefined;
        const integer = (v?: string) => v && /^-?\d+$/.test(v) ? Number(v) : undefined;
        const byModel = new Map<number, SavedIdentity[]>();
        for (let i = 0; i < atoms.rowCount; i++) {
            const model = integer(field('pdbx_PDB_model_num', i)) ?? 1;
            const local = byModel.get(model) ?? [];
            const identity: SavedIdentity = { documentId, modelNumber: model, atomIndex: local.length,
                labelAsymId: present(field('label_asym_id', i)), authAsymId: present(field('auth_asym_id', i)),
                labelSeqId: integer(field('label_seq_id', i)), authSeqId: integer(field('auth_seq_id', i)),
                insertionCode: present(field('pdbx_PDB_ins_code', i)), labelAtomId: present(field('label_atom_id', i)),
                authAtomId: present(field('auth_atom_id', i)), altLoc: present(field('label_alt_id', i)),
                componentId: present(field('label_comp_id', i)), entityId: present(field('label_entity_id', i)) };
            local.push(identity); byModel.set(model, local);
            if (modelNumber !== undefined && model !== modelNumber) continue;
            for (const [family, spec] of Object.entries(metadata.conditions)) {
                const value = present(field(spec.annotation, i));
                if (value !== undefined) rows.push({ family, identity, value });
            }
        }
        for (const [family, spec] of Object.entries(metadata.conditions)) {
            const category = block.categories[spec.annotation];
            if (!category || category.getField('idx1')) continue; // pair data is not an atom role
            for (let i = 0; i < category.rowCount; i++) {
                const index = integer(category.getField('idx0')?.str(i));
                const model = integer(category.getField('model_num')?.str(i));
                const value = category.fieldNames.filter(k => /^val\d+$/.test(k)).sort((a, b) => Number(a.slice(3)) - Number(b.slice(3))).map(k => category.getField(k)!.str(i));
                for (const [m, identities] of byModel) {
                    if (model !== undefined && model !== m || modelNumber !== undefined && modelNumber !== m) continue;
                    if (index !== undefined && identities[index] && value.length) rows.push({ family, identity: identities[index], value });
                }
            }
        }
    }
    return rows;
}

export function savedConditionLayers(rows: SavedCondition[], documentId: string): MetricLayer[] {
    return [...new Set(rows.map(r => r.family))].map(family => {
        const entries = rows.filter(r => r.family === family);
        const values: MetricValue<AtomRef>[] = entries.map(row => {
            const vector = Array.isArray(row.value);
            const value = vector ? (row.value as string[]).every(v => v.trim() !== '' && Number.isFinite(Number(v))) ? 1 : null : Number.isFinite(Number(row.value)) ? Number(row.value) : String(row.value);
            return { identity: row.identity, value, ...(value === null ? { missingness: 'unavailable' as const } : {}) };
        });
        return { descriptor: { id: `saved:${documentId}:${family}`, label: `Saved ${family}`, dimension: 'atom-scalar' as const,
            units: null, direction: 'neutral' as const, projectionPolicy: 'direct' as const, normalization: 'none' as const,
            description: 'Retained annotation role, independent of temporary selection. Vector coloring indicates saved payload presence; exact targets appear in readback.',
            provenance: { source: 'Retained AtomWorks CIF annotations', sourceVersion: metadata.image_sha256 },
            palette: { colors: ['#64748b', '#f97316'] } }, values };
    });
}
