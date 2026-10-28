import { lazy } from 'react'
import type { AlignmentWorkspaceProps } from './AlignmentWorkspace'

// Do not import any adapter or raw viewer bundle in the route module.
// React.lazy invokes only the selected renderer's loader on first mount.
export const alignmentViewers: NonNullable<AlignmentWorkspaceProps['viewers']> = {
  jbrowse: lazy(() => import('./JbrowseViewer')),
  'alignment-viewer-2': lazy(() => import('./Av2Viewer')),
  biojs: lazy(() => import('./BiojsViewer')),
}
