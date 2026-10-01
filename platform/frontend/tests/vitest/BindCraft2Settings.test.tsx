// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { useBC2LeafDiscovery, bc2DisplaySelectors } from '../../src/lib/bc2LeafDisplay';
import { renderToStaticMarkup } from 'react-dom/server'
import React, { createElement, act as domAct, useState } from 'react'
import { act, create } from 'react-test-renderer'
import { createRoot } from 'react-dom/client'
import { BindCraft2Settings, type BC2Inventory } from '../../src/components/BindCraft2Settings'

// Inspect disclosures before testing their surviving typed editors. The cold
// construction/count contract is covered by launcherNativeDemandMounted.
function inspectAll(host: HTMLElement) {
  for (let depth = 0; depth < 4; depth++) domAct(() => {
    for (const node of host.querySelectorAll('details')) {
      node.open = true; node.dispatchEvent(new Event('toggle'));
    }
  });
}
async function inspectTree(tree: ReturnType<typeof create>) {
  for (let depth = 0; depth < 4; depth++) await act(async () => {
    for (const node of tree.root.findAllByType('details')) node.props.onToggle?.({ currentTarget: { open: true } });
  });
}

const inventory: BC2Inventory = {
  upstream_commit: 'd5bae16e9fee95f4c97fc16bc05dcbde4ccb885f',
  recommended_defaults: { subbatch_size: null },
  fields: {
    subbatch_size: { native_key: 'subbatch_size', observed_types: ['string', 'integer', 'null'], has_native_default: true, native_default: 'auto', recommended_default: null, recommended_default_reason: 'No chunking by default for new BMS campaigns; uses more VRAM.', status: 'typed' },
    max_trajectories: { native_key: 'max_trajectories', observed_types: ['integer'], has_native_default: false, native_default: null, status: 'typed' },
    cyclic_offset_mode: { native_key: 'cyclic_offset_mode', observed_types: ['string'], has_native_default: false, native_default: null, choices: ['distance', 'direction', 'neighbours'], runtime_fallback: 'direction', status: 'typed' },
    trajectory_only: { native_key: 'trajectory_only', observed_types: ['boolean'], has_native_default: true, native_default: false, status: 'typed' },
    unresolved: { native_key: 'unresolved', observed_types: [], has_native_default: false, native_default: null, status: 'unresolved' },
    filters: { native_key: 'filters', observed_types: ['object'], has_native_default: true, native_default: { i_pTM: { threshold: 0.7, higher: true } }, status: 'typed' },
    parameter_sweep: { native_key: 'parameter_sweep', observed_types: ['object'], has_native_default: false, native_default: null, status: 'typed' },
    weights_interface_contacts: { native_key: 'weights_interface_contacts', observed_types: ['number'], has_native_default: false, native_default: null, status: 'typed' },
    binder_shapes: { native_key: 'binder_shapes', observed_types: ['array'], has_native_default: false, native_default: null, status: 'typed' },
    crop_fasta_sequence: { native_key: 'crop_fasta_sequence', observed_types: ['integer', 'array', 'boolean'], has_native_default: false, native_default: null, status: 'typed' },
    validation_models: { native_key: 'validation_models', observed_types: ['integer', 'array'], has_native_default: false, native_default: null, status: 'typed' },
    humanize: { native_key: 'humanize', observed_types: ['boolean'], has_native_default: false, native_default: null, status: 'typed' },
    gpu_ids: { native_key: 'gpu_ids', observed_types: [], has_native_default: false, native_default: null, status: 'unresolved' },
    ...Object.fromEntries(Object.entries({ relax_steps: 200, relax_learning_rate: 0.02, relax_restraint_backbone: 10, relax_restraint_sidechain: 0.5, relax_weight_bond: 100, relax_weight_clash: 5, relax_overlap_tol: 0.4, relax_min_sep: 2.5 }).map(([key, fallback]) => [key, { native_key: key, observed_types: [key === 'relax_steps' ? 'integer' : 'number'], has_native_default: false, native_default: null, runtime_fallback: fallback, applicable_when: { relax_accepted_designs: true }, fallback_authority: 'bindcraft.protein.default_relax_parameters', status: 'typed' as const }])),
  },
  presets: {}, paratope_conformations: ['extended', 'folded_back'], registered_metrics: { filters: {
    i_pTM: { params: { prediction_state: { default_literal: 'complex', source_default: "'complex'", request_types: ['string'] } } },
    Binder_RMSD: { params: { reference_state: { default_literal: null, source_default: 'BINDER_ALONE', resolved_default: 'binder_alone', request_types: ['string'] } } },
    Epitope_Residues_Contacted: { params: { epitope_cutoff: { default_literal: null, source_default: 'EPITOPE_CUTOFF', resolved_default: 10, request_types: ['number'] } } },
  }, losses: {} },
}

