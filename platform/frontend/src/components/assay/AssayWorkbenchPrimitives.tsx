import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from 'react';

export type AssayTone = 'default' | 'accent' | 'warning' | 'success' | 'error';

export type AssayNavItem<T extends string = string> = {
    id: T;
    label: string;
    eyebrow?: string;
    status?: string;
    description: string;
};

export type AssayStatusItem = {
    title: string;
    value: string;
    tone?: AssayTone;
};

export type AssaySegmentedTabItem<T extends string = string> = {
    id: T;
    label: string;
};

const toneStyles: Record<AssayTone, CSSProperties> = {
    default: {},
    accent: {
        borderColor: 'color-mix(in srgb, var(--accent-primary) 24%, transparent)',
        background: 'color-mix(in srgb, var(--accent-primary) 8%, var(--bg-secondary))',
    },
    warning: {
        borderColor: 'color-mix(in srgb, var(--warning) 24%, transparent)',
        background: 'color-mix(in srgb, var(--warning) 8%, var(--bg-secondary))',
    },
    success: {
        borderColor: 'color-mix(in srgb, var(--success) 24%, transparent)',
        background: 'color-mix(in srgb, var(--success) 8%, var(--bg-secondary))',
    },
    error: {
        borderColor: 'color-mix(in srgb, var(--error) 24%, transparent)',
        background: 'color-mix(in srgb, var(--error) 8%, var(--bg-secondary))',
    },
};

const assayPanelSurfaceStyle: CSSProperties = {
    borderColor: 'color-mix(in srgb, var(--accent-primary) 14%, transparent)',
    background:
        'linear-gradient(135deg, color-mix(in srgb, var(--bg-secondary) 92%, var(--accent-primary) 8%), color-mix(in srgb, var(--bg-primary) 55%, var(--bg-secondary) 45%))',
    boxShadow:
        '0 18px 45px rgba(2, 6, 23, 0.26), inset 0 1px 0 color-mix(in srgb, var(--accent-primary) 5%, transparent)',
};

const assayTileSurfaceStyle: CSSProperties = {
    borderColor: 'color-mix(in srgb, var(--border-primary) 42%, transparent)',
    background:
        'linear-gradient(145deg, color-mix(in srgb, var(--bg-secondary) 78%, var(--bg-primary) 22%), color-mix(in srgb, var(--bg-tertiary) 24%, var(--bg-secondary) 76%))',
    boxShadow: 'inset 0 1px 0 color-mix(in srgb, var(--accent-primary) 4%, transparent)',
};

const assayQuietSurfaceStyle: CSSProperties = {
    borderColor: 'color-mix(in srgb, var(--accent-primary) 14%, transparent)',
    background: 'color-mix(in srgb, var(--bg-secondary) 62%, var(--bg-primary) 38%)',
};

const assayNavSurfaceStyle: CSSProperties = {
    ...assayTileSurfaceStyle,
    boxShadow:
        '0 10px 26px rgba(2, 6, 23, 0.16), inset 0 1px 0 color-mix(in srgb, var(--accent-primary) 4%, transparent)',
};

const assayActiveNavSurfaceStyle: CSSProperties = {
    borderColor: 'color-mix(in srgb, var(--accent-primary) 30%, transparent)',
    background:
        'linear-gradient(145deg, color-mix(in srgb, var(--accent-primary) 14%, var(--bg-secondary)), color-mix(in srgb, var(--accent-secondary) 6%, var(--bg-primary)))',
    boxShadow:
        '0 16px 38px rgba(2, 6, 23, 0.28), inset 0 1px 0 color-mix(in srgb, var(--accent-primary) 16%, transparent)',
};

const assayControlSurfaceStyle: CSSProperties = {
    borderColor: 'color-mix(in srgb, var(--accent-primary) 22%, transparent)',
    background: 'color-mix(in srgb, var(--bg-tertiary) 32%, var(--bg-secondary))',
};

function cx(...classes: Array<string | false | undefined>): string {
    return classes.filter(Boolean).join(' ');
}

function mergeSurfaceStyles(...styles: Array<CSSProperties | undefined>): CSSProperties {
    return Object.assign({}, ...styles.filter(Boolean));
}

export function AssayPageShell({
    children,
    className = '',
    contentClassName = 'max-w-[1840px]',
}: {
    children: ReactNode;
    className?: string;
    contentClassName?: string;
}) {
    return (
        <div className={cx('min-h-full bg-[var(--bg-primary)] px-4 py-5 text-[var(--text-primary)] sm:px-6 lg:px-8', className)}>
            <div className={cx('mx-auto w-full space-y-5', contentClassName)}>{children}</div>
        </div>
    );
}

