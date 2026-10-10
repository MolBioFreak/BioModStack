import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { bioXpErrorText, type BioXpOperatorJsonSchema } from '../lib/bioxpClient';
import { type MethodValue } from '../lib/bioxpMethods';
import { MethodFields, object } from './BioXpMethodFields';
import { BioXpCorrectionPin } from './BioXpCorrectionPin';
import { PipetteValue } from './BioXpPipetteResults';

/** Returned producer values only: the browser never calculates fits or corrections. */
export function BioXpAnalysisResult({ result }: { result: MethodValue }) {
    const rows = (key: string) => Array.isArray(result[key]) ? result[key] as MethodValue[] : [];
    const points = (key: string, title: string) => <section aria-label={title}><h5>{title}</h5>{rows(key).map((point, index) => <dl key={index} className="border-b border-slate-700 py-2">
        <dt>Point</dt><dd><PipetteValue value={point.point_id} /></dd>
        <dt>Target</dt><dd><PipetteValue value={point.target} /></dd><dt>Commanded</dt><dd><PipetteValue value={point.commanded} /></dd>
        <dt>External measurements</dt><dd><PipetteValue value={point.measured} /></dd><dt>Mean</dt><dd><PipetteValue value={point.mean} /></dd>
        <dt>Bias</dt><dd><PipetteValue value={point.bias} /></dd><dt>Repeatability (sample SD)</dt><dd><PipetteValue value={point.sample_sd} /></dd>
        <dt>CV (%)</dt><dd><PipetteValue value={point.cv_percent} /></dd><dt>Count</dt><dd><PipetteValue value={point.count} /></dd>
    </dl>)}</section>;
    return <section aria-label="Calibration analysis result" className="min-w-0 break-words space-y-3">
        <h4>Returned {String(result.kind ?? '')} analysis</h4>
        <p>Pure calculation. No correction is automatically selected or applied. Commanded values are not controller readback or externally measured delivery.</p>
        <p>Units: {result.kind === 'pipette' ? 'uL' : result.kind === 'thermal' ? 'degC' : 'not reported'} · Applied: <PipetteValue value={result.applied} /></p>
        {points('points', 'Calibration points')}
        <h5>Resolved external observations / controller readback</h5><PipetteValue value={result.resolved_samples} />
        <h5>Fit</h5><PipetteValue value={result.affine_fit} />
        <h5>Returned correction (not selected)</h5><PipetteValue value={result.correction} />
        <h5>Evaluated targets / corrected commands</h5><PipetteValue value={result.evaluated} />
        {points('verification_points', 'Separately authored verification')}
        {result.thermal_application != null && <p>Thermal application: {String(result.thermal_application).replaceAll('_', ' ')}. No coefficient write is implied.</p>}
        <details><summary>Exact analysis request and context</summary><PipetteValue value={result.requested} /></details>
    </section>;
}

export function BioXpCalibrationAnalysis({ visible, connected, generation }: { visible: boolean; connected: boolean; generation: number }) {
    const [open, setOpen] = useState(false), [draft, setDraft] = useState<MethodValue>({});
    const [result, setResult] = useState<{ generation: number; value: MethodValue } | null>(null);
    const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
    const lock = useRef(false), authority = useRef(generation); authority.current = generation;
    const schema = useQuery({ queryKey: ['bioxp', 'calibration', 'schema'], queryFn: async () => (await api.get<{ analysis_request: BioXpOperatorJsonSchema }>('/api/bioxp/protocols/calibration/schema')).data, enabled: visible && open, retry: false, staleTime: Infinity });
    async function analyze() {
        if (lock.current) return;
        const current = generation, requested = structuredClone(draft);
        lock.current = true; setBusy(true); setError(null);
        try {
            const response = await api.post<MethodValue>('/api/bioxp/protocols/calibration/analyze', requested, { params: { expected_connection_generation: current } });
            if (authority.current === current) setResult({ generation: current, value: response.data });
        } catch (e) { if (authority.current === current) setError(bioXpErrorText(e)); }
        finally { lock.current = false; setBusy(false); }
    }
    return <section hidden={!visible} className="mt-4 min-w-0 rounded border border-slate-700 p-3" aria-label="Pure calibration analysis">
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>Measurement analysis</button>
        <div hidden={!open}>
            <p>Analyze authored external measurements without operating the robot. Select the model explicitly. Workflows owns ordered calibration methods and operator prompts. Missing operands are not inferred from zero.</p>
            {schema.isLoading && <p>Reading calibration definitions…</p>}
            {schema.error && <p role="alert">{bioXpErrorText(schema.error)}</p>}
            {schema.data && <MethodFields label="Analysis" schema={schema.data.analysis_request} value={draft} onChange={value => setDraft(object(value))} />}
            <button type="button" disabled={!connected || busy || !schema.data} onClick={() => void analyze()}>Analyze measurements (no device call)</button>
        </div>
        {busy && <p role="status">Calculating authored measurements…</p>}
        {error && <p role="alert">{error}</p>}
        {result?.generation === generation && <><BioXpAnalysisResult result={result.value} /><BioXpCorrectionPin key={JSON.stringify(result.value)} analysis={result.value} /></>}
    </section>;
}
