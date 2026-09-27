// @vitest-environment jsdom
import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { BindCraft2Settings, type BC2Inventory, type BC2Request } from '../../src/components/BindCraft2Settings';

// Actual common API discovery and validation, not a frontend-only widened fixture.
const api = resolve('../api');
const python = process.env.BMS_TEST_API_PYTHON ?? resolve(api, '.venv/bin/python');
const inventory: BC2Inventory = JSON.parse(execFileSync(python, ['-c', 'import json; from services.bindcraft2_typed import schema; print(json.dumps(schema()))'], { cwd: api, encoding: 'utf8' }));
let host: HTMLDivElement;
let root: Root;
let latest: BC2Request;
let changes: BC2Request[];
function mount(value: BC2Request) {
  changes = [];
  function Form() {
    const [settings, setSettings] = useState(value); latest = settings;
    return <BindCraft2Settings inventory={inventory} value={settings} onChange={next => { changes.push(next); setSettings(next); }} />;
  }
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  act(() => root.render(<Form />));
}
function unmount() { act(() => root.unmount()); host.remove(); }
afterEach(unmount);
function element(label: string) {
  const el = host.querySelector<HTMLElement>(`[aria-label="${label}"]`)!;
  expect(el, label).not.toBeNull();
  const parents: HTMLDetailsElement[] = []; let node = el.parentElement;
  while (node) { if (node.tagName === 'DETAILS') parents.unshift(node as HTMLDetailsElement); node = node.parentElement; }
  for (const details of parents) if (!details.open) act(() => details.querySelector('summary')!.click());
  return el;
}
function edit(label: string, value: string) {
  const el = element(label);
  act(() => {
    if (el instanceof HTMLSelectElement) { el.value = value; el.dispatchEvent(new Event('change', { bubbles: true })); }
    else { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('input', { bubbles: true })); }
  });
}
function click(label: string) { const el = element(label); act(() => el.click()); }
function proveRequestAndReopen() {
  const saved = JSON.parse(JSON.stringify(latest));
  const compiled = JSON.parse(execFileSync(python, ['-c', 'import copy,json,sys; from pathlib import Path; from services.bindcraft2_typed import compile_typed; r=json.load(sys.stdin); print(json.dumps(compile_typed(r,Path("campaign"),copy.deepcopy,lambda _: ())["requested_settings"]))'], { cwd: api, encoding: 'utf8', input: JSON.stringify(saved) }));
  expect(compiled).toEqual(saved);
  unmount(); mount(compiled);
  expect(latest).toEqual(saved); expect(changes).toEqual([]);
}

it('authors exact ordered design names and indices, compiles actual requests and reopens without normalization', () => {
  mount({ max_trajectories: 100, design_models: 2, losses: { induced_fit_interface: { params: { interface_mask: [0, -0.125, 2] } } } });
  expect(changes).toEqual([]);
  expect((element('design_models.count') as HTMLInputElement).value).toBe('2');
  edit('design_models.mode', 'named'); expect(latest.design_models).toEqual([]);
  proveRequestAndReopen();
  edit('design_models.new', 'model_3_multimer_v3'); click('Add design_models');
  edit('design_models.new', 'model_1_multimer_v3'); click('Add design_models');
  edit('design_models.new.type', 'integer'); edit('design_models.new', '0'); click('Add design_models');
  expect(latest.design_models).toEqual(['model_3_multimer_v3', 'model_1_multimer_v3', 0]);
  proveRequestAndReopen();
  expect((element('losses.induced_fit_interface.params.interface_mask.1') as HTMLInputElement).value).toBe('-0.125');
  click('Reset design_models'); expect(Object.hasOwn(latest, 'design_models')).toBe(false);
  proveRequestAndReopen();
});

it('disables a native filter with null and retains explicit false flags, zero and omitted override distinctions', () => {
  mount({ max_trajectories: 100, filters: { Binder_RMSD: { threshold: 1.23456789, higher: false, mandatory: false } }, losses: { induced_fit_interface: { params: { interface_mask: null } } } });
  expect(changes).toEqual([]);
  edit('filters.Binder_RMSD.threshold.mode', 'disabled');
  expect(latest.filters).toEqual({ Binder_RMSD: { threshold: null, higher: false, mandatory: false } });
  proveRequestAndReopen();
  expect((element('filters.Binder_RMSD.threshold.mode') as HTMLSelectElement).value).toBe('disabled');
  expect(host.textContent).toContain('Disabled (null)');
  edit('filters.Binder_RMSD.threshold.mode', 'number'); edit('filters.Binder_RMSD.threshold', '0');
  proveRequestAndReopen();
  expect(latest.filters).toEqual({ Binder_RMSD: { threshold: 0, higher: false, mandatory: false } });
  click('filters.Binder_RMSD.enabled'); expect(latest.filters).toEqual({});
  proveRequestAndReopen();
});

it('distinguishes omitted, false, true, empty options and explicit sweep options through actual controls and reopen', () => {
  mount({ max_trajectories: 100 }); expect(changes).toEqual([]);
  expect((element('parameter_sweep.mode') as HTMLSelectElement).value).toBe('native');
  for (const [mode, value] of [['off', false], ['on', true], ['custom', {}]] as const) {
    edit('parameter_sweep.mode', mode); expect(latest.parameter_sweep).toEqual(value);
    proveRequestAndReopen();
    expect((element('parameter_sweep.mode') as HTMLSelectElement).value).toBe(mode);
  }
  edit('parameter_sweep.max_arms', '3'); edit('parameter_sweep.axes', 'weights_interface_contacts');
  expect(latest.parameter_sweep).toEqual({ max_arms: 3, axes: ['weights_interface_contacts'] });
  proveRequestAndReopen();
  edit('parameter_sweep.mode', 'native'); expect(Object.hasOwn(latest, 'parameter_sweep')).toBe(false);
  proveRequestAndReopen();
});
