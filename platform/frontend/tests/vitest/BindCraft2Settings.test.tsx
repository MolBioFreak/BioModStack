import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import { act, create } from 'react-test-renderer'
import { BindCraft2Settings, type BC2Inventory } from '../../src/components/BindCraft2Settings'

const inventory: BC2Inventory = {
  upstream_commit: 'd5bae16e9fee95f4c97fc16bc05dcbde4ccb885f',
  fields: {
    max_trajectories: { native_key: 'max_trajectories', observed_types: ['integer'], has_native_default: false, native_default: null, status: 'typed' },
    trajectory_only: { native_key: 'trajectory_only', observed_types: ['boolean'], has_native_default: true, native_default: false, status: 'typed' },
    unresolved: { native_key: 'unresolved', observed_types: [], has_native_default: false, native_default: null, status: 'unresolved' },
    filters: { native_key: 'filters', observed_types: ['object'], has_native_default: true, native_default: { i_pTM: { threshold: 0.7, higher: true } }, status: 'typed' },
    gpu_ids: { native_key: 'gpu_ids', observed_types: [], has_native_default: false, native_default: null, status: 'unresolved' },
  },
  presets: {}, paratope_conformations: ['extended', 'folded_back'], registered_metrics: { filters: { i_pTM: { params: { prediction_state: { default_literal: 'complex', source_default: "'complex'" } } }, Binder_RMSD: { params: {} } }, losses: {} },
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
    expect(html).toContain('model execution is not enabled')
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
    expect((changes[1].filters as Record<string, unknown>).Binder_RMSD).toEqual({ higher: true })
    await act(async () => { tree!.unmount() })
  })
})
