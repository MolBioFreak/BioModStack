export type BuildIdentity = {
  layer: 'frontend'
  revision: string
  buildId: string
  buildTime: string
}

type BuildEnvironment = Partial<{
  VITE_BMS_BUILD_SHA: string
  VITE_BMS_BUILD_ID: string
  VITE_BMS_BUILD_TIME: string
}>

declare const __BMS_BUILD_METADATA__: Partial<BuildIdentity> | undefined

const injected = typeof __BMS_BUILD_METADATA__ === 'undefined' ? {} : __BMS_BUILD_METADATA__

export function resolveBuildIdentity(
  environment: BuildEnvironment = {},
  defined: Partial<BuildIdentity> = {},
): BuildIdentity {
  const revisionCandidate = environment.VITE_BMS_BUILD_SHA?.trim() || defined.revision?.trim() || ''
  return Object.freeze({
    layer: 'frontend',
    revision: /^[0-9a-f]{40}$/.test(revisionCandidate) ? revisionCandidate : 'unknown',
    buildId: environment.VITE_BMS_BUILD_ID?.trim() || defined.buildId?.trim() || 'development',
    buildTime: environment.VITE_BMS_BUILD_TIME?.trim() || defined.buildTime?.trim() || 'unknown',
  })
}

const viteEnvironment = (import.meta as ImportMeta & { readonly env?: BuildEnvironment }).env ?? {}

export const buildIdentity = resolveBuildIdentity(viteEnvironment, injected)
