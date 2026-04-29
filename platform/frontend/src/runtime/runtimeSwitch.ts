import { getCurrentAppPath, joinBrowserUrl } from './navigation.js'

export type RuntimeMode = 'dev' | 'container'

export type RuntimePortSettings = {
  dev_web_host_port?: number | null
  prod_web_host_port?: number | null
}

type RuntimeSwitchTarget = {
  mode: RuntimeMode
  label: string
  url: string
}

export type RuntimeSwitchTargets = {
  dev: RuntimeSwitchTarget
  stable: RuntimeSwitchTarget
}

type RuntimeSwitchTargetOptions = {
  ports?: RuntimePortSettings | null
  currentPathname: string
  currentSearch?: string
  currentHash?: string
  currentRouterBasename: string
}

function normalizePort(value: number | null | undefined, fallback: number): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1 || value > 65535) {
    return fallback
  }
  return value
}

function withLocationSuffix(url: string, search = '', hash = ''): string {
  const target = new URL(url)
  target.search = search
  target.hash = hash
  return target.toString()
}

export function runtimeModeLabel(mode: RuntimeMode): string {
  return mode === 'dev' ? 'Vite dev web' : 'Stable /bms/ web'
}

export function buildRuntimeSwitchTargets(options: RuntimeSwitchTargetOptions): RuntimeSwitchTargets {
  const devPort = normalizePort(options.ports?.dev_web_host_port, 5173)
  const prodPort = normalizePort(options.ports?.prod_web_host_port, 18080)
  const appPath = getCurrentAppPath(options.currentPathname, options.currentRouterBasename)
  const devBaseUrl = `http://127.0.0.1:${devPort}/`
  const stableBaseUrl = `http://127.0.0.1:${prodPort}/bms/`

  return {
    dev: {
      mode: 'dev',
      label: runtimeModeLabel('dev'),
      url: withLocationSuffix(joinBrowserUrl(devBaseUrl, appPath), options.currentSearch, options.currentHash),
    },
    stable: {
      mode: 'container',
      label: runtimeModeLabel('container'),
      url: withLocationSuffix(joinBrowserUrl(stableBaseUrl, appPath), options.currentSearch, options.currentHash),
    },
  }
}