export function AssayPanel({ children, className = '' }: { children: ReactNode; className?: string }) {
    return (
        <section className={cx('rounded-2xl border bg-[var(--bg-secondary)]', className)} style={assayPanelSurfaceStyle}>
            {children}
        </section>
    );
}

export function AssayPageHeader({
    eyebrow,
    title,
    description,
    children,
}: {
    eyebrow: string;
    title: string;
    description: string;
    children?: ReactNode;
}) {
    return (
        <AssayPanel className="overflow-hidden p-5">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                <div className="max-w-5xl">
                    <div className="text-xs font-semibold uppercase tracking-[0.28em] text-[var(--accent-primary)]">{eyebrow}</div>
                    <h1 className="mt-2 text-2xl font-bold leading-tight text-[var(--text-primary)] sm:text-3xl">{title}</h1>
                    <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">{description}</p>
                </div>
                {children ? <div className="shrink-0">{children}</div> : null}
            </div>
        </AssayPanel>
    );
}

export function AssayModeTabs<T extends string>({
    items,
    activeId,
    onChange,
    columnsClass = 'md:grid-cols-3',
}: {
    items: Array<AssayNavItem<T>>;
    activeId: T;
    onChange: (id: T) => void;
    columnsClass?: string;
}) {
    return (
        <nav className={cx('grid gap-3', columnsClass)} aria-label="Assay analytics modes">
            {items.map((item) => {
                const active = activeId === item.id;
                return (
                    <button
                        key={item.id}
                        type="button"
                        onClick={() => onChange(item.id)}
                        className={cx(
                            'rounded-2xl border p-4 text-left transition-all focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--bg-primary)] hover:-translate-y-0.5',
                            active ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
                        )}
                        style={active ? assayActiveNavSurfaceStyle : assayNavSurfaceStyle}
                        aria-pressed={active}
                    >
                        <div className={cx('text-[11px] font-semibold uppercase tracking-[0.22em]', active ? 'text-[var(--accent-primary)]' : 'text-[var(--text-muted)]')}>
                            {item.eyebrow ?? item.status}
                        </div>
                        <div className="mt-2 text-base font-semibold">{item.label}</div>
                        <p className="mt-2 text-xs leading-relaxed text-[var(--text-secondary)]">{item.description}</p>
                    </button>
                );
            })}
        </nav>
    );
}

export function AssayWorkbenchIntro({
    eyebrow,
    title,
    description,
    children,
}: {
    eyebrow: string;
    title: string;
    description: string;
    children?: ReactNode;
}) {
    return (
        <AssayPanel className="p-5">
            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-[var(--accent-primary)]">{eyebrow}</div>
            <h2 className="mt-2 text-2xl font-bold text-[var(--text-primary)]">{title}</h2>
            <p className="mt-2 max-w-5xl text-sm leading-6 text-[var(--text-secondary)]">{description}</p>
            {children ? <div className="mt-4">{children}</div> : null}
        </AssayPanel>
    );
}

export function AssayStatusStrip({
    items,
    columnsClass = 'md:grid-cols-3',
}: {
    items: AssayStatusItem[];
    columnsClass?: string;
}) {
    return (
        <div className={cx('grid gap-3', columnsClass)}>
            {items.map((item) => (
                <div
                    key={`${item.title}:${item.value}`}
                    className="rounded-xl border p-3 text-xs"
                    style={mergeSurfaceStyles(assayTileSurfaceStyle, toneStyles[item.tone ?? 'default'])}
                >
                    <div className={cx('font-semibold', item.tone === 'warning' ? 'text-[var(--warning)]' : 'text-[var(--text-primary)]')}>
                        {item.title}
                    </div>
                    <div className="mt-1 leading-relaxed text-[var(--text-muted)]">{item.value}</div>
                </div>
            ))}
        </div>
    );
}

export function AssaySubnavGrid<T extends string>({
    items,
    activeId,
    onChange,
    columnsClass = 'sm:grid-cols-2 xl:grid-cols-4',
}: {
    items: Array<AssayNavItem<T>>;
    activeId: T;
    onChange: (id: T) => void;
    columnsClass?: string;
}) {
    return (
        <div className={cx('grid grid-cols-1 gap-3', columnsClass)}>
            {items.map((item) => {
                const active = activeId === item.id;
                return (
                    <button
                        key={item.id}
                        type="button"
                        onClick={() => onChange(item.id)}
                        className={cx(
                            'rounded-xl border p-4 text-left transition-all focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--bg-primary)] hover:-translate-y-0.5',
                            active ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
                        )}
                        style={active ? assayActiveNavSurfaceStyle : assayNavSurfaceStyle}
                        aria-pressed={active}
                    >
                        <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-[var(--accent-primary)]">
                            {item.status ?? item.eyebrow}
                        </div>
                        <div className="mt-1 text-sm font-semibold">{item.label}</div>
                        <p className="mt-2 text-xs leading-relaxed text-[var(--text-muted)]">{item.description}</p>
                    </button>
                );
            })}
        </div>
    );
}

