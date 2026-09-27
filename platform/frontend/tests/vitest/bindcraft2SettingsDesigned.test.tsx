// @vitest-environment jsdom
import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { BindCraft2Settings, type BC2Inventory, type BC2Request } from '../../src/components/BindCraft2Settings';
import { BC2_SYSTEM_FIELDS, BC2_EXPERT_GROUPS } from '../../src/lib/bindcraft2ControlMetadata';

// Use the checked-in inventory denominator, not a hand-picked set of fields.
const raw = JSON.parse(readFileSync('../api/config/models/bindcraft2_native_inventory.json', 'utf8'));
const overlay = JSON.parse(readFileSync('../api/config/models/bindcraft2_typed_inventory.json', 'utf8'));
const inventory: BC2Inventory = structuredClone(raw);
for (const [key, descriptor] of Object.entries(overlay.top_level_resolved)) Object.assign(inventory.fields[key], descriptor, { status: 'typed' });
for (const [group, metrics] of Object.entries(inventory.registered_metrics)) for (const [metric, info] of Object.entries(metrics)) for (const [name, descriptor] of Object.entries(info.params)) {
  const specific = overlay.metric_parameter_types?.[`${group}.${metric}.${name}`];
  const expression = overlay.metric_expression_types[descriptor.source_default ?? ''];
  if (specific) { descriptor.request_types = specific.types; descriptor.resolved_default = specific.default; }
  else if (descriptor.default_literal !== null) descriptor.request_types = [Array.isArray(descriptor.default_literal) ? 'array' : typeof descriptor.default_literal];
  else if (expression) { descriptor.request_types = expression.types; descriptor.resolved_default = expression.default; descriptor.native_default_encoding = expression.default_encoding; }
}
let host: HTMLDivElement;
let root: Root;
let latest: BC2Request;
let hydrate: (value: BC2Request) => void;
let changes: BC2Request[];
function mount(value: BC2Request = {}, slot = false) {
  latest = value; changes = [];
  function Form() {
    const [settings, setSettings] = useState(value);
    latest = settings; hydrate = setSettings;
    return <BindCraft2Settings inventory={inventory} value={settings} onChange={next => { changes.push(next); setSettings(next); }} structureInputs={slot ? <div data-testid="rich-structures">Structure workspace</div> : undefined} />;
  }
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  act(() => root.render(<Form />));
}
afterEach(() => { if (root) act(() => root.unmount()); host?.remove(); });
function input(label: string) { const element = host.querySelector<HTMLInputElement>(`[aria-label="${label}"]`); expect(element, label).not.toBeNull(); return element!; }
/** Open real disclosure summaries outermost first before interacting. */
function reveal(element: Element) {
  const parents: HTMLDetailsElement[] = []; let node = element.parentElement;
  while (node) { if (node.tagName === 'DETAILS') parents.unshift(node as HTMLDetailsElement); node = node.parentElement; }
  for (const details of parents) if (!details.open) act(() => details.querySelector('summary')!.click());
  for (const details of parents) expect(details.open).toBe(true);
}
function edit(label: string, value: string) {
  const element = input(label); reveal(element);
  act(() => {
    if (element instanceof HTMLSelectElement) { element.value = value; element.dispatchEvent(new Event('change', { bubbles: true })); }
    else { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(element, value); element.dispatchEvent(new Event('input', { bubbles: true })); }
  });
}
function click(label: string) { const element = input(label); reveal(element); act(() => element.click()); }
function button(text: string) { const element = Array.from(host.querySelectorAll('button')).find(node => node.textContent === text)!; expect(element).toBeTruthy(); reveal(element); act(() => element.click()); }

const maskPath = 'losses.induced_fit_interface.params.interface_mask';
const maskParams = () => (latest.losses as Record<string, { params: Record<string, unknown> }>).induced_fit_interface.params;