describe('BC2 model-owned operator adapter', () => {
  it('renders typed finite budget, native default, metric controls and unresolved status', () => {
    const html = renderToStaticMarkup(createElement(BindCraft2Settings, {
      inventory, value: { max_trajectories: 3, filters: { i_pTM: { threshold: 0.8, higher: true } } }, onChange: () => {},
    }))
    expect(html).toContain('aria-label="max_trajectories"')
    // Native-key labels and fieldsets must shrink in the narrow served form.
    expect(html).toContain('[overflow-wrap:anywhere]')
    expect(html).toContain('[&amp;_fieldset]:min-w-0')
    expect(html).toContain('[&amp;_button]:max-w-full')
    expect(html).toContain('aria-label="trajectory_only"')
    expect(html).toContain('Native default: false')
    expect(html).not.toContain('aria-label="cyclic_offset_mode"')
    expect(html).not.toContain('aria-label="filters.i_pTM.threshold"')
    expect(html).toContain('Configure Interface pTM')
    expect(html).not.toContain('aria-label="gpu_ids"')
    // Launch ownership stays in the parent; settings introduce no execution gates.
    expect(html).not.toContain('Unresolved settings prevent');
    const unavailable = renderToStaticMarkup(createElement(BindCraft2Settings, { inventory, value: {}, onChange: () => {}, launchAvailable: false }));
    expect(unavailable).toContain('aria-label="max_trajectories"');
    expect(unavailable).not.toContain('Model execution is not enabled.');
  })

  it('edits native sweep controls without inventing an arm budget', async () => {
    const changes: Record<string, unknown>[] = []
    let tree: ReturnType<typeof create> | undefined
    await act(async () => { tree = create(createElement(BindCraft2Settings, {
      inventory, value: { max_trajectories: 7, parameter_sweep: { axes: [], max_arms: 5 } },
      onChange: next => changes.push(next),
    })) })
    await inspectTree(tree!);
    await act(async () => { tree!.root.findByProps({ 'aria-label': 'parameter_sweep.axes' }).props.onChange({ currentTarget: { value: 'weights_interface_contacts' } }) })
    expect(changes[0].parameter_sweep).toEqual({ axes: ['weights_interface_contacts'], max_arms: 5 })
    await act(async () => { tree!.root.findByProps({ 'aria-label': 'parameter_sweep.max_arms' }).props.onChange({ currentTarget: { value: '3' } }) })
    expect(changes[1].parameter_sweep).toEqual({ axes: [], max_arms: 3 })
    await act(async () => { tree!.unmount() })
  })

  it('uses native filter cutoffs or requires an explicit one, never inventing zero', async () => {
    const changes: Record<string, unknown>[] = []
    let tree: ReturnType<typeof create> | undefined
    await act(async () => {
      tree = create(createElement(BindCraft2Settings, {
        inventory, value: { max_trajectories: 3, filters: {} }, onChange: next => changes.push(next),
      }))
    })
    await inspectTree(tree!);
    await act(async () => {
      tree!.root.findByProps({ 'aria-label': 'filters.i_pTM.enabled' }).props.onChange({ currentTarget: { checked: true } })
    })
    expect((changes[0].filters as Record<string, unknown>).i_pTM).toEqual({ threshold: 0.7 })
    await act(async () => {
      tree!.root.findByProps({ 'aria-label': 'filters.Binder_RMSD.enabled' }).props.onChange({ currentTarget: { checked: true } })
    })
    expect((changes[1].filters as Record<string, unknown>).Binder_RMSD).toEqual({})
    await act(async () => { tree!.unmount() })
  })
  it('mounts a new metric without inventing a zero threshold and preserves typed edits', () => {
    let latest: Record<string, unknown> = {}
    function Form() {
      const [value, setValue] = useState<Record<string, unknown>>({ max_trajectories: 3 })
      latest = value
      return <BindCraft2Settings inventory={inventory} value={value} onChange={setValue} />
    }
    const host = document.createElement('div')
    document.body.append(host)
    const root = createRoot(host)
    domAct(() => root.render(<Form />)); inspectAll(host)
    const enable = host.querySelector<HTMLInputElement>('[aria-label="filters.Binder_RMSD.enabled"]')!
    domAct(() => enable.click())
    inspectAll(host)
    expect((latest.filters as Record<string, unknown>).Binder_RMSD).toEqual({})
    expect((latest.filters as Record<string, unknown>).i_pTM).toBeUndefined()
    const threshold = host.querySelector<HTMLInputElement>('[aria-label="filters.Binder_RMSD.threshold"]')!
    expect(threshold.value).toBe('')
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    domAct(() => { setter.call(threshold, '1.25'); threshold.dispatchEvent(new Event('input', { bubbles: true })) })
    expect((latest.filters as Record<string, unknown>).Binder_RMSD).toEqual({ threshold: 1.25 })
    const state = host.querySelector<HTMLInputElement>('[aria-label="filters.Binder_RMSD.params.reference_state"]')!
    expect(state.value).toBe('binder_alone')
    domAct(() => root.unmount())
    host.remove()
  })
})

