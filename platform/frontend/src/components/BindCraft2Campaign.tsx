import type { CSSProperties, ReactNode } from 'react';
import type { BC2CampaignPreview } from '../lib/bindcraft2AuthoringApi';
import type { BC2Request, BC2Section } from './BindCraft2Settings';

interface BindCraft2CampaignProps {
    name: string;
    onNameChange: (name: string) => void;
    onBack: () => void;
    generatorChooser: ReactNode;
    children: ReactNode;
    section?: BC2Section;
    onSectionChange?: (section: BC2Section) => void;
    requestedSettings: BC2Request;
    preview: BC2CampaignPreview | null;
    previewBusy: boolean;
    submitting: boolean;
    launchAvailable?: boolean;
    error?: string | null;
    onPreview: () => void;
    onLaunch: () => void;
    onOpenLibrary: () => void;
    executionTarget: ReactNode;
    library: ReactNode;
}

const surface: CSSProperties = {
    background: 'var(--bg-secondary)',
    borderColor: 'var(--border-primary)',
    color: 'var(--text-primary)',
};
const inset: CSSProperties = {
    background: 'var(--surface-control)',
    borderColor: 'var(--border-primary)',
    color: 'var(--text-primary)',
};
const action: CSSProperties = {
    background: 'var(--accent-primary)',
    borderColor: 'var(--accent-primary)',
    color: 'var(--text-on-accent)',
};
const buttonClass = 'inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50';

function shortValue(value: unknown, fallback = 'Profile default'): string {
    if (value === undefined) return fallback;
    if (value === null) return 'Native automatic';
    if (Array.isArray(value)) return value.length ? value.map(String).join(' · ') : 'Explicit empty selection';
    if (typeof value === 'object') return 'Custom settings';
    return String(value);
}

function messageText(value: unknown): string {
    if (typeof value === 'string') return value;
    if (value && typeof value === 'object' && 'message' in value) return String(value.message);
    return JSON.stringify(value);
}

