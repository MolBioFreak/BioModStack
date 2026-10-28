import { useEffect, useRef, useState } from 'react';
import {
    fetchOntPod5Restore,
    fetchOntRawSignalCapabilities,
    importOntRawSignalUpload,
    requestOntBlow5Preparation,
    requestOntPod5Restore,
    uploadFile,
} from '../../lib/api';

type Step = 'idle' | 'uploading' | 'registering' | 'indexing' | 'restoring' | 'ready' | 'failed';

interface RawSignalPod5InputProps {
    /** Exact Project domain experiment that owns the imported raw-signal run. */
    domainExperimentId: string | null;
    onPod5Directory: (directory: string) => void;
    onBusyChange: (busy: boolean) => void;
}

const STEP_LABEL: Record<Step, string> = {
    idle: '',
    uploading: 'Uploading raw signal…',
    registering: 'Registering the raw-signal source…',
    indexing: 'Validating and indexing BLOW5…',
    restoring: 'Restoring POD5 for Dorado…',
    ready: 'POD5 restored and selected for basecalling.',
    failed: 'Raw-signal preparation failed.',
};

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

/**
 * BLOW5/SLOW5 → indexed BLOW5 → restored POD5, using the same raw-signal
 * services as Raw signal tools. Dorado 2.1.2 reads POD5 only, so the restored
 * POD5 directory becomes this analysis's input.
 */
export function RawSignalPod5Input({ domainExperimentId, onPod5Directory, onBusyChange }: RawSignalPod5InputProps) {
    const [file, setFile] = useState<File | null>(null);
    const [index, setIndex] = useState<File | null>(null);
    const [step, setStep] = useState<Step>('idle');
    const [detail, setDetail] = useState('');
    const active = useRef(true);
    useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
    useEffect(() => { onBusyChange(!['idle', 'ready', 'failed'].includes(step)); }, [step, onBusyChange]);
    useEffect(() => () => onBusyChange(false), [onBusyChange]);

    const format = file?.name.toLowerCase().endsWith('.slow5') ? 'slow5' : file?.name.toLowerCase().endsWith('.blow5') ? 'blow5' : null;

    const run = async () => {
        if (!file || !format || !domainExperimentId) return;
        onPod5Directory('');
        setDetail('');
        try {
            setStep('uploading');
            const directory = `inputs/ont-raw-signal/${crypto.randomUUID()}`;
            const name = `source.${format}`;
            const sourcePath = (await uploadFile(directory, new File([file], name))).data.path;
            const indexPath = format === 'blow5' && index ? (await uploadFile(directory, new File([index], `${name}.idx`))).data.path : null;
            if (!active.current) return;
            setStep('registering');
            const registration = await importOntRawSignalUpload({
                file_path: sourcePath, format, index_file_path: indexPath, experiment_group: domainExperimentId, sample_id: null,
            });
            const { run_id: runId, observed_generation: generation } = registration;
            setStep('indexing');
            let blow5Id: string | null = null;
            let requested = false;
            for (let attempt = 0; attempt < 720 && active.current; attempt++) {
                const capabilities = await fetchOntRawSignalCapabilities(runId, generation, 'blow5');
                const ready = capabilities.representations.find(rep => rep.format === 'blow5' && rep.state === 'ready');
                if (ready) { blow5Id = ready.representation_id; break; }
                if (!requested) {
                    const source = capabilities.representations.find(rep => rep.role === 'source') ?? registration.representation;
                    await requestOntBlow5Preparation(runId, generation, source.representation_id, 'blow5');
                    requested = true;
                }
                await sleep(5000);
            }
            if (!blow5Id) throw new Error('Indexed BLOW5 did not become ready.');
            setStep('restoring');
            let restore = await requestOntPod5Restore(runId, generation, blow5Id);
            while (active.current && restore.state === 'running') {
                await sleep(5000);
                restore = await fetchOntPod5Restore(runId, generation, blow5Id);
            }
            if (restore.state !== 'ready' || !restore.directory) throw new Error(restore.error || `POD5 restore ended in state ${restore.state}.`);
            if (!active.current) return;
            onPod5Directory(restore.directory);
            setDetail(`${restore.restored_read_count ?? 'All'} reads restored to ${restore.directory}. Raw signal is also kept as indexed BLOW5 for squiggle views.`);
            setStep('ready');
        } catch (error) {
            if (!active.current) return;
            setDetail(error instanceof Error ? error.message : String(error));
            setStep('failed');
        }
    };

    const busy = !['idle', 'ready', 'failed'].includes(step);
    return <div className="mt-3 space-y-2" data-raw-signal-pod5-input>
        {!domainExperimentId && <p className="text-xs text-[var(--warning)]">Open a Project first; imported raw signal is stored with that Project.</p>}
        <label className="block text-xs font-medium text-[var(--text-secondary)]">
            BLOW5 or SLOW5 file
            <input aria-label="BLOW5 or SLOW5 file" type="file" accept=".blow5,.slow5" disabled={busy}
                className="mt-1 block w-full min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 text-sm text-[var(--text-primary)]"
                onChange={event => { setFile(event.target.files?.[0] ?? null); setStep('idle'); event.target.value = ''; }} />
        </label>
        {format === 'blow5' && <label className="block text-xs font-medium text-[var(--text-secondary)]">
            Optional existing BLOW5 index (.idx)
            <input aria-label="BLOW5 index file" type="file" accept=".idx" disabled={busy}
                className="mt-1 block w-full min-w-0 rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 text-sm text-[var(--text-primary)]"
                onChange={event => { setIndex(event.target.files?.[0] ?? null); event.target.value = ''; }} />
        </label>}
        {file && !format && <p role="alert" className="text-xs text-[var(--text-error)]">Choose a .blow5 or .slow5 file.</p>}
        <button type="button" onClick={() => void run()} disabled={!file || !format || !domainExperimentId || busy}
            className="rounded border border-[var(--border-primary)] px-3 py-1 text-xs font-semibold text-[var(--text-primary)] disabled:opacity-40">
            {busy ? STEP_LABEL[step] : 'Prepare POD5 for basecalling'}
        </button>
        {step !== 'idle' && !busy && <p role={step === 'failed' ? 'alert' : 'status'} className={`text-xs ${step === 'failed' ? 'text-[var(--text-error)]' : 'text-[var(--text-secondary)]'}`}>{STEP_LABEL[step]} {detail}</p>}
        <p className="text-xs text-[var(--text-secondary)]">Dorado 2.1.2 basecalls POD5 only. The file is validated, indexed as BLOW5 and restored to POD5, which then runs through the POD5 settings below.</p>
    </div>;
}
