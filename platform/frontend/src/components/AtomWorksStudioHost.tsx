import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { captureStudio, isStudioMessage, studioFrameUrl } from '../lib/atomworksStudio';
import type { StudioCapture } from '../lib/atomworksStudio';

export interface AtomWorksStudioHostProps {
  sessionId: string;
  onDirty?: () => void;
  onReady?: () => void;
}
/** Parent uses ref.capture() for Save/Template/Project/Generate, then adopts those exact bytes. */
export const AtomWorksStudioHost = forwardRef<StudioCapture, AtomWorksStudioHostProps>(
  function AtomWorksStudioHost({ sessionId, onDirty, onReady }, ref) {
    const frame = useRef<HTMLIFrameElement>(null);
    const generation = useRef(0);
    const selection = useRef(0);
    const [error, setError] = useState<string | null>(null);
    const [expanded, setExpanded] = useState(false);
    // Render-time invalidation also covers A -> B -> A before an old export finishes.
    const previous = useRef(sessionId);
    if (previous.current !== sessionId) { previous.current = sessionId; generation.current++; selection.current++; }
    useEffect(() => () => { generation.current++; selection.current++; }, []);
    useImperativeHandle(ref, () => ({ generation: () => generation.current, capture: async () => {
      const started = selection.current;
      try {
        const snapshot = await captureStudio(sessionId);
        if (started !== selection.current) throw new Error('Studio document changed during capture');
        setError(null); return snapshot;
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : String(cause);
        if (started === selection.current) setError(message);
        throw cause;
      }
    } }), [sessionId]);
    const framed = useRef<string | null>(null);
    useEffect(() => {
      const receive = (event: MessageEvent) => {
        if (!isStudioMessage(event, frame.current?.contentWindow ?? null, window.location.origin, sessionId)) return;
        if (event.data.type === 'ready') {
          // A restored session keeps the previous camera, so a fresh frame shows
          // an empty viewport. Studio's host API reopens the same session and
          // frames it. Once per session: openSession announces ready again.
          if (framed.current !== sessionId) {
            framed.current = sessionId;
            try { (frame.current?.contentWindow as (Window & { atomworksStudio?: { openSession?: (id: string) => unknown } }) | null)
              ?.atomworksStudio?.openSession?.(sessionId); } catch { /* the editor stays usable without framing */ }
          }
          onReady?.();
        }
        else { generation.current++; onDirty?.(); }
      };
      window.addEventListener('message', receive);
      return () => window.removeEventListener('message', receive);
    }, [sessionId, onDirty, onReady]);
    return <section aria-label="AtomWorks Studio editor" style={expanded ? { position: 'fixed', inset: 0, zIndex: 1000, background: 'var(--bg-primary)', overflow: 'auto', padding: 8 } : { minWidth: 0 }}>
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}
        style={{ minHeight: 44, padding: '8px 12px' }}>{expanded ? 'Return to form' : 'Expand molecular editor'}</button>
      {error && <p role="alert">Unsaved Studio edits: {error}. Existing saved source is unchanged; retry capture.</p>}
      <div style={{ overflowX: 'auto' }}>
      <iframe key={sessionId} ref={frame} title="AtomWorks Studio"
        src={studioFrameUrl(sessionId, window.location.origin)}
        style={{ width: '100%', minWidth: 320, height: expanded ? 'calc(100dvh - 68px)' : 'max(70vh, 800px)', minHeight: expanded ? 650 : undefined, border: 0 }} />
      </div>
    </section>;
  });
