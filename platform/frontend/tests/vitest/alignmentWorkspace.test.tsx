import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import AlignmentWorkspace, { type AlignmentViewerProps } from '../../src/components/alignments/AlignmentWorkspace'

const document = { resultId: 'result-1', rows: [{ rowId: 'r1', label: 'first', aligned: 'AC-' }] }
function TestAdapter({ document: result }: AlignmentViewerProps) { return createElement('p', null, `adapter for ${result.resultId}`) }

describe('alignment workspace host', () => {
  it('reports missing viewers and only presents same-origin paths', () => {
    const html = renderToStaticMarkup(createElement(AlignmentWorkspace, { document, downloads: [
      { label: 'artifact', href: '/api/jobs/1/artifact' },
      { label: 'external', href: 'https://example.com/secret' },
      { label: 'network path', href: '//example.com/secret' },
    ] }))
    expect(html).toContain('JBrowseMSA is not connected')
    expect(html).toContain('/api/jobs/1/artifact')
    expect(html).not.toContain('example.com')
  })
  it('renders an explicitly injected adapter rather than a stand-in', () => {
    const html = renderToStaticMarkup(createElement(AlignmentWorkspace, { document, viewers: { jbrowse: TestAdapter } }))
    expect(html).toContain('adapter for result-1')
    expect(html).not.toContain('JBrowseMSA is not connected')
  })
})
