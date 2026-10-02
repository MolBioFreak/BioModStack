import { useEffect, useRef, useState } from 'react';
import type { AssembleTask, WorkflowResult } from '../../../../lib/goldenGateWorkflowTypes';
import { batchCount, captureAlternative, designGoldenGateBatch, editAlternative, type BatchSlot, type BatchScope, type BatchResultEvent, type BatchProgress } from '../../../../lib/goldenGateBatch';
import { Choice, Num, Text } from './Fields';

export function GoldenGateBatchPanel({ base, slots, scope, onSlots, onScope, onEdit, onResult }: {
  base: AssembleTask; slots: BatchSlot[]; scope: BatchScope;
  onSlots: (slots: BatchSlot[]) => void; onScope: (scope: BatchScope) => void;
  onEdit: (request: AssembleTask) => void; onResult: (result: WorkflowResult) => void;
}) {
  const [partId, setPartId] = useState('');
  const [label, setLabel] = useState('Alternative 1');
  const [results, setResults] = useState<BatchResultEvent[]>([]);
  const [progress, setProgress] = useState<BatchProgress | null>(null);
  const [status, setStatus] = useState('Not run');
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);
  const runId = useRef(0);
  useEffect(() => () => { active.current?.abort(); runId.current++; }, []);
  const capture = () => {
    try {
      const alternative = captureAlternative(base, partId, label);
      const slot = slots.find(s => s.part_id === partId);
      onSlots(slot ? slots.map(s => s === slot ? { ...s, alternatives: [...s.alternatives.filter(a => a.id !== label), alternative] } : s)
        : [...slots, { part_id: partId, alternatives: [alternative] }]);
      setStatus(`Captured ${label}. Existing same-name alternative is replaced only in this slot.`);
    } catch (e) { setStatus(String(e)); }
  };
  const run = async () => {
    active.current?.abort(); const controller = new AbortController(); active.current = controller;
    const id = ++runId.current;
    setResults([]); setErrors([]); setProgress(null); setBusy(true); setStatus('Running explicit batch snapshot');
    try {
      await designGoldenGateBatch(structuredClone({ schema_version: 'bms.golden-gate-batch.v1', base, slots, scope }), event => {
        if (id !== runId.current || controller.signal.aborted) return;
        setProgress(event.progress);
        if (event.event === 'result') setResults(previous => [...previous, event]);
        if (event.event === 'error') setErrors(previous => [...previous, `Combination ${event.index}: ${event.message}`]);
        if (event.event === 'finished') setStatus(`Finished: ${event.coverage} coverage`);
      }, controller.signal);
    } catch (e) { if (id === runId.current && !controller.signal.aborted) setStatus(String(e)); }
    finally { if (id === runId.current) setBusy(false); }
  };
  return <details aria-label="Golden Gate combinatorial batch"><summary>Combinatorial slot alternatives</summary>
    <p>Use the native part/source/preparation controls above, then capture each named alternative. To revise one, load it into those controls and capture under the same name. Unvaried parts and the current enzyme, target, Tm, fidelity and reaction settings are shared by this stage. Each chosen alternative retains its source, orientation, role, preparation, automatic primer, edit and reaction-part settings. Different material must use distinct source IDs.</p>
    <Choice label="Batch part slot" value={partId} values={['', ...base.parts.map(p => p.id)]} onChange={setPartId}/>
    <Text label="Batch alternative name" value={label} onChange={setLabel}/>
    <button onClick={capture}>Capture current part alternative</button>
    {slots.map(slot => <fieldset key={slot.part_id}><legend>Slot {slot.part_id}</legend>
      {slot.alternatives.map(a => <div key={a.id}>{a.id}: {a.source.id} · {a.part.preparation.kind} · {a.part.orientation}
        <button onClick={() => { setPartId(slot.part_id); setLabel(a.id); onEdit(editAlternative(base, a)); }}>Edit alternative {slot.part_id} / {a.id}</button>
        <button onClick={() => onSlots(slots.flatMap(s => s !== slot ? [s] : s.alternatives.length === 1 ? [] : [{ ...s, alternatives: s.alternatives.filter(x => x !== a) }]))}>Remove alternative {slot.part_id} / {a.id}</button>
      </div>)}<button onClick={() => onSlots(slots.filter(s => s !== slot))}>Remove batch slot {slot.part_id}</button>
    </fieldset>)}
    <Choice label="Batch coverage" value={scope.mode} values={['full', 'sampled']} onChange={mode => onScope(mode === 'full' ? {mode} : {mode, count: 1, seed: 0})}/>
    {scope.mode === 'sampled' && <><Num label="Sample combination count" integer min={1} value={scope.count} onChange={count => onScope({...scope, count: count ?? 1})}/><Num label="Sample seed" integer value={scope.seed} onChange={seed => onScope({...scope, seed: seed ?? 0})}/><p>Explicit deterministic sampling without replacement (python-random-floyd-v1). Slot and alternative order define indices; the last slot varies fastest.</p></>}
    <p>Authoring domain: {slots.length ? batchCount(slots).toString() : '0'} possible combinations. {scope.mode === 'full' ? 'Full Cartesian enumeration selected; no implicit sampling.' : `${scope.count} explicitly sampled combinations selected.`}</p>
    <button disabled={!slots.length} onClick={() => void run()}>{busy ? 'Restart combinatorial batch' : 'Run combinatorial batch'}</button>
    {busy && <button onClick={() => { active.current?.abort(); runId.current++; setBusy(false); setStatus('Cancelled delivery: incomplete coverage. Already delivered results retained. The active native calculation may finish; disconnect is checked between combinations.'); }}>Cancel batch delivery</button>}
    <p role="status">{status}</p>
    {progress && <p aria-label="Batch delivered scope">Run snapshot: total {progress.total}; selected {progress.selected}; evaluated {progress.evaluated}; completed {progress.completed}; omitted by selection {progress.omitted}; selected not yet evaluated {progress.remaining}. Delivered results: {results.length}.</p>}
    {errors.map((e, i) => <p key={i}>{e}</p>)}
    {results.map(r => <button key={r.index} onClick={() => onResult(r.result)}>View batch combination {r.index}: {r.alternatives.join(' / ')}</button>)}
    <p>Each result opens the existing candidate viewer and fixed Save/export controls. Results remain in this panel until an explicit new batch run or leaving the workspace; Save selected candidates for durable retention. No scheduler or pooled-library score.</p>
  </details>;
}
