import { Component, type ReactNode } from 'react'

/** Keeps a renderer load/render failure inside the viewer panel. */
export default class ViewerBoundary extends Component<{name: string; children: ReactNode}, {error: string | null}> {
  state = {error: null as string | null}
  static getDerivedStateFromError(error: unknown) { return {error: error instanceof Error ? error.message : String(error)} }
  render() {
    if (this.state.error === null) return this.props.children
    return <div role="alert" className="alignment-viewer-error">
      <p>{this.props.name} could not be loaded. The alignment, its downloads and the other viewers remain available.</p>
      <p className="text-sm">{this.state.error}</p>
    </div>
  }
}
