import { useEffect, useRef, useState } from 'react';

/** Display only. Neither discovery nor compiler values are scientific overrides. */
export type BC2LeafDisplay = { selectors: Record<string, unknown>; values: Record<string, unknown>; origins?: Record<string, string> };
export const bc2DisplaySelectors = (value: Record<string, unknown>) => Object.fromEntries(
  ['core', 'modality', 'target', 'humanize', 'protease_stable', 'disulfide_staple', 'mixed_topology', 'termini_together', 'termini_accessible', 'cyclize_peptide', 'forced_targeting', 'copies', 'oligomer_tie', 'relax_accepted_designs', 'trajectory_only'].filter(key => Object.hasOwn(value, key)).map(key => [key, value[key]])
);
export function bc2SelectorSignature(value: Record<string, unknown>): string {
  return JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))));
}
/** Used by the existing shell discovery owner; mounted-only pending coalescing. */
export function useBC2LeafDiscovery<T>(enabled: boolean, selectors: Record<string, unknown>) {
  const signature = bc2SelectorSignature(selectors);
  const pending = useRef(new Map<string, Promise<T>>());
  const [state, setState] = useState<{ signature: string; inventory?: T; error?: string }>();
  useEffect(() => {
    if (!enabled) return;
    let current = true;
    let request = pending.current.get(signature);
    if (!request) {
      request = fetch(`/api/models/bindcraft2/native-settings?selectors=${encodeURIComponent(signature)}`)
        .then(async response => { if (!response.ok) throw new Error(`Native settings discovery unavailable (${response.status})`); return (await response.json()).settings as T; });
      pending.current.set(signature, request);
      const remove = () => { if (pending.current.get(signature) === request) pending.current.delete(signature); };
      void request.then(remove, remove);
    }
    void request.then(inventory => { if (current) setState({ signature, inventory }); }, error => { if (current) setState({ signature, error: String(error.message ?? error) }); });
    return () => { current = false; };
  }, [enabled, signature]);
  return state?.signature === signature ? state : undefined;
}
