/** Editor transport and immutable single-response capture, not durable custody. */
export const STUDIO_BASE = '/api/atomworks-studio';
export interface StudioSession { session_id: string; structure: { revision: number; [key: string]: unknown } }
export interface StudioSnapshot {
  session_id: string; revision: number; name: string; cif: string;
  issues: Array<Record<string, string>>;
}
export interface StudioCapture {
  capture: () => Promise<StudioSnapshot>;
  /** Monotonic mount-local session/edit generation; never a native revision. */
  generation: () => number;
}
export function studioFrameUrl(sessionId: string, origin: string): string {
  return `${STUDIO_BASE}/?${new URLSearchParams({ session: sessionId, embed: '1', parent_origin: origin })}`;
}
export function isStudioMessage(event: MessageEvent, frameWindow: Window | null,
  origin: string, sessionId: string): boolean {
  return frameWindow !== null && event.origin === origin && event.source === frameWindow
    && event.data?.source === 'atomworks-annotation-studio'
    && event.data?.session_id === sessionId
    && ['ready', 'changed', 'example'].includes(event.data?.type);
}
async function responseJson(response: Response) {
  if (!response.ok) throw new Error(`Studio request failed (${response.status})`);
  return response.json();
}
export async function importStudio(file: File, options: Record<string, string> = {}): Promise<StudioSession> {
  const body = new FormData(); body.append('file', file);
  for (const [key, value] of Object.entries(options)) body.append(key, value);
  return responseJson(await fetch(`${STUDIO_BASE}/api/v1/sessions`, {
    method: 'POST', credentials: 'same-origin', cache: 'no-store', body,
  }));
}
export async function captureStudio(sessionId: string): Promise<StudioSnapshot> {
  // /export is one locked snapshot, including its revision and issues. Never /cif.
  const snapshot = await responseJson(await fetch(
    `${STUDIO_BASE}/api/v1/sessions/${encodeURIComponent(sessionId)}/export`,
    { method: 'POST', credentials: 'same-origin', cache: 'no-store' }));
  return { ...snapshot, session_id: sessionId };
}
