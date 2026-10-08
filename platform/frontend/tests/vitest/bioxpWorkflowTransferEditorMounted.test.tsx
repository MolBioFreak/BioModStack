import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpWorkflowTransferEditor } from '../../src/components/BioXpWorkflowTransferEditor';
import { emptyTransferIntent, type WorkflowTransferIntent } from '../../src/lib/bioxpWorkflowPlan';
let host: HTMLDivElement, root: Root, latest: WorkflowTransferIntent;
const changed = vi.fn(), selected = vi.fn();
function Controlled({ initial = emptyTransferIntent() }: { initial?: WorkflowTransferIntent }) {
    const [value, setValue] = useState(initial); latest = value;
    return <BioXpWorkflowTransferEditor value={value} onChange={v => { changed(v); setValue(v); }} selection={{ station: 'LOC_MS', wells: ['C3', 'A1'] }} onSelect={selected} />;
}
async function button(text: string) { const node = [...host.querySelectorAll('button')].find(b => b.textContent === text || b.getAttribute('aria-label') === text)!; expect(node).toBeTruthy(); await act(async () => node.click()); }
async function input(label: string, value: string) {
    const node = [...host.querySelectorAll('label')].find(l => [...l.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join('') === label)!.querySelector('input,select') as HTMLInputElement;
    await act(async () => { Object.getOwnPropertyDescriptor(node.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(node, value); node.dispatchEvent(new Event(node.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
}
beforeEach(() => { changed.mockClear(); selected.mockClear(); host = document.createElement('div'); document.body.append(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
it('adopts deck order and native zero location, edits pairing without sorting or broadcasting', async () => {
    await act(async () => root.render(<Controlled />)); expect(changed).not.toHaveBeenCalled(); expect(latest).toEqual(emptyTransferIntent());
    await button('Use deck selection as source'); expect(latest.source).toEqual({ station: 'LOC_MS', location_id: 0, wells: ['C3', 'A1'] });
    await button('Use deck selection as destination'); await button('Move destination reference 2 earlier');
    expect(host.querySelector('[aria-label="Transfer pairs"]')?.textContent).toBe('C3 → A1A1 → C3');
    await input('Destination reference 1', 'B7'); expect(latest.destination.wells).toEqual(['B7', 'C3']);
    await button('Remove destination reference 2'); expect(host.textContent).toContain('equal lengths to compile');
    expect(latest.source.wells).toEqual(['C3', 'A1']); expect(latest.destination.wells).toEqual(['B7']);
    await button('Show source on deck'); expect(selected).toHaveBeenCalledWith({ station: 'LOC_MS', wells: ['C3', 'A1'] });
    await button('Add destination reference'); expect(latest.destination.wells).toEqual(['B7', '']);
    await button('Clear source'); expect(latest.source).toEqual({ station: '', location_id: '', wells: [] });
});
it('retains raw scalars, distinguishes deliberate native-high null and blank lift, and selects native channels', async () => {
    await act(async () => root.render(<Controlled />));
    expect(host.querySelectorAll('input[type=checkbox]')).toHaveLength(4);
    expect([...host.querySelectorAll('.bioxp-channel-choice')].map(node => node.textContent)).toEqual(['Pipette 1', 'Pipette 2', 'Pipette 3', 'Pipette 4']);
    await input('Volume per channel (µL)', '000.250'); await input('Aspirate speed', '12.00'); await input('Dispense speed', '17');
    await input('Source move Z position', '0'); await input('Destination move Z position', '2');
    await input('Source lift target', 'high'); expect(latest.source_lift_height_steps).toBeNull();
    await input('Destination lift height (steps above calibrated low)', '0012');
    const channel = [...host.querySelectorAll('input[type=checkbox]')][2] as HTMLInputElement;
    await act(async () => channel.click()); expect(latest.channels).toEqual([2]);
    await button('Swap source and destination'); expect(latest.source_lift_height_steps).toBe('0012'); expect(latest.destination_lift_height_steps).toBeNull(); expect(latest.source_position_flag).toBe('2');
    await input('Destination lift target', ''); expect(latest.destination_lift_height_steps).toBe('');
    expect(latest).toMatchObject({ volume_ul: '000.250', aspirate_speed: '12.00', dispense_speed: '17' });
    await input('Volume per channel (µL)', ''); await button('Clear channels'); expect(latest.volume_ul).toBe(''); expect(latest.channels).toEqual([]);
});
it('does not normalize saved unknown numeric flags, null lifts or channel order on unrelated edits', async () => {
    const initial = { ...emptyTransferIntent(), source_position_flag: '09', source_lift_height_steps: null, channels: [7, 2, 99], volume_ul: 1.5 };
    await act(async () => root.render(<Controlled initial={initial} />)); expect(changed).not.toHaveBeenCalled();
    expect(host.textContent).toContain('Saved value: 09'); await input('Dispense speed', '');
    await input('Aspirate speed', '3'); expect(latest).toEqual({ ...initial, aspirate_speed: '3' });
});
