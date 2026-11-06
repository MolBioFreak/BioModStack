import { execFileSync } from 'node:child_process'
import { existsSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import type { Plugin } from 'vite'

type ViewerBundle = { name: string; outputs: string[]; inputs: string[]; dependency: string; command: string[] }

const VIEWERS: ViewerBundle[] = [
  { name: 'jbrowse', outputs: ['dist/viewer.js'], inputs: ['src.jsx', 'vite.config.js', 'package.json'], dependency: 'node_modules/react-msaview', command: ['node_modules/vite/bin/vite.js', 'build'] },
  { name: 'av2', outputs: ['dist/viewer.js'], inputs: ['src.jsx', 'vite.config.js', 'package.json'], dependency: 'node_modules/alignment-viewer-2', command: ['node_modules/vite/bin/vite.js', 'build'] },
  { name: 'biojs', outputs: ['dist/vendor/msa.js', 'dist/vendor/msa.css'], inputs: ['copy-vendor.mjs', 'package.json'], dependency: 'node_modules/msa', command: ['copy-vendor.mjs'] },
]

const mtime = (file: string) => { try { return statSync(file).mtimeMs } catch { return undefined } }

/** True when a generated bundle is absent or older than the source that produces it. */
export function viewerBundleStale(dir: string, viewer: Pick<ViewerBundle, 'outputs' | 'inputs'>): boolean {
  const outputs = viewer.outputs.map(output => mtime(path.join(dir, output)))
  if (outputs.some(value => value === undefined)) return true
  const oldest = Math.min(...(outputs as number[]))
  return viewer.inputs.some(input => (mtime(path.join(dir, input)) ?? 0) > oldest)
}

/**
 * The Alignments route imports generated (gitignored) viewer bundles.
 * `pnpm dev`/`pnpm build` build them first, but the managed Development
 * frontend launches Vite directly. Build any absent or stale bundle from its
 * locked workspace before serving. A failure is logged and never stops the
 * server: the route keeps the alignment, downloads and other viewers usable.
 */
export function alignmentViewerBundles(frontendRoot: string): Plugin {
  return {
    name: 'bms-alignment-viewer-bundles',
    apply: 'serve',
    configResolved() {
      const root = path.join(frontendRoot, 'alignment-viewers')
      if (!existsSync(root) || !readdirSync(root).length) return
      for (const viewer of VIEWERS) {
        const dir = path.join(root, viewer.name)
        if (!viewerBundleStale(dir, viewer)) continue
        if (!existsSync(path.join(dir, viewer.dependency))) {
          console.warn(`[alignment viewers] ${viewer.name}: locked dependencies are not installed (run the frontend dependency install); this viewer will report itself unavailable.`)
          continue
        }
        try {
          execFileSync(process.execPath, viewer.command, { cwd: dir, stdio: 'inherit', timeout: 600_000 })
        } catch (error) {
          console.warn(`[alignment viewers] ${viewer.name} build failed: ${error instanceof Error ? error.message : String(error)}`)
        }
      }
    },
  }
}
