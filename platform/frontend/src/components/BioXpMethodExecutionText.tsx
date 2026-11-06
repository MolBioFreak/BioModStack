import { useState } from 'react';
import type { MethodCompile, MethodValue } from '../lib/bioxpMethods';
import { buildMethodRunRequest } from '../lib/bioxpMethodRunRequest';

export type BioXpMethodExecutionTextProps = {
    input: MethodValue;
    /** Only the fresh compilation matching input; omit while unavailable/stale. */
    result?: MethodCompile;
    actions: MethodValue[];
    pageLabel: string;
    /** Human-readable planned well projection; never part of the native payload. */
    pageActivity?: string;
    workflowActivity?: string;
    pinned?: { id: string; revision: number } | null;
    basis?: { id: string; revision: number; edited: boolean } | null;
    generation?: number;
};
const json = (value: unknown) => JSON.stringify(value, null, 2);

function TextExport({ text, label, filename }: { text: string; label: string; filename: string }) {
    const [status, setStatus] = useState('');
    async function copy() {
        try { await navigator.clipboard.writeText(text); setStatus('Copied.'); }
        catch { setStatus('Copy unavailable; select and copy the text below.'); }
    }
    function download() {
        const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
        const link = document.createElement('a'); link.href = url; link.download = filename;
        document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 0);
    }
    return <div className="bioxp-method-execution-text-export">
        <button type="button" onClick={copy}>Copy</button>
        <button type="button" onClick={download}>Download txt</button>
        <span role="status">{status}</span>
        <textarea aria-label={label} readOnly value={text} wrap="off" rows={18}
            style={{ width: '100%', maxWidth: '100%', boxSizing: 'border-box', overflow: 'auto', fontFamily: 'monospace', whiteSpace: 'pre', userSelect: 'text' }} />
    </div>;
}

/** Display/export only. The caller owns freshness and selected-page action selection. */
export function BioXpMethodExecutionText({ input, result, actions, pageLabel, pageActivity, workflowActivity, pinned = null, basis = null, generation }: BioXpMethodExecutionTextProps) {
    const [full, setFull] = useState(false);
    const heading = full ? 'Full workflow' : `Stage: ${pageLabel} — selected-page excerpt`;
    const activity = full ? workflowActivity : pageActivity;
    const text = !result?.document
        ? `${heading}\nCompiled actions unavailable — no fresh compilation.\nAuthored input:\n${json(input)}`
        : `${heading}\nNative actions belong to one submitted protocol, not individual HTTP calls.\n${full
            ? 'Complete compiled protocol document, including enclosing execution context.'
            : 'Selected native actions only — not a runnable protocol. Enclosing stage conditions and protocol metadata are preserved in Full workflow; consult that context when interpreting this excerpt.'}\n\n${activity
            ? `Planned well projection (display only; not execution evidence or native payload):\n${activity}\n\n`
            : ''}${full ? 'Exact compiled protocol JSON' : 'Exact selected native actions JSON'}:\n${json(full ? result.document : actions)}`;
    const request = buildMethodRunRequest(input, pinned, basis, 'GENERATED_ON_RUN', generation ?? 0);
    const runBody = generation === undefined ? { ...request.body, expected_generation: 'UNKNOWN' } : request.body;
    const apiText = `POST /api/bioxp/methods/check\n${json(input)}\n\nRun request template — not submitted.\nGENERATED_ON_RUN is a display placeholder; the real idempotency key is generated only on Run.\n${generation === undefined ? 'Connection generation is unknown; UNKNOWN is a display placeholder.' : `Current connection generation: ${generation}; submission freezes its own generation.`}\nPOST /api/bioxp/methods/${request.path}\n${json(runBody)}`;
    return <details className="bioxp-method-execution-text">
        <summary>Execution text (optional)</summary>
        <div role="group" aria-label="Execution text scope">
            <button type="button" aria-pressed={!full} onClick={() => setFull(false)}>Selected page</button>
            <button type="button" aria-pressed={full} onClick={() => setFull(true)}>Full workflow</button>
        </div>
        <TextExport text={text} label="Execution text" filename="bioxp-execution.txt" />
        <details><summary>API request text</summary>
            <p>Read-only request display. Nothing is submitted, executed or imported here.</p>
            <TextExport text={apiText} label="API request text" filename="bioxp-api-requests.txt" />
        </details>
    </details>;
}
