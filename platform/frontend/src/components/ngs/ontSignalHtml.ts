import { secureNgsHtml } from '../../lib/ngsAlignmentViewer';

/** Keep the native report's confinement while enabling Bokeh CustomJS callbacks.
 * Older Squigualiser wrappers embed a second CSP. Policies intersect, so adding
 * unsafe-eval only to our outer policy still breaks label updates on pan/zoom.
 * This is exclusively for governed native Bokeh reports in allow-scripts-only
 * opaque-origin frames, never for an application document or arbitrary HTML.
 */
export function secureOntSignalHtml(source: string, csp: string): Blob {
    const native = new DOMParser().parseFromString(source, 'text/html');
    for (const meta of native.querySelectorAll<HTMLMetaElement>('meta[http-equiv]')) {
        if (meta.httpEquiv.toLowerCase() !== 'content-security-policy') continue;
        meta.content = meta.content.replace(
            /(^|;)\s*script-src\s+'unsafe-inline'\s*(?=;|$)/g,
            "$1 script-src 'unsafe-inline' 'unsafe-eval'",
        );
    }
    return secureNgsHtml(native.documentElement.outerHTML, csp);
}
