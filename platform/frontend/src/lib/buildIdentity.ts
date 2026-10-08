export type BuildIdentity = {
  layer: 'frontend'
  revision: string
  buildId: string
  buildTime: string
}

declare const __BMS_BUILD_METADATA__: Partial<BuildIdentity> | undefined

const injected = typeof __BMS_BUILD_METADATA__ === 'undefined' ? {} : __BMS_BUILD_METADATA__
const viteEnvironment = import.meta.env ?? {}

export const buildIdentity: BuildIdentity = Object.freeze({
  layer: 'frontend',
  revision: viteEnvironment.VITE_BMS_BUILD_SHA?.trim() || injected.revision?.trim() || 'unknown',
  buildId: viteEnvironment.VITE_BMS_BUILD_ID?.trim() || injected.buildId?.trim() || 'development',
  buildTime: viteEnvironment.VITE_BMS_BUILD_TIME?.trim() || injected.buildTime?.trim() || 'unknown',
})
