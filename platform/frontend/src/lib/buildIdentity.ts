export type BuildIdentity = {
  layer: 'frontend'
  revision: string
  buildId: string
  buildTime: string
}

declare const __BMS_BUILD_METADATA__: Partial<BuildIdentity> | undefined

const injected = typeof __BMS_BUILD_METADATA__ === 'undefined' ? {} : __BMS_BUILD_METADATA__

export const buildIdentity: BuildIdentity = Object.freeze({
  layer: 'frontend',
  revision: injected.revision?.trim() || 'unknown',
  buildId: injected.buildId?.trim() || 'development',
  buildTime: injected.buildTime?.trim() || 'unknown',
})
