import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { compileCoreRequest, reusableDigestChoices, type GoldenGateDraft, type GoldenGateDesignRequest, type NativeScienceSettings, type GoldenGateDesignResult, type FidelityResult, type Solution } from '../../../../lib/goldenGateDesign';
import { GoldenGatePartsEditor, type GoldenGatePartsEditorProps } from './GoldenGatePartsEditor';
const Results = lazy(() => import('./GoldenGateDesignResults').then(m => ({ default: m.GoldenGateDesignResults })));

export interface GoldenGateDesignEditorProps extends Omit<GoldenGatePartsEditorProps, 'value' | 'onChange' | 'digests'> {
  /** Immutable controlled value. Any edit/clone/reopen supplies a new object. */
  value: GoldenGateDraft;
  onChange: (v: GoldenGateDraft) => void;
  /** Native leaf callback arguments, NOT a proposed HTTP request body. */
  onDesign: (core: GoldenGateDesignRequest, science: NativeScienceSettings, signal: AbortSignal) => Promise<{ core: GoldenGateDesignResult; fidelity?: FidelityResult | null }>;
  /** Persistence is never automatically aborted. Parent pairs authoritative save. */
  onSave?: (core: GoldenGateDesignRequest, science: NativeScienceSettings, selectedSolutionId: string) => Promise<{ label: string }>;
  onSaved?: (ack: { label: string }) => void;
  onLoadProduct?: (product: Solution) => void;
  renderSequence?: (product: Solution) => React.ReactNode;
  /** Compose native settings editors here; their changes must call onChange. */
  children?: React.ReactNode;
}

type Receipt = { owner: GoldenGateDraft; result: GoldenGateDesignResult; fidelity: FidelityResult | null; selected: string | null; request: GoldenGateDesignRequest; science: NativeScienceSettings };
export function GoldenGateDesignEditor({ value, onChange, onDesign, onSave, onSaved, onLoadProduct, renderSequence, children, ...partsProps }: GoldenGateDesignEditorProps) {
  const current = useRef(value); current.current = value;
  const mounted = useRef(true);
  const generation = useRef(0);
  const active = useRef<AbortController | null>(null);
  const saveGeneration = useRef(0);
  const selectedRef = useRef<string | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [busyOwner, setBusyOwner] = useState<GoldenGateDraft | null>(null);
  const [savingOwner, setSavingOwner] = useState<GoldenGateDraft | null>(null);
  const [message, setMessage] = useState<{ owner: GoldenGateDraft; text: string } | null>(null);
  useEffect(() => { active.current?.abort(); generation.current += 1; }, [value]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; active.current?.abort(); generation.current += 1; saveGeneration.current += 1; }; }, []);
  const visible = receipt?.owner === value ? receipt : null;
  const design = async () => {
    saveGeneration.current += 1;
    setSavingOwner(null);
    active.current?.abort();
    const owner = value; const id = ++generation.current;
    const controller = new AbortController(); active.current = controller;
    setMessage(null);
    try {
      const request = compileCoreRequest(value.core);
      // Reject incomplete numeric controls; never coerce unknown NaN to JSON null.
      const finite = (x: unknown): boolean => typeof x === 'number' ? Number.isFinite(x) : x === null || typeof x !== 'object' ? true : Object.values(x).every(finite);
      if (!finite(value.science)) throw new Error('Complete the numeric scientific settings or select unknown for optional quantities.');
      const science = structuredClone(value.science);
      setBusyOwner(owner);
      const result = await onDesign(request, science, controller.signal);
      if (!mounted.current || current.current !== owner || generation.current !== id || controller.signal.aborted) return;
      saveGeneration.current += 1;
      setSavingOwner(null);
      selectedRef.current = result.core.selected_solution_id;
      setReceipt({ owner, result: result.core, fidelity: result.fidelity ?? null, selected: result.core.selected_solution_id, request, science });
    } catch (error) {
      if (mounted.current && current.current === owner && generation.current === id && !controller.signal.aborted) setMessage({ owner, text: error instanceof Error ? error.message : String(error) });
    } finally {
      if (mounted.current && current.current === owner && generation.current === id) setBusyOwner(null);
    }
  };
  const save = async () => {
    if (!visible?.selected || !onSave) return;
    const owner = value; const selected = visible.selected; const run = ++saveGeneration.current;
    setSavingOwner(owner); setMessage(null);
    try {
      const ack = await onSave(structuredClone(visible.request), structuredClone(visible.science), selected);
      if (mounted.current && current.current === owner && saveGeneration.current === run && selectedRef.current === selected) { setMessage({ owner, text: ack.label }); onSaved?.(ack); }
    } catch (error) {
      if (mounted.current && current.current === owner && saveGeneration.current === run && selectedRef.current === selected) setMessage({ owner, text: error instanceof Error ? error.message : String(error) });
    } finally { if (mounted.current && current.current === owner && saveGeneration.current === run) setSavingOwner(null); }
  };
  return <div className="space-y-4 text-slate-200"><GoldenGatePartsEditor {...partsProps} value={value.core} onChange={core => onChange({ ...value, core })} digests={receipt ? reusableDigestChoices(receipt.owner.core, value.core, receipt.result.digests) : []} />{children}<p>Only an explicit Design action invokes computation. Native science controls are passed separately; the receiving workflow must declare which operations it supports.</p><button type="button" onClick={() => void design()}>{busyOwner === value ? 'Restart design' : 'Design explicit parts'}</button>{busyOwner === value && <button type="button" onClick={() => { active.current?.abort(); generation.current += 1; setBusyOwner(null); }}>Cancel preview</button>}{message?.owner === value && <p role="status">{message.text}</p>}{visible && <Suspense fallback={<p>Loading result viewer…</p>}><Results result={visible.result} fidelity={visible.fidelity} selectedSolutionId={visible.selected} onSelect={selected => { if (!visible.result.solutions.some(s => s.id === selected)) return; selectedRef.current = selected; saveGeneration.current += 1; setSavingOwner(null); setMessage(null); setReceipt({ ...visible, selected }); }} onLoadProduct={onLoadProduct} renderSequence={renderSequence} />{onSave && <button type="button" disabled={!visible.selected || savingOwner === value} onClick={() => void save()}>{savingOwner === value ? 'Saving selected design…' : 'Save selected design'}</button>}</Suspense>}</div>;
}
