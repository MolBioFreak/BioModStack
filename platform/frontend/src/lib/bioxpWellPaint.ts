import type { WorkflowAssignment, WorkflowDeckPlan, WorkflowMaterial } from './bioxpWorkflowPlan';

/** Pure helpers behind the well grids: stroke geometry and applying a stroke to a plan. */
export type GridCell = { row: number; column: number };
export const PLATE_ROWS = 'ABCDEFGH';
export const cellWell = (cell: GridCell) => `${PLATE_ROWS[cell.row]}${cell.column + 1}`;
export const wellCell = (well: string): GridCell => ({ row: PLATE_ROWS.indexOf(well[0]), column: Number(well.slice(1)) - 1 });

/** Cells on the straight line after `from` up to and including `to`, so a fast drag leaves no gaps. */
export function cellsAlong(from: GridCell, to: GridCell): GridCell[] {
    const steps = Math.max(Math.abs(to.row - from.row), Math.abs(to.column - from.column));
    const out: GridCell[] = [];
    for (let i = 1; i <= steps; i++) out.push({ row: Math.round(from.row + (to.row - from.row) * i / steps), column: Math.round(from.column + (to.column - from.column) * i / steps) });
    return out;
}

/** Every cell of the rectangle between two corners, row by row. */
export function cellsInRectangle(a: GridCell, b: GridCell): GridCell[] {
    const out: GridCell[] = [];
    for (let row = Math.min(a.row, b.row); row <= Math.max(a.row, b.row); row++)
        for (let column = Math.min(a.column, b.column); column <= Math.max(a.column, b.column); column++) out.push({ row, column });
    return out;
}

/** `a01`, `A01` and `A1` all name A1. Null when the text is not a well A1-H12. */
export function normalizeWell(text: string): string | null {
    const match = /^\s*([A-Ha-h])\s*0*([1-9]|1[0-2])\s*$/.exec(text);
    return match ? `${match[1].toUpperCase()}${match[2]}` : null;
}

/** What happens on a well that already holds something. */
export type FilledWells = 'replace' | 'keep';
export type PaintSlot = { labware_id: string; well: string };

const sameSlot = (a: WorkflowAssignment, slot: PaintSlot) => a.labware_id === slot.labware_id && a.well === slot.well;

/**
 * Assign materials to slots in order. One material fills every slot; several are placed one per slot
 * until they run out. Returns how many materials were used. A blank amount stays null.
 */
export function paintMaterials(plan: WorkflowDeckPlan, slots: PaintSlot[], materialIds: string[], volume: string | number | null,
    mode: FilledWells, newId: () => string = () => crypto.randomUUID()): { plan: WorkflowDeckPlan; used: number } {
    if (!materialIds.length || !slots.length) return { plan, used: 0 };
    let assignments = [...plan.assignments];
    const single = materialIds.length === 1;
    let used = 0;
    for (let i = 0; i < slots.length; i++) {
        const material = single ? materialIds[0] : materialIds[i];
        if (material === undefined) break;
        const slot = slots[i];
        const here = assignments.filter(a => sameSlot(a, slot));
        const mine = here.find(a => a.material_id === material);
        if (mine && (mode === 'keep' || here.length === 1)) {
            if (volume !== null && volume !== mine.volume_ul) assignments = assignments.map(a => a === mine ? { ...a, volume_ul: volume } : a);
        } else {
            if (mode === 'replace') assignments = assignments.filter(a => !sameSlot(a, slot));
            assignments.push({ id: newId(), ...slot, material_id: material, volume_ul: volume ?? mine?.volume_ul ?? null });
        }
        used = single ? 1 : i + 1;
    }
    return { plan: { ...plan, assignments }, used };
}

export function eraseSlots(plan: WorkflowDeckPlan, slots: PaintSlot[]): WorkflowDeckPlan {
    return { ...plan, assignments: plan.assignments.filter(a => !slots.some(slot => sameSlot(a, slot))) };
}

/** Drop one material on a well. If it already sits in exactly one of `sheetLabware`'s wells, it moves there. */
export function dropMaterial(plan: WorkflowDeckPlan, materialId: string, slot: PaintSlot, sheetLabware: string[], volume: string | number | null,
    mode: FilledWells, newId: () => string = () => crypto.randomUUID()): WorkflowDeckPlan {
    const placed = plan.assignments.filter(a => a.material_id === materialId && sheetLabware.includes(a.labware_id));
    if (placed.length === 1 && !sameSlot(placed[0], slot)) {
        const kept = { ...plan, assignments: plan.assignments.filter(a => a !== placed[0]) };
        return paintMaterials(kept, [slot], [materialId], volume ?? placed[0].volume_ul, mode, newId).plan;
    }
    return paintMaterials(plan, [slot], [materialId], volume, mode, newId).plan;
}

/** Short label for a well: barcode if there is one, otherwise the start of the name. */
export function materialTag(material: Pick<WorkflowMaterial, 'name' | 'barcode'> | undefined): string {
    if (!material) return '?';
    if (material.barcode) return material.barcode.slice(0, 6);
    return (String(material.name || '').trim().split(/[\s(]/)[0] || 'Unnamed').slice(0, 6);
}
