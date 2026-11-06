import { defineConfig } from 'vite'
import { readFileSync } from 'node:fs'

// AV2's module reads localStorage during import, even when persistence is off.
// Replace only that module's storage calls; never change the sandbox or the
// global browser storage object. Fail the build if upstream changes the code.
function scopedVolatileStorage() {
  let transformed = false
  return {
    name: 'av2-scoped-volatile-storage',
    buildStart() { transformed = false },
    buildEnd(error) { if (!error && !transformed) throw new Error('AV2 settings module was not transformed') },
    transform(code, id) {
      id = id.replaceAll('\\', '/')
      if (id.endsWith('/alignment-viewer-2/dist/js/components/virtualization/VirtualizedMatrixViewerHook.js')) {
        // Vite treats this upstream non-module SCSS as global CSS, so its
        // webpack-style :export default is undefined. Read the actual SCSS
        // value instead of hard-coding a second, possibly divergent number.
        const scss = readFileSync(id.replace('VirtualizedMatrixViewerHook.js', 'VirtualizedMatrixViewer.scss'), 'utf8')
        const value = scss.match(/\$hoverTrackerSize:\s*(\d+)\s*;/)?.[1]
        const target = 'VirtualizedMatrixViewer_scss_1.default.hoverTrackerSize'
        if (!value || code.split(target).length !== 2) throw new Error('AV2 hover style implementation changed')
        const click = '//onClick={handleClickFn}'
        if (code.split(click).length !== 2) throw new Error('AV2 click hook changed')
        const deps = '        contentFromParent,\n        handleMousemoveFn,'
        if (code.split(deps).length !== 2) throw new Error('AV2 matrix dependencies changed')
        return code.replace(target, value).replace(deps, '        horizVirtualizationAxis?.cellSizePx, vertVirtualizationAxis?.cellSizePx, horizVirtualizationAxis?.cellCount, vertVirtualizationAxis?.cellCount,\n' + deps).replace(click, `onClick: (event) => {
          if (!horizVirtualizationAxis || !vertVirtualizationAxis || !horizParams || !vertParams) return;
          const bounds = event.currentTarget.getBoundingClientRect();
          const column = Math.floor((event.clientX - bounds.left + horizVirtualizationAxis.worldOffsetPx) / horizVirtualizationAxis.cellSizePx);
          const index = Math.floor((event.clientY - bounds.top + vertVirtualizationAxis.worldOffsetPx) / vertVirtualizationAxis.cellSizePx);
          if (index >= 0 && index < vertVirtualizationAxis.cellCount && column >= 0 && column < horizVirtualizationAxis.cellCount)
            window.dispatchEvent(new CustomEvent('bms-av2-cell', { detail: { index, column } }));
        },`)

      }
      if (!/\/alignment-viewer-2\/dist\/(?:js|esm)\/components\/settings\/LocalStorageAndURLManager\.js$/.test(id)) return
      const reads = code.match(/\blocalStorage\.getItem\(/g) || []
      const writes = code.match(/\blocalStorage\.setItem\(/g) || []
      if (reads.length !== 2 || writes.length !== 1) throw new Error('AV2 storage implementation changed; inspect before building')
      transformed = true
      return `const av2VolatileStore = new Map();\nconst av2Storage = { getItem: key => av2VolatileStore.get(key) ?? null, setItem: (key, value) => av2VolatileStore.set(key, String(value)) };\n` + code.replace(/\blocalStorage\.(getItem|setItem)\(/g, 'av2Storage.$1(')
    },
  }
}
export default defineConfig({ base: './', plugins: [scopedVolatileStorage()], build: { outDir: 'dist', rollupOptions: { input: 'src.jsx', output: { inlineDynamicImports: true, entryFileNames: 'viewer.js', format: 'iife', name: 'BmsAv2' } } } })
