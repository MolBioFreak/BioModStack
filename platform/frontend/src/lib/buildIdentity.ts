export type BuildIdentity = {
  layer: 'frontend'
  revision: string
  buildId: string
  buildTime: string
}

const injected = {
  revision: import.meta.env.VITE_BMS_BUILD_SHA,
  buildId: import.meta.env.VITE_BMS_BUILD_ID,
  buildTime: import.meta.env.VITE_BMS_BUILD_TIME,
}

export const buildIdentity: BuildIdentity = Object.freeze({
  layer: 'frontend',
  revision: injected.revision?.trim() || 'unknown',
  buildId: injected.buildId?.trim() || 'development',
  buildTime: injected.buildTime?.trim() || 'unknown',
})
