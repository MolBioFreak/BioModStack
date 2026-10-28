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
    useEffect(() => {
      const receive = (event: MessageEvent) => {
        if (!isStudioMessage(event, frame.current?.contentWindow ?? null, window.location.origin, sessionId)) return;
        if (event.data.type === 'ready') onReady?.();
        else { generation.current++; onDirty?.(); }
      };
      window.addEventListener('message', receive);
      return () => window.removeEventListener('message', receive);
    }, [sessionId, onDirty, onReady]);
    return <section aria-label="AtomWorks Studio editor">
      {error && <p role="alert">Unsaved Studio edits: {error}. Existing saved source is unchanged; retry capture.</p>}
      <iframe key={sessionId} ref={frame} title="AtomWorks Studio"
        src={studioFrameUrl(sessionId, window.location.origin)}
        style={{ width: '100%', height: '70vh', border: 0 }} />
    </section>;
  });
