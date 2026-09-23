// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import React, { createElement, act as domAct, useState } from 'react'
import { act, create } from 'react-test-renderer'
import { createRoot } from 'react-dom/client'
import { BindCraft2Settings, type BC2Inventory } from '../../src/components/BindCraft2Settings'

const inventory: BC2Inventory = {
  upstream_commit: 'd5bae16e9fee95f4c97fc16bc05dcbde4ccb885f',
  fields: {
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
    expect(html).toContain('aria-label="trajectory_only"')
    expect(html).toContain('Native default: false')
    expect(html).toContain('Native runtime fallback when omitted: &quot;direction&quot;')
    expect(html).toContain('aria-label="cyclic_offset_mode"')
    expect(html).toContain('aria-label="filters.i_pTM.threshold"')
    expect(html).toContain('Unsupported typed control / unresolved source type')
    expect(html).not.toContain('aria-label="gpu_ids"')
    expect(html).toContain('Launch availability is determined by the launcher.')
    const unavailable = renderToStaticMarkup(createElement(BindCraft2Settings, { inventory, value: {}, onChange: () => {}, launchAvailable: false }))
    expect(unavailable).toContain('Model execution is not enabled.')
    const available = renderToStaticMarkup(createElement(BindCraft2Settings, { inventory, value: {}, onChange: () => {}, launchAvailable: true }))
    expect(available).toContain('Launcher reports execution available.')
  })

  it('edits native sweep controls without inventing an arm budget', async () => {
    const changes: Record<string, unknown>[] = []
    let tree: ReturnType<typeof create> | undefined
    await act(async () => { tree = create(createElement(BindCraft2Settings, {
      inventory, value: { max_trajectories: 7, parameter_sweep: { axes: [], max_arms: 5 } },
      onChange: next => changes.push(next),
    })) })
    await act(async () => { tree!.root.findByProps({ 'aria-label': 'parameter_sweep.axes' }).props.onChange({ currentTarget: { selectedOptions: [{ value: 'weights_interface_contacts' }] } }) })
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
    await act(async () => {
      tree!.root.findByProps({ 'aria-label': 'filters.i_pTM.enabled' }).props.onChange({ currentTarget: { checked: true } })
    })
    expect((changes[0].filters as Record<string, unknown>).i_pTM).toEqual({ threshold: 0.7, higher: true })
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
    domAct(() => root.render(<Form />))
    const enable = host.querySelector<HTMLInputElement>('[aria-label="filters.Binder_RMSD.enabled"]')!
    domAct(() => enable.click())
    expect((latest.filters as Record<string, unknown>).Binder_RMSD).toEqual({})
    expect((latest.filters as Record<string, unknown>).i_pTM).toEqual({ threshold: 0.7, higher: true })
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
  domAct(() => root.render(<Form />))
  try {
    const keys = Object.keys(inventory.fields).filter(key => key.startsWith('relax_'))
    expect(keys).toHaveLength(8)
    for (const key of keys) {
      const input = host.querySelector<HTMLInputElement>(`[aria-label="${key}"]`)!
      expect(input).not.toBeNull()
      expect(input.type).toBe('number')
      expect(input.value).toBe('')
      const label = input.closest('label')!.textContent!
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
  domAct(() => root.render(<Form />))
  try {
    domAct(() => host.querySelector<HTMLInputElement>('[aria-label="humanize"]')!.click())
    expect(latest.humanize).toBe(true)
    domAct(() => Array.from(host.querySelectorAll('button')).find(button => button.textContent === 'Add conformation group')!.click())
    expect(latest.binder_shapes).toEqual([['']])
    domAct(() => host.querySelector<HTMLInputElement>('[aria-label="filters.i_pTM.enabled"]')!.click())
    expect((latest.filters as Record<string, unknown>).i_pTM).toEqual({ threshold: 0.7, higher: true })
    expect(host.querySelector('[aria-label="crop_fasta_sequence.mode"]')).not.toBeNull()
    expect(host.querySelector('[aria-label="validation_models.mode"]')).not.toBeNull()
  } finally { domAct(() => root.unmount()); host.remove() }
})
