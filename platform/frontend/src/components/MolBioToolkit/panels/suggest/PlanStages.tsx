import type { ReactNode } from 'react';

/**
 * Shared Setup -> Plans -> Workup shell for the Build target panel (presentation only).
 * Theme: bg #0b1020, panel #18223a, card #1b2740, border #293a59, accent #83a7ff, text #eef4ff, muted #a6b3ca.
 * Every container is min-w-0 / wrapping so a 390px viewport never scrolls horizontally.
 */

export function StageHeading({ n, title, right }: { n: number; title: string; right?: ReactNode }) {
    return <div data-stage-heading={title} className="flex min-w-0 items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-[#a6b3ca]">
        <span aria-hidden="true" className="grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full bg-[#293a59] text-[10px] text-[#eef4ff]">{n}</span>
        <h6 className="min-w-0 font-bold">{title}</h6>
        {right != null && <span className="ml-auto min-w-0 break-words text-xs font-medium normal-case tracking-normal text-[#939fb6]">{right}</span>}
    </div>;
}

/** Stage 1 card. With `plain`, children render unwrapped (used by methods that keep their own layout). */
export function SetupCard({ children, plain = false, right }: { children: ReactNode; plain?: boolean; right?: ReactNode }) {
    if (plain) return <>{children}</>;
    return <div data-stage="setup" className="min-w-0 space-y-3 rounded-xl border border-[#293a59] bg-[#1b2740] p-3">
        <StageHeading n={1} title="Setup" right={right} />
        {children}
    </div>;
}

/** A titled group inside a stage card; renders a plain-language heading above its controls. */
export function SetupGroup({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
    return <div className="min-w-0 space-y-2">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-[#a6b3ca]">{title}</div>
        {hint && <p className="text-[#939fb6]">{hint}</p>}
        {children}
    </div>;
}

export function Chip({ children, tone = 'plain' }: { children: ReactNode; tone?: 'plain' | 'ok' | 'warn' | 'acc' }) {
    const color = tone === 'ok' ? 'border-[#2d6b57] text-[#54d8a2]' : tone === 'warn' ? 'border-[#7a5a24] text-[#ffb84a]'
        : tone === 'acc' ? 'border-[#3b5aa0] text-[#83a7ff]' : 'border-[#293a59] text-[#a6b3ca]';
    return <span className={`max-w-full break-words rounded-full border bg-[#0d1324] px-2 py-px text-[11px] ${color}`}>{children}</span>;
}

/** Stage 2: ranked proposal cards. `empty` is shown instead of the list when there are none. */
export function PlansList({ count, children, empty }: { count: number; children: ReactNode; empty?: ReactNode }) {
    return <div data-stage="plans" className="min-w-0 space-y-2">
        <StageHeading n={2} title="Plans" right={`${count} ${count === 1 ? 'plan' : 'plans'}`} />
        {count === 0 && empty}
        {count > 0 && <ul className="min-w-0 space-y-2">{children}</ul>}
    </div>;
}

/** One ranked plan card; the first plan is accented as the recommended one. */
export function PlanCard({ rank, title, chips, children, action, featured = false }: {
    rank: number; title: ReactNode; chips?: ReactNode; children?: ReactNode; action: ReactNode; featured?: boolean;
}) {
    return <li data-plan-card={rank} className={`min-w-0 space-y-2 rounded-xl border bg-[#1b2740] p-3 ${featured ? 'border-[#3b5aa0]' : 'border-[#293a59]'}`}>
        <div className="min-w-0 break-words font-medium text-[#eef4ff]">#{rank} {title}</div>
        {chips && <div className="flex min-w-0 flex-wrap gap-1.5">{chips}</div>}
        {children}
        <div className="flex min-w-0 flex-wrap items-center gap-2 pt-1">{action}</div>
    </li>;
}

export function PrimaryButton({ children, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
    return <button type="button" {...rest} className="max-w-full rounded-lg bg-[#83a7ff] px-3 py-1.5 text-center font-semibold text-black disabled:opacity-50">{children}</button>;
}

/** Stage 3: what to order / amplify. One WorkupBlock per plan, inside a single Workup stage. */
export function WorkupStage({ children }: { children: ReactNode }) {
    return <div data-stage="workup" className="min-w-0 space-y-2">
        <StageHeading n={3} title="Workup" right="what to order or amplify" />
        {children}
    </div>;
}

export function WorkupBlock({ title, children }: { title: ReactNode; children: ReactNode }) {
    return <div data-workup-block className="min-w-0 space-y-1.5 rounded-xl border border-[#293a59] bg-[#151e33] p-3">
        <div className="min-w-0 break-words font-semibold text-[#eef4ff]">{title}</div>
        {children}
    </div>;
}

/** Labelled sequence line with a copy button; `copyLabel` is the exact button text. */
export function CopyRow({ label, text, copyLabel, onCopy }: { label: string; text: string; copyLabel: string; onCopy: (text: string) => void }) {
    return <div className="min-w-0 break-all text-[#dbe4f5]">{label}: <code>{text}</code>
        <button type="button" className="ml-2 rounded border border-[#3b4f73] px-2 py-0.5 text-[#83a7ff]" onClick={() => onCopy(text)}>{copyLabel}</button></div>;
}
