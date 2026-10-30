import { useEffect, useRef, useState } from 'react';

/** One outstanding item before a live deck move, in the order it will run. */
export type PrepStep = { id: 'enable' | 'home_z' | 'home_xy'; label: string; done: () => boolean; submit: () => void; blocked: () => string | null };
export type PrepIntent = { label: string; run: () => void; ready: () => string | null };

type Run = { intent: PrepIntent; steps: PrepStep[]; index: number; submittedAt: number | null; error: string | null; finished: boolean };

const STEP_TIMEOUT_MS = 120_000;

/**
 * Operator-confirmed preparation: nothing runs until Proceed. Each step is the
 * existing robot action; the next starts only after the robot reports the
 * previous one done. The move itself is still evaluated by the robot.
 */
export function useBioXpMovePreparation(plan: () => PrepStep[], tick: unknown) {
    const [offer, setOffer] = useState<{ intent: PrepIntent; steps: PrepStep[] } | null>(null);
    const [run, setRun] = useState<Run | null>(null);
    const runRef = useRef(run); runRef.current = run;
    // Steps are re-read from the latest plan each update so done/blocked see current robot state.
    const planRef = useRef(plan); planRef.current = plan;
    const live = (step: PrepStep) => planRef.current().find(s => s.id === step.id) ?? step;
    const request = (intent: PrepIntent) => {
        const steps = plan().filter(step => !step.done());
        if (!steps.length) {
            // Nothing this popup can prepare: send the move if the robot allows it, otherwise say why.
            const reason = intent.ready();
            setOffer(null);
            if (reason === null) { intent.run(); setRun(null); } else setRun({ intent, steps: [], index: 0, submittedAt: null, error: reason, finished: true });
            return;
        }
        setRun(null); setOffer({ intent, steps });
    };
    const proceed = () => { if (offer) setRun({ intent: offer.intent, steps: offer.steps, index: 0, submittedAt: null, error: null, finished: false }); setOffer(null); };
    const cancel = () => { setOffer(null); setRun(null); };
    useEffect(() => {
        const current = runRef.current;
        if (!current || current.finished || current.error) return;
        const captured = current.steps[current.index];
        const step = captured && live(captured);
        if (!step) {
            const reason = current.intent.ready();
            if (reason === null) current.intent.run();
            setRun({ ...current, finished: true, error: reason });
            return;
        }
        if (step.done()) { setRun({ ...current, index: current.index + 1, submittedAt: null }); return; }
        if (current.submittedAt === null) {
            const blocked = step.blocked();
            if (blocked === null) { step.submit(); setRun({ ...current, submittedAt: Date.now() }); }
            return;
        }
        if (Date.now() - current.submittedAt > STEP_TIMEOUT_MS) setRun({ ...current, error: `${step.label} did not finish within ${STEP_TIMEOUT_MS / 1000} s; the move was not sent.` });
    }, [tick, run]);
    return { offer, run, request, proceed, cancel, live };
}

export function BioXpMovePreparation({ state }: { state: ReturnType<typeof useBioXpMovePreparation> }) {
    const { offer, run, proceed, cancel, live } = state;
    if (offer) return <section role="alertdialog" aria-label="Prepare before moving" className="bld-prep">
        <h3>Not ready to move to {offer.intent.label}</h3>
        <p>These will run first, in this order, then the move:</p>
        <ol>{offer.steps.map(step => <li key={step.id}>{step.label}</li>)}<li>Move to {offer.intent.label}</li></ol>
        <p className="bld-note">Homing moves the axes. Stops stay available throughout; the robot still checks every step.</p>
        <div className="bld-prep-actions"><button type="button" onClick={proceed}>Proceed</button><button type="button" onClick={cancel}>Cancel</button></div>
    </section>;
    if (run && (!run.finished || run.error)) {
        const step = run.steps[run.index] && live(run.steps[run.index]);
        return <section role="status" aria-label="Preparing move" className="bld-prep">
            <h3>{run.error ? 'Move not sent' : `Preparing to move to ${run.intent.label}`}</h3>
            <ol>{run.steps.map((s, i) => <li key={s.id} aria-current={i === run.index ? 'step' : undefined}>{s.label}{i < run.index ? ' · done' : i === run.index && !run.error ? (run.submittedAt ? ' · running' : ` · waiting${live(s).blocked() ? `: ${live(s).blocked()}` : ''}`) : ''}</li>)}<li>Move to {run.intent.label}</li></ol>
            {run.error && <p role="alert">{run.error}</p>}
            {!run.error && step && <p className="bld-note">Waiting for the robot to report “{step.label}” done.</p>}
            <div className="bld-prep-actions"><button type="button" onClick={cancel}>{run.error ? 'Close' : 'Stop preparing'}</button></div>
        </section>;
    }
    return null;
}
