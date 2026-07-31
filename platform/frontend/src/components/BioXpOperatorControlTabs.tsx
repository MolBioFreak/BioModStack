import { useEffect, useMemo, useState } from 'react';

import {
    type BioXpOperatorActionReceipt,
    type BioXpOperatorActionSpec,
    bioXpErrorText,
    useAssessBioXpOperatorAction,
    useBioXpOperatorActionAdmission,
    useBioXpOperatorActionHistory,
    useBioXpOperatorControlCatalog,
    useInvokeBioXpOperatorAction,
} from '../lib/bioxpClient';

type Pane = 'primitive' | 'meta' | 'logs';

const paneClass = (active: boolean) => `rounded px-4 py-2 text-sm font-semibold ${active ? 'bg-cyan-700 text-white' : 'bg-slate-800 text-slate-300'}`;
const safetyTone: Record<BioXpOperatorActionSpec['safety_class'], string> = {
    read_only: 'border-sky-700/60 bg-sky-950/20',
    service: 'border-violet-700/60 bg-violet-950/20',
    motion: 'border-amber-700/60 bg-amber-950/20',
    stop: 'border-orange-700/60 bg-orange-950/20',
    emergency: 'border-red-700/70 bg-red-950/30',
};

type PrimitiveGroup = 'critical' | 'motion-power' | 'transport-evidence' | 'safety-recovery' | 'initialization' | 'all' | string;

function criticalGroup(action: BioXpOperatorActionSpec): Exclude<PrimitiveGroup, 'critical' | 'all'> | null {
    const path = action.informational_path.toLowerCase();
    if (action.subsystem === 'motion.power') return 'motion-power';
    if (path === '/reconnect' || path.includes('/maintenance/usb') || path.includes('/hardware/snapshot')) return 'transport-evidence';
    if (action.safety_class === 'stop' || action.safety_class === 'emergency' || path.includes('/recover') || path.includes('/strict_startup')) return 'safety-recovery';
    if (path.includes('/initial') || path.includes('/startup')) return 'initialization';
    return null;
}

function isCriticalAction(action: BioXpOperatorActionSpec): boolean {
    return criticalGroup(action) !== null;
}

function initialInputs(action: BioXpOperatorActionSpec | undefined): Record<string, unknown> {
    if (!action) return {};
    return Object.fromEntries(action.inputs.flatMap((input) => input.default === null || input.default === undefined
        ? []
        : [[input.name, input.default]]));
}

function normalizeInput(action: BioXpOperatorActionSpec, values: Record<string, unknown>): Record<string, unknown> {
    const result: Record<string, unknown> = {};
    for (const input of action.inputs) {
        const value = values[input.name];
        if (value === '' || value === undefined || value === null) {
            if (input.required) throw new Error(`${input.label} is required.`);
            continue;
        }
        if (input.value_type === 'integer') {
            const parsed = Number(value);
            if (!Number.isSafeInteger(parsed)) throw new Error(`${input.label} must be an integer.`);
            result[input.name] = parsed;
        } else if (input.value_type === 'number') {
            const parsed = Number(value);
            if (!Number.isFinite(parsed)) throw new Error(`${input.label} must be a finite number.`);
            result[input.name] = parsed;
        } else if (input.value_type === 'boolean') {
            result[input.name] = value === true;
        } else if (input.value_type === 'json') {
            result[input.name] = typeof value === 'string' ? JSON.parse(value) : value;
        } else {
            result[input.name] = String(value);
        }
    }
    return result;
}

function ReceiptCard({ receipt }: { receipt: BioXpOperatorActionReceipt }) {
    const terminalPass = receipt.machine_assessment === 'pass' || receipt.operator_assessment === 'pass';
    const terminalFail = receipt.machine_assessment === 'fail' || receipt.operator_assessment === 'fail';
    return (
        <article className={`rounded border p-3 text-xs ${terminalPass ? 'border-emerald-700/60' : terminalFail ? 'border-red-700/60' : 'border-slate-700'}`}>
            <div className="flex flex-wrap justify-between gap-2">
                <span className="font-mono text-cyan-200">{receipt.action_id}</span>
                <span>{receipt.status} · machine={receipt.machine_assessment} · operator={receipt.operator_assessment ?? 'unreviewed'}</span>
            </div>
            <p className="mt-1 font-mono text-slate-400">{receipt.command_id} · generation {receipt.ownership_generation}</p>
            <p className="mt-1 text-slate-300">remote_acknowledged={String(receipt.remote_acknowledged)} · physical_effect_verified={String(receipt.physical_effect_verified)} · duration_ms={receipt.duration_ms ?? 'pending'}</p>
            {receipt.error && <p className="mt-1 text-red-300">{receipt.error}</p>}
            {receipt.operator_note && <p className="mt-1 text-slate-300">Operator: {receipt.operator_note}</p>}
            {receipt.stage_receipts.length > 0 && (
                <details className="mt-2"><summary>Stage receipts ({receipt.stage_receipts.length})</summary><pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap text-[11px] text-slate-400">{JSON.stringify(receipt.stage_receipts, null, 2)}</pre></details>
            )}
            {receipt.response && (
                <details className="mt-2"><summary>Bounded response</summary><pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap text-[11px] text-slate-400">{JSON.stringify(receipt.response, null, 2)}</pre></details>
            )}
        </article>
    );
}

