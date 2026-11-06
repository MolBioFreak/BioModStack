import { useState } from 'react';
import { repeatDeNovoSettings } from '../lib/deNovoContinuation';

/** Manual unsent clone, distinct from retry and retained-state continuation. */
export function DeNovoRepeatSettings({ jobId }: { jobId: string }) {
    const [busy, setBusy] = useState(false), [error, setError] = useState('');
    return <span><button type="button" disabled={busy} onClick={() => {
        setBusy(true); setError('');
        void repeatDeNovoSettings(jobId).then(href => window.location.assign(href))
            .catch(reason => setError(String(reason))).finally(() => setBusy(false));
    }}>Repeat with these settings</button>{error && <span role="alert">{error}</span>}</span>;
}