it('mounts every optional native relaxation numeric control without filling omitted values', () => {
  let latest: Record<string, unknown> = {}
  function Form() {
    const [value, setValue] = useState<Record<string, unknown>>({ max_trajectories: 3 })
    latest = value
    return <BindCraft2Settings inventory={inventory} value={value} onChange={setValue} />
  }
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  domAct(() => root.render(<Form />)); inspectAll(host)
  try {
    const keys = Object.keys(inventory.fields).filter(key => key.startsWith('relax_'))
    expect(keys).toHaveLength(8)
    for (const key of keys) {
      const input = host.querySelector<HTMLInputElement>(`[aria-label="${key}"]`)!
      expect(input).not.toBeNull()
      expect(input.type).toBe('number')
      expect(input.value).toBe('')
      const label = input.closest('[data-bc2-field]')!.textContent!
      expect(label).toContain('Applies when relax_accepted_designs is enabled')
      expect(label).toContain('Native relaxation fallback when omitted')
    }
    expect(keys.every(key => !(key in latest))).toBe(true)
    const steps = host.querySelector<HTMLInputElement>('[aria-label="relax_steps"]')!
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    domAct(() => { setter.call(steps, '12'); steps.dispatchEvent(new Event('input', { bubbles: true })) })
    expect(latest.relax_steps).toBe(12)
    expect(keys.filter(key => key in latest)).toEqual(['relax_steps'])
  } finally { domAct(() => root.unmount()); host.remove() }
})

it('mounted scientific controls emit typed operator edits', () => {
  let latest: Record<string, unknown> = {}
  function Form() {
    const [value, setValue] = useState<Record<string, unknown>>({ max_trajectories: 3, filters: {} })
    latest = value
    return <BindCraft2Settings inventory={inventory} value={value} onChange={setValue} />
  }
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  domAct(() => root.render(<Form />)); inspectAll(host)
  try {
    domAct(() => host.querySelector<HTMLInputElement>('[aria-label="humanize"]')!.click())
    expect(latest.humanize).toBe(true)
    domAct(() => Array.from(host.querySelectorAll('button')).find(button => button.textContent === 'Add conformation group')!.click())
    expect(latest.binder_shapes).toEqual([['']])
    domAct(() => host.querySelector<HTMLInputElement>('[aria-label="filters.i_pTM.enabled"]')!.click())
    expect((latest.filters as Record<string, unknown>).i_pTM).toEqual({ threshold: 0.7 })
    expect(host.querySelector('[aria-label="crop_fasta_sequence.mode"]')).not.toBeNull()
    expect(host.querySelector('[aria-label="validation_models.mode"]')).not.toBeNull()
  } finally { domAct(() => root.unmount()); host.remove() }
})