describe('BC2 designed controls and saved campaign fidelity', () => {
  it('accounts for every prior scientific field exactly once, with standalone fallback controls', () => {
    mount();
    const expected = Object.keys(raw.fields).filter(key => !BC2_SYSTEM_FIELDS.has(key)).sort();
    const mounted = Array.from(host.querySelectorAll<HTMLElement>('[data-bc2-field]'), node => node.dataset.bc2Field!).sort();
    expect(mounted).toEqual(expected);
    for (const key of expected) {
      const card = host.querySelector(`[data-bc2-field="${key}"]`)!;
      expect(card.querySelector('input,select,button'), key).not.toBeNull();
      reveal(card);
    }
    expect(changes).toEqual([]);
    expect(latest).toEqual({});
    expect(host.textContent).not.toContain('editor pending');
    expect(host.querySelector('textarea')).toBeNull();
    console.info(`BC2 scientific inventory: ${expected.length} top-level controls; ${Object.keys(raw.fields).length - expected.length} scheduler/internal exclusions; no omitted scientific keys.`);
  });

  it('keeps every discovered loss/filter parameter reachable in its per-metric disclosure', () => {
    const saved = Object.fromEntries(Object.entries(inventory.registered_metrics).map(([kind, entries]) => [kind, Object.fromEntries(Object.keys(entries).map(name => [name, kind === 'filters' ? { threshold: 0, params: {} } : { params: {} }]))]));
    mount(saved);
    let params = 0; let metrics = 0;
    for (const [kind, entries] of Object.entries(inventory.registered_metrics)) for (const [metric, info] of Object.entries(entries)) {
      metrics++;
      for (const [name, descriptor] of Object.entries(info.params)) {
        expect(descriptor.request_types?.length, `${kind}.${metric}.${name}`).toBeGreaterThan(0);
        const path = `${kind}.${metric}.params.${name}`;
        const control = host.querySelector(`[aria-label="${path}"], [aria-label="${path}.mode"]`);
        expect(control, path).not.toBeNull();
        reveal(control!); params++;
      }
    }
    expect(latest).toEqual(saved); expect(changes).toEqual([]);
    console.info(`BC2 nested inventory: ${metrics} metrics, ${params} typed parameters reachable without raw JSON.`);
  });

  it('delegates only target list and scaffold, keeping the singular native preset and all other controls', () => {
    mount({ target: 'hPDL1', targets: [], binder_scaffold: 'saved.pdb', mutate_positions: 'A26-32' }, true);
    expect(host.querySelector('[data-testid="rich-structures"]')).not.toBeNull();
    const expected = Object.keys(raw.fields).filter(key => !BC2_SYSTEM_FIELDS.has(key) && !['targets', 'binder_scaffold'].includes(key)).sort();
    expect(Array.from(host.querySelectorAll<HTMLElement>('[data-bc2-field]'), node => node.dataset.bc2Field!).sort()).toEqual(expected);
    const preset = input('target') as unknown as HTMLSelectElement;
    expect(preset.multiple).toBe(false); expect(preset.value).toBe('hPDL1');
    edit('target', 'mPDL1'); expect(latest.target).toBe('mPDL1');
    expect(latest.targets).toEqual([]); expect(latest.binder_scaffold).toBe('saved.pdb'); expect(latest.mutate_positions).toBe('A26-32');
  });

  it('edits ordered modality chips without resetting saved biological controls', () => {
    mount({ modality: ['VHH', 'induced_fit'], core: 'benchmark', humanize: false, campaign_seed: 0, binder_lengths: [], weights_induced_fit_interface: -0.125 });
    expect(changes).toEqual([]);
    click('Move modality induced_fit earlier'); expect(latest.modality).toEqual(['induced_fit', 'VHH']);
    click('Remove modality VHH'); expect(latest.modality).toEqual(['induced_fit']);
    edit('modality', 'binder'); expect(latest.modality).toEqual(['induced_fit', 'binder']);
    expect(latest.humanize).toBe(false); expect(latest.campaign_seed).toBe(0); expect(latest.binder_lengths).toEqual([]); expect(latest.weights_induced_fit_interface).toBe(-0.125);
    click('Reset modality'); expect(Object.hasOwn(latest, 'modality')).toBe(false);
  });

  it('hydrates and rehydrates saved clones without normalizing false/zero/null/empty lists or unknown fields', () => {
    const saved = { campaign_seed: 0, humanize: false, binder_lengths: [], paratope_conformations: [], targets: [], aa_bias: { C: 0 }, filters: {}, losses: { induced_fit_interface: { params: { interface_mask: null, cutoff: 8.125 }, prediction_state: 'complex' } }, native_future: { preserve: [] } };
    mount(saved);
    expect(changes).toEqual([]); expect(latest).toEqual(saved);
    edit('number_of_final_designs', '4'); expect(latest).toEqual({ ...saved, number_of_final_designs: 4 });
    const clone = { ...saved, losses: { induced_fit_interface: { params: { interface_mask: [0, -0.5, 1.234567890123, 2], cutoff: 0 } } }, campaign_seed: 13 };
    act(() => hydrate(clone)); expect(latest).toEqual(clone);
    reveal(input(`${maskPath}.2`)); expect(input(`${maskPath}.2`).value).toBe('1.234567890123');
    edit('campaign_seed', '0'); expect(latest).toEqual({ ...clone, campaign_seed: 0 });
  });

  it('distinguishes omitted mask, automatic null, empty vector and precise native row weights', () => {
    mount({ losses: { induced_fit_interface: { params: { chain: 'binder' } } } });
    expect(input(`${maskPath}.mode`).value).toBe('native'); expect(changes).toEqual([]);
    edit(`${maskPath}.mode`, 'null'); expect(maskParams()).toEqual({ chain: 'binder', interface_mask: null });
    edit(`${maskPath}.mode`, 'vector'); expect(maskParams().interface_mask).toEqual([]);
    edit(`${maskPath}.new`, '-0.125'); button('Add row');
    edit(`${maskPath}.new`, '1.234567890123'); button('Add row');
    edit(`${maskPath}.0`, '0'); expect(maskParams().interface_mask).toEqual([0, 1.234567890123]);
    click(`Remove ${maskPath}.0`); expect(maskParams().interface_mask).toEqual([1.234567890123]);
    edit(`${maskPath}.mode`, 'native'); expect(maskParams()).toEqual({ chain: 'binder' });
    expect(host.textContent).toContain('not target hotspots or PDB residue numbers');
  });

  it('synchronizes slider and precise inputs without clamping native values or filling omissions', () => {
    mount({ target_flexibility: 0.123456789, min_plddt_screen: 1.125, weights_interface_contacts: -1.25 });
    expect(input('target_flexibility').value).toBe('0.123456789');
    expect(input('min_plddt_screen').value).toBe('1.125');
    expect(input('min_plddt_screen').max).toBe('');
    expect(changes).toEqual([]);
    edit('target_flexibility slider', '0.45'); expect(latest.target_flexibility).toBe(0.45); expect(input('target_flexibility').value).toBe('0.45');
    edit('target_flexibility', '0.987654321'); expect(latest.target_flexibility).toBe(0.987654321);
    click('Reset target_flexibility'); expect(Object.hasOwn(latest, 'target_flexibility')).toBe(false);
    expect(latest.min_plddt_screen).toBe(1.125); expect(latest.weights_interface_contacts).toBe(-1.25);
  });

  it('reaches conditional expert groups through disclosure summaries and search without changing request', () => {
    mount({ modality: 'binder', relax_learning_rate: 0.0000123, binder_shapes: [] });
    for (const group of BC2_EXPERT_GROUPS) {
      const summary = Array.from(host.querySelectorAll('summary')).find(node => node.textContent?.startsWith(group));
      if (!summary) continue;
      expect((summary.parentElement as HTMLDetailsElement).open).toBe(false);
      act(() => summary.click()); expect((summary.parentElement as HTMLDetailsElement).open).toBe(true);
    }
    expect(changes).toEqual([]);
    edit('Find expert setting', 'relax_learning_rate');
    const rate = input('relax_learning_rate'); expect(rate.closest('details')?.open).toBe(true);
    edit('relax_learning_rate', '0'); expect(latest.relax_learning_rate).toBe(0);
    edit('Find expert setting', ''); expect(latest.binder_shapes).toEqual([]);
    click('Reset relax_learning_rate'); expect(Object.hasOwn(latest, 'relax_learning_rate')).toBe(false);
  });

  it('configures filters without fake cutoffs and restores nested omitted booleans', () => {
    mount({ filters: {} });
    edit('Find filters', 'Binder_RMSD');
    click('filters.Binder_RMSD.enabled');
    expect((latest.filters as Record<string, unknown>).Binder_RMSD).toEqual({});
    edit('filters.Binder_RMSD.threshold', '0');
    edit('filters.Binder_RMSD.higher', 'false'); edit('filters.Binder_RMSD.mandatory', 'false');
    expect((latest.filters as Record<string, unknown>).Binder_RMSD).toEqual({ threshold: 0, higher: false, mandatory: false });
    edit('filters.Binder_RMSD.higher', 'native'); edit('filters.Binder_RMSD.mandatory', 'native');
    expect((latest.filters as Record<string, unknown>).Binder_RMSD).toEqual({ threshold: 0 });
    edit('filters.Binder_RMSD.params.reference_state', 'custom_state');
    click('Reset filters.Binder_RMSD.params.reference_state');
    expect((latest.filters as Record<string, { params: object }>).Binder_RMSD.params).toEqual({});
  });

  it('edits numeric length lists, sweep axes and sequence windows without fabricated values', () => {
    mount({ binder_lengths: [], parameter_sweep: { max_arms: 3 }, crop_fasta_sequence: false });
    edit('binder_lengths.new', '83');
    const add = input('binder_lengths.new').closest('[role="group"]')!.querySelector('button')!;
    act(() => add.click()); expect(latest.binder_lengths).toEqual([83]);
    click('Remove binder_lengths.0'); expect(latest.binder_lengths).toEqual([]);
    edit('parameter_sweep.axes', 'weights_interface_contacts'); expect(latest.parameter_sweep).toEqual({ max_arms: 3, axes: ['weights_interface_contacts'] });
    edit('parameter_sweep.multiplier', '1.0123456789');
    expect((latest.parameter_sweep as Record<string, unknown>).multiplier).toBe(1.0123456789);
    edit('parameter_sweep.multiplier', ''); expect(Object.hasOwn(latest.parameter_sweep as object, 'multiplier')).toBe(false);
    edit('crop_fasta_sequence.mode', 'range'); expect(latest.crop_fasta_sequence).toBe(false);
    edit('crop_fasta_sequence.0', '11'); edit('crop_fasta_sequence.1', '37'); expect(latest.crop_fasta_sequence).toEqual([11, 37]);
    edit('crop_fasta_sequence.mode', 'off'); expect(latest.crop_fasta_sequence).toBe(false);
  });
});
