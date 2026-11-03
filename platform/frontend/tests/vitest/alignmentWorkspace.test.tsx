import React, { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { mkdtempSync, mkdirSync, writeFileSync, utimesSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { viewerBundleStale } from '../../build/alignmentViewerBundles'
import { nullLabel, fieldLabel, optionLabel, primaryFieldsFor } from '../../src/components/alignments/alignmentFieldPresentation'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import AlignmentWorkspace, { type AlignmentViewerProps } from '../../src/components/alignments/AlignmentWorkspace'
import { anchorAt, type AlignmentDocument } from '../../src/lib/alignmentCoordinates'

const document = { resultId: 'result-1', rows: [{ rowId: 'r1', label: 'first', aligned: 'AC-' }] }
function TestAdapter({ document: result }: AlignmentViewerProps) { return createElement('p', null, `adapter for ${result.resultId}`) }

describe('alignment workspace host', () => {
  it('reports missing viewers and only presents same-origin paths', () => {
    const html = renderToStaticMarkup(createElement(AlignmentWorkspace, { document, downloads: [
      { label: 'artifact', href: '/api/jobs/1/artifact' },
      { label: 'external', href: 'https://example.com/secret' },
      { label: 'network path', href: '//example.com/secret' },
    ] }))
    expect(html).toContain('JBrowseMSA is not available')
    expect(html).toContain('/api/jobs/1/artifact')
    expect(html).not.toContain('example.com')
  })
  it('renders an explicitly injected adapter rather than a stand-in', () => {
    const html = renderToStaticMarkup(createElement(AlignmentWorkspace, { document, viewers: { jbrowse: TestAdapter } }))
    expect(html).toContain('adapter for result-1')
    expect(html).not.toContain('JBrowseMSA is not available')
  })
  it('reports reverse-complemented source coordinates separately from alignment residue order', async () => {
    const reversed: AlignmentDocument = {resultId: 'reversed', rows: [{rowId: 'a', label: 'same', aligned: 'C-GT',
      sourceMemberId: 'revision', occurrenceId: 'first', sourcePositions: [2, null, 1, 0], sourceOrientation: 'reverse_complement'}]}
    const Adapter = ({document: result, onSelect}: AlignmentViewerProps) => <button onClick={() => onSelect(anchorAt(result, 'a', 0)!)}>Select first displayed base</button>
    const div = window.document.createElement('div'); const root = createRoot(div)
    try {
      await act(async () => root.render(<AlignmentWorkspace document={reversed} viewers={{jbrowse: Adapter}}/>))
      await act(async () => [...div.querySelectorAll('button')].find(button => button.textContent === 'Select first displayed base')!.click())
      expect(div.querySelector('.alignment-selection')?.textContent).toBe('Row same, column 1, residue 1; sequence position 3; reverse-complemented from the saved sequence')
    } finally { await act(async () => root.unmount()) }
  })
  it('keeps a failed renderer inside its panel with the result and downloads still usable', async () => {
    const Broken = () => { throw new Error('Failed to fetch dynamically imported module: viewer.js') }
    const div = window.document.createElement('div'); const root = createRoot(div)
    const errors = console.error; console.error = () => {}
    try {
      await act(async () => root.render(createElement(AlignmentWorkspace, { document, viewers: { jbrowse: Broken, biojs: TestAdapter }, downloads: [{ label: 'artifact', href: '/api/a' }] })))
      expect(div.querySelector('[role="alert"]')?.textContent).toContain('JBrowseMSA could not be loaded')
      expect(div.querySelector('a[href="/api/a"]')).not.toBeNull()
      expect(div.textContent).toContain('1 sequences × 3 columns')
      const select = div.querySelector('#alignment-renderer') as HTMLSelectElement
      await act(async () => { select.value = 'biojs'; select.dispatchEvent(new Event('change', { bubbles: true })) })
      expect(div.textContent).toContain('adapter for result-1')
      expect(div.querySelector('[role="alert"]')).toBeNull()
    } finally { console.error = errors; await act(async () => root.unmount()) }
  })
})

describe('alignment settings presentation', () => {
  it('names native keys for operators and resolves strategy defaults from the compiler', () => {
    expect(fieldLabel('treeout')).toBe('Save guide tree')
    expect(optionLabel('strategy', 'fft_ns_2')).toContain('FFT-NS-2')
    expect(nullLabel('maxiterate', { engine: 'mafft', strategy: 'fft_ns_2' })).toBe('Default (0)')
    expect(nullLabel('maxiterate', { engine: 'mafft', strategy: 'linsi' })).toBe('Default (1000)')
    expect(nullLabel('pair_method', { engine: 'mafft', strategy: 'ginsi' })).toBe('Default (globalpair)')
    expect(nullLabel('tree_method', { engine: 'mafft', strategy: 'auto' })).toBe('Default')
    expect(nullLabel('go', { engine: 'famsa' })).toBe('Default')
    expect(primaryFieldsFor('mafft', 'protein').has('bl')).toBe(true)
    expect(primaryFieldsFor('mafft', 'protein').has('kimura')).toBe(false)
    expect(primaryFieldsFor('mafft', 'dna').has('kimura')).toBe(true)
    expect(primaryFieldsFor('mafft', 'rna').has('bl')).toBe(false)
  })
})

describe('alignment viewer bundle freshness', () => {
  it('rebuilds absent or source-newer generated bundles only', () => {
    const dir = mkdtempSync(path.join(process.env.TMPDIR || tmpdir(), 'viewer-bundle-'))
    const spec = { outputs: ['dist/viewer.js'], inputs: ['src.jsx'] }
    writeFileSync(path.join(dir, 'src.jsx'), 'x')
    expect(viewerBundleStale(dir, spec)).toBe(true)
    mkdirSync(path.join(dir, 'dist')); writeFileSync(path.join(dir, 'dist/viewer.js'), 'y')
    utimesSync(path.join(dir, 'src.jsx'), new Date(1000), new Date(1000))
    expect(viewerBundleStale(dir, spec)).toBe(false)
    utimesSync(path.join(dir, 'src.jsx'), new Date(), new Date(Date.now() + 60_000))
    expect(viewerBundleStale(dir, spec)).toBe(true)
  })
})