describe('BC2 chunking modes', () => {
  function mount(initial: Record<string, unknown>) {
    let latest = initial;
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host);
    function Form() {
      const [value, setValue] = useState(initial); latest = value;
      return <BindCraft2Settings inventory={inventory} value={value} onChange={setValue} section="campaign"
        inherited={{ selectors: {}, values: { subbatch_size: 'auto' } }} effectiveSettings={{ subbatch_size: 4 }} />;
    }
    domAct(() => root.render(<Form />));
    return { host, latest: () => latest, close: () => { domAct(() => root.unmount()); host.remove(); } };
  }
  function mode(host: HTMLElement, value: string) {
    const select = host.querySelector<HTMLSelectElement>('[aria-label="subbatch_size.mode"]')!;
    domAct(() => { select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })); });
  }
  it('defaults Off in Generation without mutating sparse inheritance; modes emit native types and reset recommendation', () => {
    const form = mount({ campaign_seed: 0 });
    try {
      const select = form.host.querySelector<HTMLSelectElement>('[aria-label="subbatch_size.mode"]')!;
      expect(select.value).toBe('off'); expect(select.closest('[hidden]')).toBeNull();
      expect(form.host.querySelector('[aria-label="subbatch_size"]')).toBeNull();
      expect(form.latest()).toEqual({ campaign_seed: 0 });
      expect(select.closest('[data-bc2-field]')!.textContent).toContain('BMS recommended default');
      expect(select.closest('[data-bc2-field]')!.textContent).toContain('Native default: "auto"');
      mode(form.host, 'auto'); expect(form.latest()).toEqual({ campaign_seed: 0, subbatch_size: 'auto' });
      mode(form.host, 'custom'); expect(form.latest().subbatch_size).toBe(16);
      const input = form.host.querySelector<HTMLInputElement>('[aria-label="subbatch_size"]')!;
      expect(input.type).toBe('number'); expect(input.step).toBe('1'); expect(input.max).toBe('');
      domAct(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '4096'); input.dispatchEvent(new Event('input', { bubbles: true })); });
      expect(form.latest().subbatch_size).toBe(4096);
      mode(form.host, 'off'); expect(form.latest().subbatch_size).toBeNull();
      domAct(() => form.host.querySelector<HTMLButtonElement>('[aria-label="Reset subbatch_size"]')!.click());
      expect(form.latest()).toEqual({ campaign_seed: 0 }); expect(select.value).toBe('off');
    } finally { form.close(); }
  });
  it.each([null, 'auto', 1, 8192])('preserves explicit saved %j through mounted JSON reopen without inventing custom size', saved => {
    const original = { subbatch_size: saved, max_trajectories: 7, losses: { custom: {} } };
    const form = mount(JSON.parse(JSON.stringify(original)));
    try {
      expect(form.latest()).toEqual(original);
      expect(form.host.querySelector<HTMLSelectElement>('[aria-label="subbatch_size.mode"]')!.value).toBe(saved === null ? 'off' : saved === 'auto' ? 'auto' : 'custom');
      if (typeof saved === 'number') expect(form.host.querySelector<HTMLInputElement>('[aria-label="subbatch_size"]')!.value).toBe(String(saved));
    } finally { form.close(); }
  });
});

