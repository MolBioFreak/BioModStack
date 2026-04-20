type MaybePath = string | null | undefined

type RouterBasenameOptions = {
  injectedBasename?: MaybePath
  envBaseUrl?: MaybePath
}

type RouterBasenameGlobal = {
  __BMS_ROUTER_BASENAME__?: unknown
}

function normalizeAppPath(path: MaybePath): string {
  const trimmed = path?.trim()
  if (!trimmed || trimmed === '/') {
    return '/'
  }

  const withLeadingSlash = trimmed.startsWith('/') ? trimmed : `/${trimmed}`
  const withoutTrailingSlash = withLeadingSlash.replace(/\/+$/, '')
  return withoutTrailingSlash || '/'
}

function normalizeRouterBasename(path: MaybePath): string {
  const normalizedPath = normalizeAppPath(path)
  if (normalizedPath === '/') {
    return '/'
  }
  return `${normalizedPath}/`
}

function getInjectedRouterBasename(): string | undefined {
  if (typeof globalThis === 'undefined') {
    return undefined
  }

  const injectedBasename = (globalThis as RouterBasenameGlobal).__BMS_ROUTER_BASENAME__
  return typeof injectedBasename === 'string' ? injectedBasename : undefined
}

function joinAppPath(basename: string, appPath: string): string {
  if (basename === '/') {
    return appPath
  }
  if (appPath === '/') {
    return basename
  }
  return `${basename.slice(0, -1)}${appPath}`
}

export function getRouterBasename(options: RouterBasenameOptions = {}): string {
  const basenameCandidate = options.injectedBasename ?? getInjectedRouterBasename() ?? options.envBaseUrl
  return normalizeRouterBasename(basenameCandidate)
}

export function getCurrentAppPath(pathname: string, basename: string = getRouterBasename()): string {
  const normalizedPathname = normalizeAppPath(pathname)
  const normalizedBasename = normalizeRouterBasename(basename)

  if (normalizedBasename === '/') {
    return normalizedPathname
  }

  const basenamePrefix = normalizedBasename.slice(0, -1)
  if (normalizedPathname === basenamePrefix) {
    return '/'
  }
  if (!normalizedPathname.startsWith(`${basenamePrefix}/`)) {
    return normalizedPathname
  }

  const appPath = normalizedPathname.slice(basenamePrefix.length)
  return normalizeAppPath(appPath)
}

export function isAppPath(pathname: string, appPath: string, basename: string = getRouterBasename()): boolean {
  const currentPath = getCurrentAppPath(pathname, basename)
  const normalizedAppPath = normalizeAppPath(appPath)

  if (normalizedAppPath === '/') {
    return currentPath === '/'
  }

  return currentPath === normalizedAppPath || currentPath.startsWith(`${normalizedAppPath}/`)
}

export function joinBrowserUrl(browserUrl: string, appPath: string): string {
  const url = new URL(browserUrl)
  const basename = normalizeRouterBasename(url.pathname)
  const normalizedAppPath = normalizeAppPath(appPath)

  url.pathname = joinAppPath(basename, normalizedAppPath)
  return url.toString()
}
