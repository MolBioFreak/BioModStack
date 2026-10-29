import { useState } from 'react';

/** Keep machine identities out of the reading path; one click copies the exact value. */
export function BioXpCopyId({ label, value }: { label: string; value: string | null | undefined }) {
    const [copied, setCopied] = useState(false);
    if (!value) return null;
    return <button type="button" className="bioxp-copy-id" title={value} aria-label={`Copy ${label}`} data-copy-value={value}
        onClick={() => { void navigator.clipboard?.writeText(value).then(() => setCopied(true), () => setCopied(false)); }}>
        {copied ? `${label} copied` : `Copy ${label}`}
    </button>;
}