// Typed display fixtures exercise the leaf contract, not API/native runtime resolution.
describe('BC2 sparse display transitions', () => {
  const displayInventory: BC2Inventory = {
    ...inventory,
    fields: {
      ...inventory.fields,
      core: { native_key: 'core', observed_types: ['string'], has_native_default: false, native_default: null, status: 'typed' },
      number_of_final_designs: { native_key: 'number_of_final_designs', observed_types: ['integer'], has_native_default: false, native_default: null, runtime_fallback: 1, source_evidence: "bindcraft/campaign.py:settings.get('number_of_final_designs', 1)", status: 'typed' },
      campaign_seed: { native_key: 'campaign_seed', observed_types: ['integer'], has_native_default: false, native_default: null, status: 'typed' },
      autotune: { native_key: 'autotune', observed_types: ['boolean'], has_native_default: true, native_default: true, status: 'typed' },
      desperation: { native_key: 'desperation', observed_types: ['boolean'], has_native_default: true, native_default: true, status: 'typed' },
      relax_accepted_designs: { native_key: 'relax_accepted_designs', observed_types: ['boolean'], has_native_default: true, native_default: false, status: 'typed' },
      binder_lengths: { native_key: 'binder_lengths', observed_types: ['array'], items: { type: 'integer' }, has_native_default: false, native_default: null, status: 'typed' },
      weights_binder_contacts: { native_key: 'weights_binder_contacts', observed_types: ['number'], has_native_default: true, native_default: 1, status: 'typed' },
      losses: { native_key: 'losses', observed_types: ['object'], has_native_default: false, native_default: null, status: 'typed' },
    },
    presets: { core: { benchmark: {} } },
    registered_metrics: { ...inventory.registered_metrics, losses: { binder_contacts: inventory.registered_metrics.filters.i_pTM } },
  };
  function mount(initial: Record<string, unknown>, inherited?: BC2Inventory['display'], effective?: Record<string, unknown>) {
    let latest = initial;
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host);
    function Form() {
      const [value, setValue] = useState(initial); latest = value;
      return <BindCraft2Settings inventory={displayInventory} value={value} inherited={inherited} effectiveSettings={effective} onChange={setValue} />;
    }
    domAct(() => root.render(<Form />)); inspectAll(host);
    return { host, latest: () => latest, close: () => { domAct(() => root.unmount()); host.remove(); } };
  }
  const input = (host: HTMLElement, key: string) => host.querySelector<HTMLInputElement>(`[aria-label="${key}"]`)!;
  function edit(host: HTMLElement, key: string, text: string) {
    domAct(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input(host, key), text); input(host, key).dispatchEvent(new Event('input', { bubbles: true })); });
  }
  it('clear removes only the required attempt key, survives serialized reopen and never claims inheritance', () => {
    const form = mount({ max_trajectories: 7, campaign_seed: 0 });
    expect(form.host.textContent).toContain('Clear explicit attempt limit (required)');
    domAct(() => form.host.querySelector<HTMLButtonElement>('[aria-label="Reset max_trajectories"]')!.click());
    expect(form.latest()).toEqual({ campaign_seed: 0 });
    expect(input(form.host, 'max_trajectories').value).toBe('');
    expect(form.host.textContent).toContain('Required · unconfigured · no native default');
    const reopened = mount(JSON.parse(JSON.stringify(form.latest())), { selectors: {}, values: { max_trajectories: 123 } }, { max_trajectories: 456 });
    expect(input(reopened.host, 'max_trajectories').value).toBe('');
    reopened.close(); form.close();
  });
  it('shows source-qualified accepted fallback and relaxation only ON without seeding drafts', () => {
    const form = mount({});
    expect(input(form.host, 'number_of_final_designs').value).toBe('1');
    expect(input(form.host, 'relax_steps').value).toBe('');
    domAct(() => input(form.host, 'relax_accepted_designs').click());
    expect(input(form.host, 'relax_steps').value).toBe('200');
    expect(input(form.host, 'relax_learning_rate').value).toBe('0.02');
    expect(form.latest()).toEqual({ relax_accepted_designs: true });
    edit(form.host, 'relax_steps', '0');
    expect(form.latest().relax_steps).toBe(0);
    domAct(() => form.host.querySelector<HTMLButtonElement>('[aria-label="Reset relax_steps"]')!.click());
    expect(input(form.host, 'relax_steps').value).toBe('200');
    expect(form.latest()).toEqual({ relax_accepted_designs: true });
    form.close();
  });
  it('shows matching selected inheritance, explicit false/zero/null win, reset restores display only', () => {
    const inherited = { selectors: { core: 'benchmark' }, values: { campaign_seed: 42, autotune: false, desperation: false }, origins: { campaign_seed: 'core/benchmark.json' } };
    const form = mount({ core: 'benchmark' }, inherited);
    expect(input(form.host, 'campaign_seed').value).toBe('42');
    expect(input(form.host, 'autotune').checked).toBe(false);
    expect(form.host.textContent).toContain('core/benchmark.json');
    expect(form.latest()).toEqual({ core: 'benchmark' });
    edit(form.host, 'campaign_seed', '0');
    expect(form.latest()).toEqual({ core: 'benchmark', campaign_seed: 0 });
    domAct(() => form.host.querySelector<HTMLButtonElement>('[aria-label="Reset campaign_seed"]')!.click());
    expect(input(form.host, 'campaign_seed').value).toBe('42');
    expect(form.latest()).toEqual({ core: 'benchmark' });
    form.close();
    const explicit = mount({ core: 'benchmark', campaign_seed: null, autotune: false }, inherited, { campaign_seed: 9, autotune: true });
    expect(input(explicit.host, 'campaign_seed').value).toBe('');
    expect(input(explicit.host, 'autotune').checked).toBe(false);
    expect(explicit.latest().campaign_seed).toBeNull();
    explicit.close();
  });
  it('ignores stale inherited selector signature and accepts separate current compiler authority', () => {
    const form = mount({ core: 'other' }, { selectors: { core: 'benchmark' }, values: { campaign_seed: 42 } });
    expect(input(form.host, 'campaign_seed').value).toBe('');
    expect(form.host.textContent).toContain('Inherited value unavailable');
    form.close();
    const current = mount({ core: 'benchmark' }, { selectors: { core: 'benchmark' }, values: { campaign_seed: 42 } }, { campaign_seed: 81 });
    expect(input(current.host, 'campaign_seed').value).toBe('81');
    expect(current.host.textContent).toContain('Compiler effective value (read-only authority)');
    expect(current.latest()).toEqual({ core: 'benchmark' }); current.close();
  });
  it('partial saved metrics retain inherited active rows and edits never clone inherited params', () => {
    const inherited = { selectors: {}, values: { filters: { i_pTM: { threshold: 0.7, higher: true, params: { prediction_state: 'complex' } }, Binder_RMSD: { threshold: 2, higher: false } }, losses: { binder_contacts: { params: { prediction_state: 'complex' } } }, weights_binder_contacts: 1 } };
    const frozen = JSON.stringify(inherited);
    const form = mount({ filters: { Binder_RMSD: { threshold: null } }, losses: { binder_contacts: {} }, weights_binder_contacts: 0 }, inherited);
    expect(input(form.host, 'filters.i_pTM.enabled').checked).toBe(false);
    expect(input(form.host, 'filters.i_pTM.threshold').value).toBe('0.7');
    expect(form.host.textContent).toContain('Active filter');
    expect(form.host.textContent).toContain('Zero contribution');
    expect(form.host.textContent).toContain('Disabled (explicit null)');
    edit(form.host, 'filters.i_pTM.threshold', '0.8');
    expect(form.latest().filters).toEqual({ Binder_RMSD: { threshold: null }, i_pTM: { threshold: 0.8 } });
    domAct(() => input(form.host, 'filters.i_pTM.enabled').click());
    expect(form.latest().filters).toEqual({ Binder_RMSD: { threshold: null } });
    expect(input(form.host, 'filters.i_pTM.threshold').value).toBe('0.7');
    expect(JSON.stringify(inherited)).toBe(frozen);
    form.close();
  });
  it.each([[80, 120], [100], [60, 80, 100], [80.5]])('keeps saved binder lengths %j exact with integer step on existing and Add rows', (...lengths: number[]) => {
    const form = mount({ binder_lengths: lengths });
    lengths.forEach((length, index) => { expect(input(form.host, `binder_lengths.${index}`).value).toBe(String(length)); expect(input(form.host, `binder_lengths.${index}`).step).toBe('1'); });
    expect(input(form.host, 'binder_lengths.new').step).toBe('1');
    expect(form.latest()).toEqual({ binder_lengths: lengths });
    edit(form.host, 'binder_lengths.new', '140');
    domAct(() => Array.from(form.host.querySelectorAll('button')).find(button => button.textContent === 'Add value')!.click());
    expect(form.latest()).toEqual({ binder_lengths: [...lengths, 140] }); form.close();
  });
});

