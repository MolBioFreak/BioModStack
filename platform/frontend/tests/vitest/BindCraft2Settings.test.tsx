import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement, act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BindCraft2Settings, type BC2Inventory } from '../../src/components/BindCraft2Settings'

const inventory: BC2Inventory = {
  upstream_commit: 'd5bae16e9fee95f4c97fc16bc05dcbde4ccb885f',
  fields: {
    max_trajectories: { native_key: 'max_trajectories', observed_types: ['integer'], has_native_default: false, native_default: null, status: 'typed' },
    trajectory_only: { native_key: 'trajectory_only', observed_types: ['boolean'], has_native_default: true, native_default: false, status: 'typed' },
    unresolved: { native_key: 'unresolved', observed_types: [], has_native_default: false, native_default: null, status: 'unresolved' },
    filters: { native_key: 'filters', observed_types: ['object'], has_native_default: true, native_default: { i_pTM: { threshold: 0.7, higher: true } }, status: 'typed' },
    binder_shapes: { native_key: 'binder_shapes', observed_types: ['array'], has_native_default: false, native_default: null, status: 'typed' },
    humanize: { native_key: 'humanize', observed_types: ['boolean'], has_native_default: false, native_default: null, status: 'typed' },
    gpu_ids: { native_key: 'gpu_ids', observed_types: [], has_native_default: false, native_default: null, status: 'unresolved' },
  },
  presets: {}, paratope_conformations: ['extended', 'folded_back'], registered_metrics: { filters: { i_pTM: { params: { prediction_state: { default_literal: 'complex', source_default: "'complex'" } } } }, losses: {} },
}

describe('BC2 model-owned operator adapter', () => {
  it('renders typed finite budget, native default, metric controls and unresolved status', () => {
    const html = renderToStaticMarkup(createElement(BindCraft2Settings, {
      inventory, value: { max_trajectories: 3, filters: { i_pTM: { threshold: 0.8, higher: true } } }, onChange: () => {},
    }))
    expect(html).toContain('aria-label="max_trajectories"')
    expect(html).toContain('aria-label="trajectory_only"')
    expect(html).toContain('Native default: false')
    expect(html).toContain('aria-label="filters.i_pTM.threshold"')
    expect(html).toContain('Unsupported typed control / unresolved source type')
    expect(html).not.toContain('aria-label="gpu_ids"')
    expect(html).toContain('Launch availability is determined by the launcher.')
    const disabled = renderToStaticMarkup(createElement(BindCraft2Settings, { inventory, value: {}, onChange: () => {}, launchAvailable: false }))
    expect(disabled).toContain('Model execution is not enabled.')
    const available = renderToStaticMarkup(createElement(BindCraft2Settings, { inventory, value: {}, onChange: () => {}, launchAvailable: true }))
    expect(available).not.toContain('Model execution is not enabled.')
  })
})

it('mounted controls emit the native filter threshold instead of a fabricated zero', () => {
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  let latest: Record<string, unknown> = {}
  function Mounted() {
    const [value, setValue] = useState<Record<string, unknown>>({ max_trajectories: 2, filters: {} })
    return <BindCraft2Settings inventory={inventory} value={value} onChange={next => { latest = next; setValue(next) }} />
  }
  try {
    act(() => root.render(<Mounted />))
    const control = (name: string) => {
      const element = host.querySelector<HTMLInputElement>(`[aria-label="${name}"]`)
      if (!element) throw Error(name)
      return element
    }
    act(() => control('filters.i_pTM.enabled').click())
    expect(latest.filters).toMatchObject({ i_pTM: { threshold: 0.7, higher: true } })
    act(() => control('humanize').click())
    expect(latest.humanize).toBe(true)
    const addGroup = Array.from(host.querySelectorAll('button')).find(button => button.textContent === 'Add conformation group')!
    act(() => addGroup.click())
    expect(latest.binder_shapes).toEqual([['']])
  } finally { act(() => root.unmount()); host.remove() }
})
