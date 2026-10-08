import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpWorkflowMaterials } from '../../src/components/BioXpWorkflowMaterials';
import type { WorkflowDeckPlan } from '../../src/lib/bioxpWorkflowPlan';
let host: HTMLDivElement, root: Root, latest: WorkflowDeckPlan;
const changed = vi.fn();
function Controlled({ initial }: { initial: WorkflowDeckPlan }) {
    const [plan, setPlan] = useState(initial); latest = plan;
    return <BioXpWorkflowMaterials plan={plan} onChange={p => { changed(p); setPlan(p); }} selection={{ station: 'LOC_RC', wells: ['B2', 'A1'] }} />;
}
async function button(text: string) { const node = [...host.querySelectorAll('button')].find(b => b.textContent === text)!; expect(node).toBeTruthy(); await act(async () => node.click()); }
async function input(label: string, value: string) {
    const node = [...host.querySelectorAll('label')].find(l => [...l.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join('') === label)!.querySelector('input,select') as HTMLInputElement;
    await act(async () => { Object.getOwnPropertyDescriptor(node.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(node, value); node.dispatchEvent(new Event(node.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
}
beforeEach(() => { changed.mockClear(); host = document.createElement('div'); document.body.append(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const blank = (): WorkflowDeckPlan => ({ labware: [], materials: [], assignments: [] });
it('creates named fixed labware, material rows and ordered explicit well assignments without defaults', async () => {
    await act(async () => root.render(<Controlled initial={blank()} />)); expect(changed).not.toHaveBeenCalled();
    await button('Add labware at selected station'); await input('Labware 1 name', 'Reaction plate');
    expect(latest.labware[0]).toMatchObject({ name: 'Reaction plate', station: 'LOC_RC', profile_id: 'LOC_RC' });
    await button('Add sample'); await input('Material 1 name', 'DNA'); await input('Material 1 description', 'Unmeasured');
    await input('Planned amount per selected well (µL)', '001.2500'); await button('Assign material to selected wells');
    expect(latest.assignments.map(a => [a.well, a.volume_ul])).toEqual([['B2', '001.2500'], ['A1', '001.2500']]);
    expect(latest.assignments.every(a => a.material_id === latest.materials[0].id)).toBe(true);
    await input('Association 1 well', 'C4'); expect(latest.assignments[0].well).toBe('C4');
    await input('Association 1 material', ''); expect(latest.assignments[0].material_id).toBe('');
    await input('Association 1 material', latest.materials[0].id);
    await input('Association 1 planned amount (µL)', ''); expect(latest.assignments[0].volume_ul).toBe('');
    await button('Unset association 2 amount'); expect(latest.assignments[1].volume_ul).toBeNull();
    await button('Add reagent'); expect(latest.materials[1]).toMatchObject({ name: '', kind: 'reagent' });
    await input('Material 2 kind', 'sample'); expect(latest.materials[1].kind).toBe('sample');
    await button('Remove material 1 and its assignments'); expect(latest.assignments).toEqual([]); expect(latest.labware).toHaveLength(1);
});
it('preserves null and unresolved legacy associations on unrelated edits and permits incomplete assignments', async () => {
    const initial: WorkflowDeckPlan = { labware: [{ id: 'l', station: 'LOC_RC', profile_id: '', name: '' }], materials: [{ id: 'm', name: '', kind: 'reagent', description: '' }], assignments: [{ id: 'a', labware_id: 'missing', material_id: 'missing', well: 'Z99', volume_ul: null }] };
    await act(async () => root.render(<Controlled initial={initial} />)); expect(changed).not.toHaveBeenCalled();
    await input('Labware 1 name', ''); await input('Material 1 description', 'note');
    expect(latest.assignments).toEqual(initial.assignments); expect(latest.labware[0].profile_id).toBe('');
    await input('Assignment material', 'm'); await button('Assign material to selected wells');
    expect(latest.assignments.slice(1).map(a => a.volume_ul)).toEqual(['', '']);
    await button('Remove labware 1 and its assignments'); expect(latest.assignments).toEqual(initial.assignments);
    await button('Remove association 1'); expect(latest.assignments).toEqual([]);
});