it('mounted discovery coalesces identical pending reads and discards stale projection success/failure', async () => {
  const requests: { url: string; resolve: (response: Response) => void; reject: (error: Error) => void }[] = [];
  const fetchMock = vi.fn((url: string) => new Promise<Response>((resolve, reject) => requests.push({ url, resolve, reject })));
  vi.stubGlobal('fetch', fetchMock);
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  let current: ReturnType<typeof useBC2LeafDiscovery<BC2Inventory>>;
  function Discovery({ core }: { core: string }) {
    current = useBC2LeafDiscovery<BC2Inventory>(true, bc2DisplaySelectors({ core }));
    return <div>{current?.inventory?.upstream_commit ?? current?.error ?? 'pending'}</div>;
  }
  try {
    await domAct(async () => root.render(<React.StrictMode><Discovery core="benchmark" /></React.StrictMode>));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(decodeURIComponent(requests[0].url.split('selectors=')[1]))).toEqual({ core: 'benchmark' });
    await domAct(async () => root.render(<React.StrictMode><Discovery core="binder" /></React.StrictMode>));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await domAct(async () => requests[1].resolve({ ok: true, json: async () => ({ model_id: 'bindcraft2', launch_available: true, settings: { ...inventory, upstream_commit: 'current' } }) } as Response));
    expect(host.textContent).toBe('current');
    await domAct(async () => requests[0].reject(new Error('obsolete')));
    expect(host.textContent).toBe('current');
    await domAct(async () => root.render(<React.StrictMode><Discovery core="benchmark" /></React.StrictMode>));
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(host.textContent).toBe('pending');
    await domAct(async () => root.render(<React.StrictMode><Discovery core="binder" /></React.StrictMode>));
    await domAct(async () => requests[2].resolve({ ok: true, json: async () => ({ settings: { ...inventory, upstream_commit: 'obsolete' } }) } as Response));
    expect(host.textContent).not.toBe('obsolete');
  } finally { domAct(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); }
});
