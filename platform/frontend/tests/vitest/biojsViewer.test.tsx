import { createElement, act } from 'react'
import { createRoot } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import BiojsViewer from '../../src/components/alignments/BiojsViewer'

describe('BioJS isolation host', () => {
  it('replaces the frame when rows change under the same result id', async () => {
    const el = window.document.createElement('div')
    window.document.body.appendChild(el)
    const root = createRoot(el)
    const doc = { resultId: 'r', rows: [{ rowId: 'one', label: 'one', aligned: 'A-' }] }
    const render = (value: typeof doc) => act(async () => { root.render(createElement(BiojsViewer, { document: value, onSelect: () => {} })) })
    await render(doc)
    const first = el.querySelector('iframe')
    await render({ ...doc, rows: [{ ...doc.rows[0] }] })
    expect(el.querySelector('iframe')).toBe(first)
    await render({ ...doc, rows: [{ ...doc.rows[0], aligned: 'AC' }] })
    const second = el.querySelector('iframe')
    expect(second).not.toBe(first)
    await render({ ...doc, rows: [{ ...doc.rows[0], rowId: 'another', aligned: 'AC' }] })
    expect(el.querySelector('iframe')).not.toBe(second)
    await act(async () => { root.unmount() })
    el.remove()
  })
  it('loads the local document in an opaque sandbox and labels full-document loading', () => {
    const html = renderToStaticMarkup(createElement(BiojsViewer, {
      document: { resultId: 'r', rows: [{ rowId: 'one', label: 'one', aligned: 'A-' }] }, onSelect: () => {},
    }))
    expect(html).toContain('sandbox="allow-scripts"')
    expect(html).toContain('srcDoc=')
    expect(html).toContain('Full alignment loaded')
    expect(html).not.toContain('allow-same-origin')
  })
})
