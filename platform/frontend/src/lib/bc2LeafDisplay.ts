import { useEffect, useRef, useState } from 'react';
import type { BC2Display, BC2Inventory } from './bindcraft2Types';

/** Discovery and compiler values never become scientific overrides. */
export type BC2LeafDisplay = BC2Display;
export function bc2DisplaySelectors(value: Record<string, unknown>, inventory?: BC2Inventory | null) {
  // The model owns the complete pure-resolver selector surface. Before discovery,
  // only its three stable profile selectors are known; no scientific merge occurs.
  const fields = inventory?.display_selector_fields ?? ['core', 'modality', 'target', ...Object.keys(inventory?.presets.property ?? {})];
  return Object.fromEntries(fields.filter(key => Object.hasOwn(value, key)).map(key => [key, value[key]]));
}
export function bc2SelectorSignature(value: Record<string, unknown>): string {
  return JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))));
}
/** One mounted discovery owner, with coalesced pending reads and stale commits discarded. */
export function useBC2LeafDiscovery<T>(enabled: boolean, selectors: Record<string, unknown>) {
  const signature = bc2SelectorSignature(selectors);
  const pending = useRef(new Map<string, Promise<{ inventory: T; launchAvailable: boolean }>>());
  const [state, setState] = useState<{ signature: string; inventory?: T; launchAvailable?: boolean; error?: string }>();
  useEffect(() => {
    if (!enabled) return;
    let current = true;
    let request = pending.current.get(signature);
    if (!request) {
      request = fetch(`/api/models/bindcraft2/native-settings?selectors=${encodeURIComponent(signature)}`)
        .then(async response => {
          if (!response.ok) throw new Error(`Inherited value unavailable (${response.status}).`);
          const data = await response.json();
          if (data.model_id !== 'bindcraft2' || !data.settings?.fields) throw new Error('Inherited value unavailable: invalid discovery response.');
          return { inventory: data.settings as T, launchAvailable: data.launch_available === true };
        });
      pending.current.set(signature, request);
      const remove = () => { if (pending.current.get(signature) === request) pending.current.delete(signature); };
      void request.then(remove, remove);
    }
    void request.then(result => { if (current) setState({ signature, ...result }); }, error => { if (current) setState({ signature, error: String(error.message ?? error) }); });
    return () => { current = false; };
  }, [enabled, signature]);
  return state?.signature === signature ? state : undefined;
}