export function BioXpOperatorControlTabs({ generation, connected }: { generation: number; connected: boolean }) {
    const catalogQuery = useBioXpOperatorControlCatalog(connected);
    const historyQuery = useBioXpOperatorActionHistory(connected);
    const invoke = useInvokeBioXpOperatorAction();
    const assess = useAssessBioXpOperatorAction();
    const [pane, setPane] = useState<Pane>('primitive');
    const [subsystemFilter, setSubsystemFilter] = useState<PrimitiveGroup>('all');
    const [actionSearch, setActionSearch] = useState('');
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [inputs, setInputs] = useState<Record<string, unknown>>({});
    const [localError, setLocalError] = useState<string | null>(null);
    const [assessmentNote, setAssessmentNote] = useState('');

    const primitiveActions = useMemo(
        () => (catalogQuery.error ? [] : (catalogQuery.data?.actions ?? [])).filter((action) => action.kind === 'primitive'),
        [catalogQuery.data?.actions, catalogQuery.error],
    );
    const subsystemOptions = useMemo(
        () => [...new Set(primitiveActions.map((action) => action.subsystem))].sort(),
        [primitiveActions],
    );
    const criticalActions = useMemo(() => primitiveActions.filter(isCriticalAction), [primitiveActions]);
    const browseActions = useMemo(() => {
        let rows = primitiveActions;
        if (subsystemFilter === 'motion-power') rows = rows.filter((action) => criticalGroup(action) === 'motion-power');
        else if (subsystemFilter === 'transport-evidence') rows = rows.filter((action) => criticalGroup(action) === 'transport-evidence');
        else if (subsystemFilter === 'safety-recovery') rows = rows.filter((action) => criticalGroup(action) === 'safety-recovery');
        else if (subsystemFilter === 'initialization') rows = rows.filter((action) => criticalGroup(action) === 'initialization');
        else if (subsystemFilter !== 'all') rows = rows.filter((action) => action.subsystem === subsystemFilter);
        const query = actionSearch.trim().toLowerCase();
        if (query) rows = rows.filter((action) => [action.label, action.action_id, action.subsystem, action.informational_path].some((value) => value.toLowerCase().includes(query)));
        return rows;
    }, [actionSearch, primitiveActions, subsystemFilter]);
    const groupedBrowseActions = useMemo(
        () => [...new Set(browseActions.map((action) => action.subsystem))].sort().map((subsystem) => ({
            subsystem,
            actions: browseActions.filter((action) => action.subsystem === subsystem),
        })),
        [browseActions],
    );
    const paneActions = pane === 'meta'
        ? (catalogQuery.data?.actions ?? []).filter((action) => action.kind === pane)
        : pane === 'primitive' ? primitiveActions : [];
    const selected = paneActions.find((action) => action.action_id === selectedId) ?? paneActions[0];
    const normalizedForAdmission = useMemo(() => {
        if (!selected) return null;
        try {
            return normalizeInput(selected, inputs);
        } catch {
            return null;
        }
    }, [selected, inputs]);
    const admission = useBioXpOperatorActionAdmission(
        selected?.action_id ?? null,
        generation,
        catalogQuery.data?.ownership_generation ?? 0,
        normalizedForAdmission,
        connected,
    );
    const actionEnabled = admission.error ? false : (admission.data?.enabled ?? (selected ? selected.enabled : false));
    const disabledReason = admission.data?.disabled_reason ?? (selected ? selected.disabled_reason : null) ?? 'Robot did not admit this action.';
    const dependencies = admission.data?.dependencies ?? (selected ? selected.dependencies : []);
    const latestReceipt = invoke.data ?? assess.data ?? historyQuery.data?.receipts[0];
    const isSafetyInterrupt = selected?.safety_class === 'stop' || selected?.safety_class === 'emergency';
    const sourceAuthorityAllowsAction = catalogQuery.data?.source_authority_verified === true || isSafetyInterrupt;

    useEffect(() => {
        if (selected && selected.action_id !== selectedId) setSelectedId(selected.action_id);
    }, [selected, selectedId]);
    useEffect(() => setInputs(initialInputs(selected)), [selected?.action_id]);

    const run = () => {
        if (!selected) return;
        setLocalError(null);
        try {
            const normalized = normalizeInput(selected, inputs);
            if (selected.requires_confirmation && !window.confirm(`Run ${selected.label}?\n\n${selected.description}`)) return;
            invoke.mutate({
                actionId: selected.action_id,
                connectionGeneration: generation,
                ownershipGeneration: catalogQuery.data?.ownership_generation ?? 0,
                inputs: normalized,
            });
        } catch (error) {
            setLocalError(error instanceof Error ? error.message : String(error));
        }
    };

    const recordAssessment = (receipt: BioXpOperatorActionReceipt, verdict: 'pass' | 'fail') => {
        const note = assessmentNote.trim();
        if (!note) {
            setLocalError('A non-empty operator observation is required for PASS/FAIL.');
            return;
        }
        assess.mutate({
            commandId: receipt.command_id,
            connectionGeneration: generation,
            ownershipGeneration: catalogQuery.data?.ownership_generation ?? 0,
            verdict,
            note,
        });
    };

    const contractError = catalogQuery.error ? bioXpErrorText(catalogQuery.error) : null;
    const actionError = invoke.error ? bioXpErrorText(invoke.error) : assess.error ? bioXpErrorText(assess.error) : localError;

    return (
        <section className="rounded-xl border border-cyan-800/60 bg-slate-950/70 p-4" data-bioxp-operator-control-tabs>
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h2 className="text-lg font-semibold">OEM Route Control Plane</h2>
                    <p className="text-sm text-slate-400">Robot-owned action catalog; one tab and one auditable receipt per action. Meta actions execute only robot-owned stage sequences.</p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <button type="button" className={paneClass(pane === 'primitive')} onClick={() => setPane('primitive')}>Individual Controls</button>
                    <button type="button" className={paneClass(pane === 'meta')} onClick={() => setPane('meta')}>Meta Actions</button>
                    <button type="button" className={paneClass(pane === 'logs')} onClick={() => setPane('logs')}>Logs</button>
                </div>
            </div>

            {catalogQuery.data && (
                <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-5">
                    <div><dt className="text-slate-500">machine</dt><dd>{catalogQuery.data.machine_serial}</dd></div>
                    <div><dt className="text-slate-500">actions</dt><dd>{catalogQuery.data.actions.length}</dd></div>
                    <div><dt className="text-slate-500">source authority</dt><dd>{String(catalogQuery.data.source_authority_verified)}</dd></div>
                    <div><dt className="text-slate-500">ownership generation</dt><dd>{catalogQuery.data.ownership_generation}</dd></div>
                    <div><dt className="text-slate-500">BMS generation</dt><dd>{generation}</dd></div>
                </dl>
            )}
            {contractError && <p className="mt-3 text-sm text-red-300">Catalog unavailable: {contractError}</p>}

            {pane === 'logs' ? (
                <div className="mt-4 space-y-2">
                    {(historyQuery.data?.receipts ?? []).map((receipt) => <ReceiptCard key={receipt.command_id} receipt={receipt} />)}
                    {historyQuery.data?.receipts.length === 0 && <p className="text-sm text-slate-400">No robot-owned action receipts yet.</p>}
                </div>
            ) : (
                <>
                    {pane === 'primitive' ? (
                        <>
                            <section className="mt-4 rounded border border-cyan-800/60 bg-cyan-950/20 p-3" data-critical-controls>
                                <h3 className="font-semibold text-cyan-100">Critical Controls</h3>
                                <p className="mt-1 text-xs text-slate-400">Always visible robot-published power, transport/evidence, safety/recovery, and initialization actions.</p>
                                <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                                    {criticalActions.map((action) => (
                                        <button key={action.action_id} type="button" data-action-id={action.action_id} onClick={() => setSelectedId(action.action_id)} className={`rounded border px-3 py-2 text-left text-xs ${selected?.action_id === action.action_id ? 'border-cyan-400 bg-cyan-950/70' : 'border-slate-700 bg-slate-900'}`}>
                                            <span className="block font-semibold">{action.label}</span>
                                            <span className="text-slate-500">{criticalGroup(action)} · {action.safety_class}</span>
                                        </button>
                                    ))}
                                </div>
                            </section>
                            <div className="mt-4 grid gap-3 rounded border border-slate-800 bg-slate-900/60 p-3 md:grid-cols-[minmax(14rem,20rem)_1fr]">
                                <label className="text-xs text-slate-300">
                                    Control group
                                    <select value={subsystemFilter} onChange={(event) => setSubsystemFilter(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm">
                                        <option value="all">All Individual Controls</option>
                                        <option value="motion-power">Motion Power</option>
                                        <option value="transport-evidence">Transport / Evidence</option>
                                        <option value="safety-recovery">Safety / Recovery</option>
                                        <option value="initialization">Initialization</option>
                                        {subsystemOptions.map((subsystem) => <option key={subsystem} value={subsystem}>{subsystem}</option>)}
                                    </select>
                                </label>
                                <label className="text-xs text-slate-300">
                                    Search individual controls
                                    <input type="search" value={actionSearch} onChange={(event) => setActionSearch(event.target.value)} placeholder="24 V, snapshot, stop, axis…" className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm" />
                                </label>
                                <p className="text-xs text-slate-500 md:col-span-2">Showing {browseActions.length} of {primitiveActions.length} authoritative individual controls.</p>
                            </div>
                            <div className="mt-4 space-y-3" data-individual-control-groups>
                                {groupedBrowseActions.map((group) => (
                                    <section key={group.subsystem} className="rounded border border-slate-800 bg-slate-950/60 p-3" data-subsystem={group.subsystem}>
                                        <h3 className="text-sm font-semibold text-slate-200">{group.subsystem} <span className="text-slate-500">({group.actions.length})</span></h3>
                                        <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                                            {group.actions.map((action) => (
                                                <button key={action.action_id} type="button" data-action-id={action.action_id} onClick={() => setSelectedId(action.action_id)} className={`rounded border px-3 py-2 text-left text-xs ${selected?.action_id === action.action_id ? 'border-cyan-500 bg-cyan-950/60' : 'border-slate-700 bg-slate-900'}`}>
                                                    <span className="block font-semibold">{action.label}</span>
                                                    <span className="text-slate-500">{action.safety_class}</span>
                                                </button>
                                            ))}
                                        </div>
                                    </section>
                                ))}
                            </div>
                            {browseActions.length === 0 && <p className="mt-2 rounded border border-amber-800/60 bg-amber-950/30 p-3 text-sm text-amber-200">No authoritative controls match this group or search.</p>}
                        </>
                    ) : (
                        <div className="mt-4 flex gap-2 overflow-x-auto pb-2" role="tablist" aria-label="Meta actions">
                            {paneActions.map((action) => (
                                <button key={action.action_id} type="button" role="tab" aria-selected={selected?.action_id === action.action_id} onClick={() => setSelectedId(action.action_id)} className={`shrink-0 rounded border px-3 py-2 text-left text-xs ${selected?.action_id === action.action_id ? 'border-cyan-500 bg-cyan-950/60' : 'border-slate-700 bg-slate-900'}`}>
                                    <span className="block font-semibold">{action.label}</span>
                                    <span className="text-slate-500">{action.subsystem} · {action.safety_class}</span>
                                </button>
                            ))}
                        </div>
                    )}
                    {selected && (
                        <article className={`mt-2 rounded border p-4 ${safetyTone[selected.safety_class]}`} role="tabpanel">
                            <div className="flex flex-wrap justify-between gap-3">
                                <div><h3 className="font-semibold">{selected.label}</h3><p className="font-mono text-xs text-cyan-200">{selected.action_id}</p></div>
                                <span className="rounded border border-current px-2 py-1 text-xs">{selected.kind} · {selected.safety_class}</span>
                            </div>
                            <p className="mt-2 text-sm text-slate-300">{selected.description}</p>
                            <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                                <div><dt className="text-slate-500">Robot route</dt><dd className="font-mono">{selected.informational_method} {selected.informational_path}</dd></div>
                                <div><dt className="text-slate-500">Timeout</dt><dd>{selected.timeout_seconds}s</dd></div>
                                <div className="sm:col-span-2"><dt className="text-slate-500">OEM source</dt><dd>{selected.source_anchor ?? 'No source anchor published'}</dd></div>
                            </dl>
                            {selected.stages.length > 0 && <p className="mt-2 text-xs text-slate-400">Stages: {selected.stages.join(' → ')}</p>}
                            <div className="mt-4 grid gap-3 md:grid-cols-2">
                                {selected.inputs.map((input) => (
                                    <label key={input.name} className="text-sm">
                                        <span className="block text-slate-300">{input.label}{input.required ? ' *' : ''}{input.unit ? ` (${input.unit})` : ''}</span>
                                        {input.value_type === 'boolean' ? (
                                            <input type="checkbox" checked={inputs[input.name] === true} onChange={(event) => setInputs((current) => ({ ...current, [input.name]: event.target.checked }))} />
                                        ) : input.value_type === 'enum' ? (
                                            <select value={String(inputs[input.name] ?? '')} onChange={(event) => setInputs((current) => ({ ...current, [input.name]: event.target.value }))} className="mt-1 w-full rounded border border-slate-700 bg-slate-900 p-2">
                                                <option value="">Select…</option>{input.enum_values.map((value) => <option key={value} value={value}>{value}</option>)}
                                            </select>
                                        ) : input.value_type === 'json' ? (
                                            <textarea value={typeof inputs[input.name] === 'string' ? String(inputs[input.name]) : JSON.stringify(inputs[input.name] ?? {}, null, 2)} onChange={(event) => setInputs((current) => ({ ...current, [input.name]: event.target.value }))} rows={8} className="mt-1 w-full rounded border border-slate-700 bg-slate-900 p-2 font-mono text-xs" />
                                        ) : (
                                            <input type={input.value_type === 'string' ? 'text' : 'number'} value={String(inputs[input.name] ?? '')} min={input.minimum ?? input.exclusive_minimum ?? undefined} max={input.maximum ?? input.exclusive_maximum ?? undefined} step={input.value_type === 'integer' ? 1 : input.value_type === 'number' ? 'any' : undefined} onChange={(event) => setInputs((current) => ({ ...current, [input.name]: event.target.value }))} className="mt-1 w-full rounded border border-slate-700 bg-slate-900 p-2" />
                                        )}
                                        {(input.minimum !== null || input.maximum !== null || input.exclusive_minimum !== null || input.exclusive_maximum !== null) && <span className="mt-1 block text-xs text-slate-500">Allowed: {input.minimum !== null ? `≥ ${input.minimum}` : input.exclusive_minimum !== null ? `> ${input.exclusive_minimum}` : 'unbounded'} to {input.maximum !== null ? `≤ ${input.maximum}` : input.exclusive_maximum !== null ? `< ${input.exclusive_maximum}` : 'unbounded'}</span>}
                                        {input.description && <span className="mt-1 block text-xs text-slate-500">{input.description}</span>}
                                    </label>
                                ))}
                            </div>
                            {dependencies.length > 0 && (
                                <div className="mt-4 grid gap-1 text-xs sm:grid-cols-2">
                                    {dependencies.map((dependency) => (
                                        <div key={dependency.key} className={dependency.met ? 'text-emerald-300' : 'text-amber-200'}>
                                            {dependency.met ? '✓' : 'Blocked:'} {dependency.label}{!dependency.met && dependency.reason ? ` — ${dependency.reason}` : ''}
                                        </div>
                                    ))}
                                </div>
                            )}
                            <button type="button" disabled={!connected || !actionEnabled || invoke.isPending || !sourceAuthorityAllowsAction} onClick={run} className={`mt-4 rounded px-4 py-2 font-semibold disabled:opacity-35 ${selected.safety_class === 'emergency' ? 'bg-red-700' : selected.safety_class === 'motion' ? 'bg-amber-700' : 'bg-cyan-700'}`}>Run exactly this action</button>
                            {!actionEnabled && <p className="mt-2 text-sm text-amber-200">Blocked: {disabledReason}</p>}
                        </article>
                    )}
                </>
            )}

            {actionError && <p className="mt-3 text-sm text-red-300">{actionError}</p>}
            {latestReceipt && pane !== 'logs' && (
                <div className="mt-4 space-y-3">
                    <ReceiptCard receipt={latestReceipt} />
                    <label className="block text-sm"><span className="text-slate-300">Your physical observation</span><textarea value={assessmentNote} onChange={(event) => setAssessmentNote(event.target.value)} rows={2} className="mt-1 w-full rounded border border-slate-700 bg-slate-900 p-2" /></label>
                    <div className="flex gap-2"><button type="button" disabled={assess.isPending} onClick={() => recordAssessment(latestReceipt, 'pass')} className="rounded bg-emerald-700 px-3 py-2 text-sm font-semibold disabled:opacity-35">Record PASS</button><button type="button" disabled={assess.isPending} onClick={() => recordAssessment(latestReceipt, 'fail')} className="rounded bg-red-800 px-3 py-2 text-sm font-semibold disabled:opacity-35">Record FAIL</button></div>
                </div>
            )}
        </section>
    );
}
