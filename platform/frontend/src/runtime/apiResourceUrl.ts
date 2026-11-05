declare global {
  interface Window { __BMS_API_BASE_URL__?: string; }
}

/** Server resources bypass fetch/XHR shims when used by img, iframe or links. */
export function resolveApiResourceUrl(
  value: string,
  apiBaseUrl = typeof window === 'undefined' ? undefined : window.__BMS_API_BASE_URL__,
  pageHref = typeof window === 'undefined' ? undefined : window.location.href,
): string {
  if (!apiBaseUrl) return value;
  const base = apiBaseUrl.replace(/\/+$/, '');
  if (value === '/api' || value.startsWith('/api/')) return base + value;
  if (value.startsWith('api/')) return base + '/' + value;
  try {
    const page = new URL(pageHref ?? '');
    const url = new URL(value, page);
    if (url.origin === page.origin && (url.pathname === '/api' || url.pathname.startsWith('/api/'))) {
      return base + url.pathname + url.search + url.hash;
    }
  } catch { /* Retain external/non-URL resources unchanged. */ }
  return value;
}