/** Campaign composition only. All scientific values and native resolution remain model-owned. */
export function BindCraft2Campaign({
    name, onNameChange, onBack, generatorChooser, children, requestedSettings,
    preview, previewBusy, submitting, launchAvailable, error, onPreview, onLaunch,
    onOpenLibrary, executionTarget, library, section, onSectionChange,
}: BindCraft2CampaignProps) {
    const display = preview?.effective_settings ?? requestedSettings;
    const sources = Array.isArray(display.targets) ? display.targets : [];
    const targetNames = sources.map((target: unknown, index: number) => {
        if (target && typeof target === 'object' && 'name' in target && target.name) return String(target.name);
        return `Target ${index + 1}`;
    });
    const modality = shortValue(display.modality);
    const messages = [...(preview?.warnings ?? []), ...(preview?.blockers ?? [])];
    return (
        <section aria-label="BindCraft2 campaign" className="min-w-0 space-y-5 [overflow-wrap:anywhere]">
            <header className="relative overflow-hidden rounded-2xl border p-5 sm:p-6" style={{
                ...surface,
                background: 'linear-gradient(125deg, color-mix(in srgb, var(--accent-primary) 12%, var(--bg-secondary)), var(--bg-secondary) 70%)',
            }}>
                <div className="absolute inset-y-0 left-0 w-1" style={{ background: 'var(--accent-primary)' }} />
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="min-w-0 space-y-2">
                        <button type="button" onClick={onBack} className="inline-flex items-center gap-2 rounded px-1 py-1 text-sm hover:underline" style={{ color: 'var(--text-secondary)' }}>
                            <span aria-hidden="true">←</span> Back to workflows
                        </button>
                        <div className="flex flex-wrap items-center gap-3">
                            <h2 className="text-2xl font-semibold tracking-tight">BindCraft2 campaign</h2>
                            <span className="rounded-full border px-3 py-1 text-xs font-medium" style={{ borderColor: 'var(--accent-primary)', color: 'var(--accent-primary)' }}>Binder design</span>
                        </div>
                        <p className="max-w-2xl text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                            Prepare your targets, shape the binder and configure the native campaign. Structure selection and model settings stay together.
                        </p>
                    </div>
                    <button type="button" onClick={onOpenLibrary} className={buttonClass} style={inset}>Saved campaigns</button>
                </div>
                <details className="mt-4 rounded-xl border px-4 py-3" style={{ borderColor: 'var(--border-primary)', background: 'var(--bg-secondary)' }}>
                    <summary className="cursor-pointer text-sm font-medium">Change generation engine</summary>
                    <div className="mt-3">{generatorChooser}</div>
                </details>
            </header>

            <div className="grid min-w-0 items-start gap-5 xl:grid-cols-[minmax(0,1fr)_18rem]">
                <div className="min-w-0 space-y-4">
                    {onSectionChange && <nav aria-label="Campaign workspace sections" className="flex flex-wrap gap-2 rounded-xl border p-2" style={surface}>
                        {([['sources', 'Sources'], ['binder', 'Binder design'], ['campaign', 'Campaign'], ['objectives', 'Objectives'], ['expert', 'Expert']] as const).map(([key, label]) =>
                            <button type="button" key={key} aria-pressed={section === key} onClick={() => onSectionChange(key)} className={`${buttonClass} flex-1`} style={section === key ? action : inset}>{label}</button>)}
                    </nav>}
                    {children}
                </div>
                <aside aria-label="Campaign review and launch" className="min-w-0 space-y-4 xl:sticky xl:top-5">
                    <section className="rounded-xl border p-4" style={surface}>
                        <h3 className="text-base font-semibold">Your campaign</h3>
                        <label className="mt-4 block text-sm font-medium">
                            Campaign name
                            <input aria-label="Draft name" value={name} onChange={event => onNameChange(event.currentTarget.value)}
                                className="mt-2 w-full min-w-0 rounded-lg border px-3 py-2 font-normal focus-visible:outline-2 focus-visible:outline-offset-2" style={inset} />
                        </label>
                        <dl className="mt-4 space-y-3 text-sm">
                            <div><dt style={{ color: 'var(--text-secondary)' }}>Binder modality</dt><dd className="mt-0.5 font-medium">{modality}</dd></div>
                            <div><dt style={{ color: 'var(--text-secondary)' }}>Targets</dt><dd className="mt-0.5 font-medium">{targetNames.length ? targetNames.join(' · ') : shortValue(display.target, 'Choose sources or a native target preset')}</dd></div>
                            <div><dt style={{ color: 'var(--text-secondary)' }}>Binder lengths</dt><dd className="mt-0.5 font-medium">{shortValue(display.binder_lengths)}</dd></div>
                            <div className="grid grid-cols-2 gap-3">
                                <div><dt style={{ color: 'var(--text-secondary)' }}>Attempt budget</dt><dd className="mt-0.5 font-medium">{shortValue(display.max_trajectories, 'Not specified')}</dd></div>
                                <div><dt style={{ color: 'var(--text-secondary)' }}>Retained designs</dt><dd className="mt-0.5 font-medium">{shortValue(display.number_of_final_designs)}</dd></div>
                            </div>
                        </dl>
                        <p className="mt-4 text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{preview ? 'Showing the native compiler’s resolved settings.' : 'Preview resolves your choices against the native profiles.'}</p>
                    </section>

                    {launchAvailable === true && <section className="rounded-xl border p-4" style={surface}>
                        <h3 className="mb-3 text-base font-semibold">Execution</h3>
                        {executionTarget}
                    </section>}

                    <section className="space-y-3 rounded-xl border p-4" style={surface}>
                        <h3 className="text-base font-semibold">Review and launch</h3>
                        <button type="button" disabled={previewBusy} onClick={onPreview} className={`${buttonClass} w-full`} style={preview ? inset : action}>
                            {previewBusy ? 'Compiling native preview…' : 'Preview native campaign'}
                        </button>
                        {launchAvailable === true && <button type="button" disabled={submitting || !preview} onClick={onLaunch} className={`${buttonClass} w-full`} style={action}>
                            {submitting ? 'Submitting campaign…' : 'Launch BindCraft2 campaign'}
                        </button>}
                        <button type="button" onClick={onOpenLibrary} className={`${buttonClass} w-full`} style={inset}>Save campaign draft</button>
                        {!preview && <p role="status" className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>Preview the current native settings before launch. Editing settings refreshes what needs to be previewed.</p>}
                        {launchAvailable === false && <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>Campaign execution is unavailable in this installation. You can still edit and save your settings.</p>}
                        {error && <p role="alert" className="rounded-lg border p-3 text-sm" style={{ color: 'var(--error)', borderColor: 'var(--error)' }}>{error}</p>}
                    </section>
                    <p className="px-1 text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>Native relaxation belongs to this campaign. Further selected-candidate operations are available from the results.</p>
                </aside>
            </div>

            {preview && <section aria-label="Compiled native campaign preview" className="min-w-0 space-y-3 rounded-xl border p-5" style={surface}>
                <h3 className="text-base font-semibold">Native campaign preview</h3>
                {preview.note && <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>{preview.note}</p>}
                {messages.length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm">{messages.map((message, index) => <li key={index}>{messageText(message)}</li>)}</ul>}
                <details className="rounded-lg border p-3" style={inset}>
                    <summary className="cursor-pointer text-sm font-medium">Full compiled configuration</summary>
                    <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap text-xs leading-relaxed">{JSON.stringify(preview.effective_settings, null, 2)}</pre>
                    <p className="mt-3 text-xs" style={{ color: 'var(--text-secondary)' }}>Configuration identity: <code>{preview.preview_digest}</code></p>
                </details>
            </section>}
            <details className="rounded-xl border px-4 py-3" style={surface}>
                <summary className="cursor-pointer text-sm font-medium">Requested native settings</summary>
                <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap text-xs leading-relaxed">{JSON.stringify(requestedSettings, null, 2)}</pre>
            </details>
            {library}
        </section>
    );
}