export function AssaySegmentedTabs<T extends string>({
    items,
    activeId,
    onChange,
    ariaLabel,
    className = '',
}: {
    items: Array<AssaySegmentedTabItem<T>>;
    activeId: T;
    onChange: (id: T) => void;
    ariaLabel: string;
    className?: string;
}) {
    return (
        <div
            className={cx('flex flex-wrap gap-2 rounded-xl border p-1', className)}
            style={assayControlSurfaceStyle}
            role="tablist"
            aria-label={ariaLabel}
        >
            {items.map((item) => {
                const active = activeId === item.id;
                return (
                    <button
                        key={item.id}
                        type="button"
                        role="tab"
                        aria-selected={active}
                        onClick={() => onChange(item.id)}
                        className={cx(
                            'rounded-lg border px-3 py-2 text-sm font-semibold transition focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--bg-primary)]',
                            active ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
                        )}
                        style={
                            active
                                ? {
                                      borderColor: 'color-mix(in srgb, var(--accent-primary) 24%, transparent)',
                                      background: 'color-mix(in srgb, var(--accent-primary) 12%, var(--bg-secondary))',
                                      boxShadow: 'inset 0 1px 0 color-mix(in srgb, var(--accent-primary) 10%, transparent)',
                                  }
                                : {
                                      borderColor: 'transparent',
                                      background: 'transparent',
                                  }
                        }
                    >
                        {item.label}
                    </button>
                );
            })}
        </div>
    );
}

export function AssayInputCard({
    title,
    description,
    children,
    className = '',
}: {
    title?: string;
    description?: string;
    children: ReactNode;
    className?: string;
}) {
    return (
        <div className={cx('rounded-xl border p-4', className)} style={assayTileSurfaceStyle}>
            {title ? <h4 className="text-sm font-semibold text-[var(--text-primary)]">{title}</h4> : null}
            {description ? <p className="mt-1 text-xs leading-relaxed text-[var(--text-muted)]">{description}</p> : null}
            <div className={title || description ? 'mt-3' : ''}>{children}</div>
        </div>
    );
}

export function AssayOutputCard({
    title,
    description,
    children,
    className = '',
}: {
    title?: string;
    description?: string;
    children: ReactNode;
    className?: string;
}) {
    return (
        <div className={cx('rounded-xl border p-4', className)} style={assayTileSurfaceStyle}>
            {title || description ? (
                <div className="mb-3">
                    {title ? <h4 className="text-sm font-semibold text-[var(--text-primary)]">{title}</h4> : null}
                    {description ? <p className="mt-1 text-xs leading-relaxed text-[var(--text-muted)]">{description}</p> : null}
                </div>
            ) : null}
            {children}
        </div>
    );
}

export function AssayEmptyState({ title, description }: { title: string; description: string }) {
    return (
        <div className="rounded-xl border border-dashed p-8 text-center" style={assayQuietSurfaceStyle}>
            <div className="text-sm font-semibold text-[var(--text-primary)]">{title}</div>
            <p className="mx-auto mt-2 max-w-md text-xs leading-relaxed text-[var(--text-muted)]">{description}</p>
        </div>
    );
}

export function AssayPrimaryButton({
    children,
    className = '',
    ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { children: ReactNode }) {
    return (
        <button
            type="button"
            className={cx(
                'rounded-lg bg-[var(--accent-primary)] px-4 py-2 text-sm font-semibold text-white transition hover:bg-[var(--accent-secondary)] disabled:cursor-not-allowed disabled:opacity-50',
                className,
            )}
            {...props}
        >
            {children}
        </button>
    );
}

export function AssayErrorNotice({ message }: { message: string }) {
    return (
        <div className="rounded-lg border border-[var(--error)] bg-[color-mix(in_srgb,var(--error)_14%,transparent)] p-3 text-sm text-[var(--error)]">
            {message}
        </div>
    );
}

export function AssayFieldLabel({
    label,
    helper,
    htmlFor,
}: {
    label: string;
    helper?: string;
    htmlFor?: string;
}) {
    return (
        <label htmlFor={htmlFor} className="block text-xs font-medium text-[var(--text-secondary)]">
            {label}
            {helper ? <span className="mt-1 block font-normal leading-relaxed text-[var(--text-muted)]">{helper}</span> : null}
        </label>
    );
}
