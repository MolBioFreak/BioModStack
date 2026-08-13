/**
 * Layout - Persistent navigation wrapper for all pages
 */

import { Link, useLocation } from 'react-router-dom';
import { useCallback, useState, useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
    buildUiDiagnosticsPayload,
    resolveUiSurfaceLabel,
    type UiDiagnosticsPayload,
    type UiShellContext,
} from '../runtime/uiDiagnostics';
import {
    buildRuntimeSwitchTargets,
    buildTailnetRuntimeSwitchTargets,
    selectTailnetRuntimeEnvironment,
    type RuntimeMode,
    type RuntimePortSettings,
} from '../runtime/runtimeSwitch';
import {
    type BmsFeatureKey,
    isBmsFeatureVisible,
    resolveShowDevFeaturesDefault,
    setBmsFeature as putBmsFeature,
    useBmsFeatureState,
    useBmsFeatures,
} from '../runtime/installFeatures';
import {
    readTailnetEnvironmentStatus,
    type TailnetEnvironmentStatus,
} from '../runtime/tailnetEnvironment';
import { ThemeSelector } from './ThemeSelector';
import { ProjectReturnBanner } from './project-manager/ProjectReturnBanner';
import { buildIdentity } from '../lib/buildIdentity';

import { InfraControlStateCollector } from './InfraLiveTelemetry';
import {
    SHARED_FAN_CONTROL_QUERY_KEY,
    SHARED_POWER_CONTROL_QUERY_KEY,
} from './infraTelemetryQueryKeys';
import {
    fetchFanControl,
    fetchPowerControl,
    setFanControl,
    setPowerControlManual,
    setPowerControlPreset,
} from '../lib/api';

interface LayoutProps {
    children: React.ReactNode;
}

declare global {
    interface Window {
        biomodstack?: {
            getShellContext?: () => Promise<UiShellContext>;
            switchRuntime?: (runtimeMode: RuntimeMode) => Promise<UiShellContext>;
            startRuntimeTarget?: (target: 'dev' | 'prod' | 'both') => Promise<void>;
        };
        cordova?: unknown;
        __BMS_ROUTER_BASENAME__?: string;
    }
}

const API_HEALTH_ENDPOINT = '/api/health';
const RUNTIME_PORTS_ENDPOINT = '/api/system/runtime-ports';
const RUNTIME_START_TARGET_ENDPOINT = '/api/system/runtime/start-target';
type ApiHealthDiagnostics = {
    status: string;
    buildRevision?: string;
};

async function readApiHealth(): Promise<ApiHealthDiagnostics> {
    try {
        const response = await fetch(API_HEALTH_ENDPOINT, { cache: 'no-store' });
        if (!response.ok) {
            return { status: `unhealthy (${response.status})` };
        }
        const body = await response.json().catch(() => null) as {
            status?: unknown;
            build?: { revision?: unknown };
        } | null;
        return {
            status: String(body?.status || 'healthy'),
            buildRevision: typeof body?.build?.revision === 'string'
                ? body.build.revision
                : undefined,
        };
    } catch (error) {
        return { status: `unreachable (${error instanceof Error ? error.message : String(error)})` };
    }
}

async function readRuntimePortSettings(): Promise<RuntimePortSettings> {
    const response = await fetch(RUNTIME_PORTS_ENDPOINT, { cache: 'no-store' });
    if (!response.ok) {
        throw new Error(`runtime ports unavailable (${response.status})`);
    }
    const body = await response.json().catch(() => ({})) as RuntimePortSettings;
    return body;
}

async function startRuntimeTarget(target: 'dev' | 'prod' | 'both'): Promise<void> {
    if (window.biomodstack?.startRuntimeTarget) {
        await window.biomodstack.startRuntimeTarget(target);
        return;
    }
    const response = await fetch(RUNTIME_START_TARGET_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target }),
    });
    if (!response.ok) {
        const body = await response.json().catch(() => null) as { detail?: unknown } | null;
        throw new Error(String(body?.detail || `runtime target start failed (${response.status})`));
    }
}

async function collectUiDiagnosticsPayload(): Promise<UiDiagnosticsPayload> {
    const shellContext = window.biomodstack?.getShellContext
        ? await window.biomodstack.getShellContext().catch(() => null)
        : null;
    const apiHealth = await readApiHealth();
    const routerBasename = shellContext?.routerBasename
        ?? window.__BMS_ROUTER_BASENAME__
        ?? import.meta.env.BASE_URL
        ?? '/';
    const surfaceLabel = resolveUiSurfaceLabel({
        viteDev: Boolean(import.meta.env.DEV),
        electronShell: Boolean(window.biomodstack?.getShellContext),
        cordovaShell: Boolean(window.cordova),
    });

    return buildUiDiagnosticsPayload({
        surfaceLabel,
        origin: window.location.origin,
        href: window.location.href,
        routerBasename,
        viteMode: import.meta.env.MODE || 'unknown',
        viteBaseUrl: import.meta.env.BASE_URL || '/',
        apiHealth: apiHealth.status,
        frontendBuildRevision: buildIdentity.revision,
        apiBuildRevision: apiHealth.buildRevision,
        shellContext,
        userAgent: navigator.userAgent,
    });
}

const SHOW_SYSTEM_ANALYTICS_TAB_KEY = 'show_system_analytics_tab';
const SHOW_DEV_FEATURES_KEY = 'show_dev_features';

function readShowSystemAnalyticsTab(): boolean {
    try {
        return localStorage.getItem(SHOW_SYSTEM_ANALYTICS_TAB_KEY) === 'true';
    } catch {
        return false;
    }
}

function readShowDevFeatures(): boolean {
    try {
        return resolveShowDevFeaturesDefault(
            Boolean(import.meta.env.DEV),
            localStorage.getItem(SHOW_DEV_FEATURES_KEY),
        );
    } catch {
        return Boolean(import.meta.env.DEV);
    }
}

interface DragScrollRailProps extends React.HTMLAttributes<HTMLDivElement> {
    children: React.ReactNode;
    contentClassName: string;
}

function useHorizontalDragScroll() {
    const viewportRef = useRef<HTMLDivElement | null>(null);
    const dragStateRef = useRef({
        pointerId: null as number | null,
        startX: 0,
        scrollLeft: 0,
        moved: false,
    });
    const suppressClickUntilRef = useRef(0);
    const cleanupRef = useRef<(() => void) | null>(null);

    useEffect(() => {
        return () => {
            cleanupRef.current?.();
            cleanupRef.current = null;
        };
    }, []);

    const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
        if (event.button !== 0) {
            return;
        }

        const target = event.target as HTMLElement | null;
        if (target?.closest('[data-bms-drag-scroll-ignore="true"]')) {
            return;
        }

        const viewport = viewportRef.current;
        if (!viewport || viewport.scrollWidth <= viewport.clientWidth + 4) {
            return;
        }

        cleanupRef.current?.();

        dragStateRef.current = {
            pointerId: event.pointerId,
            startX: event.clientX,
            scrollLeft: viewport.scrollLeft,
            moved: false,
        };

        const handlePointerMove = (moveEvent: PointerEvent) => {
            const state = dragStateRef.current;
            if (state.pointerId !== moveEvent.pointerId) {
                return;
            }

            const deltaX = moveEvent.clientX - state.startX;
            if (!state.moved && Math.abs(deltaX) > 6) {
                state.moved = true;
            }
            if (!state.moved) {
                return;
            }

            suppressClickUntilRef.current = Date.now() + 100;
            viewport.scrollLeft = state.scrollLeft - deltaX;
            moveEvent.preventDefault();
        };

        const cleanup = () => {
            window.removeEventListener('pointermove', handlePointerMove);
            window.removeEventListener('pointerup', handlePointerEnd);
            window.removeEventListener('pointercancel', handlePointerEnd);
            cleanupRef.current = null;
            dragStateRef.current = {
                pointerId: null,
                startX: 0,
                scrollLeft: 0,
                moved: false,
            };
        };

        const handlePointerEnd = (endEvent: PointerEvent) => {
            const state = dragStateRef.current;
            if (state.pointerId !== endEvent.pointerId) {
                return;
            }
            if (state.moved) {
                suppressClickUntilRef.current = Date.now() + 100;
            }
            cleanup();
        };

        cleanupRef.current = cleanup;
        window.addEventListener('pointermove', handlePointerMove);
        window.addEventListener('pointerup', handlePointerEnd);
        window.addEventListener('pointercancel', handlePointerEnd);
    };

    const handleClickCapture = (event: React.MouseEvent<HTMLDivElement>) => {
        if (Date.now() < suppressClickUntilRef.current) {
            event.preventDefault();
            event.stopPropagation();
        }
    };

    return {
        viewportRef,
        handlePointerDown,
        handleClickCapture,
    };
}

function DragScrollRail({
    children,
    className,
    contentClassName,
    style,
    ...props
}: DragScrollRailProps) {
    const { viewportRef, handlePointerDown, handleClickCapture } = useHorizontalDragScroll();

    return (
        <div
            {...props}
            ref={viewportRef}
            onPointerDown={handlePointerDown}
            onClickCapture={handleClickCapture}
            className={className}
            style={{ touchAction: 'pan-x', ...style }}
        >
            <div className={contentClassName}>
                {children}
            </div>
        </div>
    );
}

const TOPBAR_NAV_ITEM_CLASSNAME = 'shrink-0 whitespace-nowrap rounded-lg px-[clamp(0.65rem,0.45rem+0.5vw,0.9rem)] py-2 text-[clamp(0.68rem,0.58rem+0.24vw,0.8125rem)] font-medium transition-all';
const TOPBAR_MOBILE_QUERY = '(max-width: 767px)';

function readIsCompactCordovaShell(): boolean {
    if (typeof document === 'undefined') {
        return false;
    }
    return document.documentElement.classList.contains('bms-cordova-compact');
}

function readIsMobileTopbar(): boolean {
    if (readIsCompactCordovaShell()) {
        return true;
    }
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
        return false;
    }
    return window.matchMedia(TOPBAR_MOBILE_QUERY).matches;
}

function useIsMobileTopbar(): boolean {
    const [isMobileTopbar, setIsMobileTopbar] = useState(() => readIsMobileTopbar());

    useEffect(() => {
        if (typeof window === 'undefined') {
            return;
        }

        const updateMobileTopbar = () => setIsMobileTopbar(readIsMobileTopbar());
        const mediaQuery = typeof window.matchMedia === 'function'
            ? window.matchMedia(TOPBAR_MOBILE_QUERY)
            : null;
        const classObserver = typeof MutationObserver === 'function'
            ? new MutationObserver(updateMobileTopbar)
            : null;

        updateMobileTopbar();
        classObserver?.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

        if (mediaQuery && typeof mediaQuery.addEventListener === 'function') {
            mediaQuery.addEventListener('change', updateMobileTopbar);
            return () => {
                mediaQuery.removeEventListener('change', updateMobileTopbar);
                classObserver?.disconnect();
            };
        }

        mediaQuery?.addListener(updateMobileTopbar);
        return () => {
            mediaQuery?.removeListener(updateMobileTopbar);
            classObserver?.disconnect();
        };
    }, []);

    return isMobileTopbar;
}

interface TopbarUtilityControlsProps {
    showSystemMenus: boolean;
    showSystemAnalyticsTab: boolean;
    showDevFeatures: boolean;
    onSetShowSystemAnalyticsTab: (enabled: boolean) => void;
    onSetShowDevFeatures: (enabled: boolean) => void;
}

function TopbarUtilityControls({
    showSystemMenus,
    showSystemAnalyticsTab,
    showDevFeatures,
    onSetShowSystemAnalyticsTab,
    onSetShowDevFeatures,
}: TopbarUtilityControlsProps) {
    return (
        <>
            <ThemeSelector />
            {showSystemMenus && <PowerControlMenu />}

            {showSystemMenus && <MSAServerSettingsMenu />}
            <DebugMenu
                showSystemAnalyticsTab={showSystemAnalyticsTab}
                showDevFeatures={showDevFeatures}
                onSetShowSystemAnalyticsTab={onSetShowSystemAnalyticsTab}
                onSetShowDevFeatures={onSetShowDevFeatures}
            />
        </>
    );
}

function MobileTopbarTools({
    showSystemMenus,
    showSystemAnalyticsTab,
    showDevFeatures,
    onSetShowSystemAnalyticsTab,
    onSetShowDevFeatures,
}: TopbarUtilityControlsProps) {
    const [isOpen, setIsOpen] = useState(false);
    const containerRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        if (!isOpen) {
            return;
        }

        const handlePointerDown = (event: PointerEvent) => {
            if (containerRef.current?.contains(event.target as Node)) {
                return;
            }
            setIsOpen(false);
        };
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                setIsOpen(false);
            }
        };

        document.addEventListener('pointerdown', handlePointerDown);
        document.addEventListener('keydown', handleKeyDown);
        return () => {
            document.removeEventListener('pointerdown', handlePointerDown);
            document.removeEventListener('keydown', handleKeyDown);
        };
    }, [isOpen]);

    return (
        <div ref={containerRef} className="relative" data-bms-mobile-topbar-tools="true">
            <button
                type="button"
                aria-label="Open top-bar controls"
                aria-expanded={isOpen}
                onClick={() => setIsOpen((value) => !value)}
                className="flex min-h-9 items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-semibold tracking-wide transition-colors"
                style={{
                    backgroundColor: isOpen ? 'color-mix(in srgb, var(--accent-primary) 18%, transparent)' : 'var(--bg-tertiary)',
                    borderColor: isOpen ? 'var(--accent-primary)' : 'var(--border-primary)',
                    color: isOpen ? 'var(--accent-primary)' : 'var(--text-secondary)',
                }}
            >
                TOOLS
                <span aria-hidden="true" className="text-[10px] leading-none">{isOpen ? '▲' : '▼'}</span>
            </button>

            {isOpen && (
                <div
                    className="fixed right-2 top-14 z-[80] max-h-[calc(100vh-4rem)] w-[min(94vw,22rem)] overflow-y-auto rounded-xl border p-2 shadow-2xl backdrop-blur-md"
                    style={{
                        backgroundColor: 'color-mix(in srgb, var(--bg-secondary) 95%, transparent)',
                        borderColor: 'var(--border-primary)',
                    }}
                    data-bms-mobile-topbar-tools-panel="true"
                >
                    <div className="mb-2 flex items-center justify-between gap-2 px-1">
                        <span className="text-[11px] font-semibold uppercase tracking-[0.2em]" style={{ color: 'var(--text-muted)' }}>
                            Controls
                        </span>
                        <button
                            type="button"
                            onClick={() => setIsOpen(false)}
                            className="rounded-md px-2 py-1 text-[11px] font-medium transition-colors hover:bg-white/10"
                            style={{ color: 'var(--text-secondary)' }}
                        >
                            Close
                        </button>
                    </div>
                    <div className="grid grid-cols-1 gap-2 overflow-visible [&>div]:w-full [&>div>button]:w-full [&>div>button]:justify-center [&>button]:w-full [&>button]:justify-center">
                        <TopbarUtilityControls
                            showSystemMenus={showSystemMenus}
                            showSystemAnalyticsTab={showSystemAnalyticsTab}
                            showDevFeatures={showDevFeatures}
                            onSetShowSystemAnalyticsTab={onSetShowSystemAnalyticsTab}
                            onSetShowDevFeatures={onSetShowDevFeatures}
                        />
                    </div>
                </div>
            )}
        </div>
    );
}

export function Layout({ children }: LayoutProps) {
    const location = useLocation();
    const isMobileTopbar = useIsMobileTopbar();
    const bmsFeatureState = useBmsFeatureState();
    const [showSystemAnalyticsTab, setShowSystemAnalyticsTab] = useState<boolean>(() => readShowSystemAnalyticsTab());
    const [showDevFeatures, setShowDevFeatures] = useState<boolean>(() => readShowDevFeatures());
    const showBioXpDevFeature = isBmsFeatureVisible(bmsFeatureState, 'bioxp', showDevFeatures);

    const isActive = (path: string) => location.pathname === path;
    const isProjectManagerActive = location.pathname === '/projects' || location.pathname.startsWith('/projects/');
    const showSystemMenus = location.pathname !== '/ngs';

    useEffect(() => {
        const activeTab = document.querySelector<HTMLElement>('[data-bms-primary-nav-active="true"]');
        activeTab?.scrollIntoView({ block: 'nearest', inline: 'center' });
    }, [location.pathname, showSystemAnalyticsTab, showDevFeatures]);

    const handleSetShowSystemAnalyticsTab = (enabled: boolean) => {
        setShowSystemAnalyticsTab(enabled);
        try {
            localStorage.setItem(SHOW_SYSTEM_ANALYTICS_TAB_KEY, String(enabled));
        } catch {
            // Ignore localStorage failures and keep UI responsive.
        }
    };

    const handleSetShowDevFeatures = (enabled: boolean) => {
        setShowDevFeatures(enabled);
        try {
            localStorage.setItem(SHOW_DEV_FEATURES_KEY, String(enabled));
        } catch {
            // Ignore localStorage failures and keep UI responsive.
        }
    };

    return (
        <div
            className="h-screen flex flex-col transition-colors duration-300 overflow-x-hidden"
            style={{
                background: `linear-gradient(to bottom right, var(--bg-gradient-from), var(--bg-gradient-via), var(--bg-gradient-to))`
            }}
        >
            <InfraControlStateCollector />
            {/* Top Navigation Bar */}
            <nav
                className="backdrop-blur-sm border-b flex-shrink-0 z-50 transition-colors duration-300"
                style={{
                    backgroundColor: 'var(--nav-bg)',
                    borderColor: 'var(--border-primary)'
                }}
            >
                <div className="w-full px-2 sm:px-3 lg:px-4">
                    <div className="flex min-w-0 flex-wrap items-center gap-2 py-2 sm:gap-3 2xl:flex-nowrap">
                        <div
                            className={isMobileTopbar
                                ? 'order-1 flex w-full items-center justify-between gap-2'
                                : 'order-1 flex w-full items-center justify-between gap-2 md:w-auto md:shrink-0 md:justify-start'}
                            data-bms-topbar-left="true"
                        >
                            <div className="flex min-w-0 items-center gap-2">
                                <DiagnosticsMenu />
                                {/* Logo / Brand */}
                                <Link to="/" className="flex items-center shrink-0">
                                    <span
                                        className="font-bold whitespace-nowrap text-[clamp(1rem,0.92rem+0.35vw,1.2rem)]"
                                        style={{
                                            color: 'var(--accent-primary)'
                                        }}
                                    >
                                        <span className="inline md:hidden">BMS</span>
                                        <span className="hidden md:inline">BioModStack</span>
                                    </span>
                                </Link>
                            </div>
                            {isMobileTopbar && (
                                <MobileTopbarTools
                                    showSystemMenus={showSystemMenus}
                                    showSystemAnalyticsTab={showSystemAnalyticsTab}
                                    showDevFeatures={showDevFeatures}
                                    onSetShowSystemAnalyticsTab={handleSetShowSystemAnalyticsTab}
                                    onSetShowDevFeatures={handleSetShowDevFeatures}
                                />
                            )}
                        </div>

                        {/* Navigation Links */}
                        <DragScrollRail
                            className={isMobileTopbar
                                ? 'order-2 min-w-0 w-full overflow-x-auto overscroll-x-contain cursor-grab active:cursor-grabbing select-none [scrollbar-width:none] [&::-webkit-scrollbar]:hidden'
                                : 'order-2 min-w-0 w-full overflow-x-auto overscroll-x-contain cursor-grab active:cursor-grabbing select-none [scrollbar-width:none] [&::-webkit-scrollbar]:hidden md:order-3 2xl:order-2 2xl:w-auto 2xl:flex-1 2xl:max-w-full'}
                            contentClassName="flex w-max items-center gap-1.5 pr-1"
                            data-bms-primary-nav-rail="true"
                        >
                            <>
                                <Link
                                    to="/"
                                    data-bms-primary-nav-active={isActive('/') ? 'true' : undefined}
                                    className={TOPBAR_NAV_ITEM_CLASSNAME}
                                    style={{
                                        backgroundColor: isActive('/') ? 'color-mix(in srgb, var(--accent-primary) 20%, transparent)' : 'transparent',
                                        color: isActive('/') ? 'var(--accent-primary)' : 'var(--text-secondary)'
                                    }}
                                >
                                    Dashboard
                                </Link>
                                <Link
                                    to="/projects"
                                    data-bms-primary-nav-active={isProjectManagerActive ? 'true' : undefined}
                                    className={TOPBAR_NAV_ITEM_CLASSNAME}
                                    style={{
                                        backgroundColor: isProjectManagerActive ? 'color-mix(in srgb, var(--accent-primary) 20%, transparent)' : 'transparent',
                                        color: isProjectManagerActive ? 'var(--accent-primary)' : 'var(--text-secondary)'
                                    }}
                                >
                                    Project Manager
                                </Link>
                                <Link
                                    to="/submit"
                                    data-bms-primary-nav-active={isActive('/submit') ? 'true' : undefined}
                                    className={TOPBAR_NAV_ITEM_CLASSNAME}
                                    style={{
                                        backgroundColor: isActive('/submit') ? 'color-mix(in srgb, var(--accent-primary) 20%, transparent)' : 'transparent',
                                        color: isActive('/submit') ? 'var(--accent-primary)' : 'var(--text-secondary)'
                                    }}
                                >
                                    Job Launcher
                                </Link>
                                <Link
                                    to="/designs"
                                    data-bms-primary-nav-active={isActive('/designs') ? 'true' : undefined}
                                    className={TOPBAR_NAV_ITEM_CLASSNAME}
                                    style={{
                                        backgroundColor: isActive('/designs') ? 'color-mix(in srgb, var(--accent-primary) 20%, transparent)' : 'transparent',
                                        color: isActive('/designs') ? 'var(--accent-primary)' : 'var(--text-secondary)'
                                    }}
                                >
                                    Data Viewer
                                </Link>
                                <Link
                                    to={{ pathname: '/designer', search: location.search }}
                                    data-bms-primary-nav-active={isActive('/designer') ? 'true' : undefined}
                                    className={TOPBAR_NAV_ITEM_CLASSNAME}
                                    style={{
                                        backgroundColor: isActive('/designer') ? 'color-mix(in srgb, var(--success) 20%, transparent)' : 'transparent',
                                        color: isActive('/designer') ? 'var(--success)' : 'var(--text-secondary)'
                                    }}
                                    title="Molecular Biology Toolkit"
                                >
                                    Mol Bio Toolkit
                                </Link>
                                <Link
                                    to={{ pathname: '/ngs', search: location.search }}
                                    data-bms-primary-nav-active={isActive('/ngs') ? 'true' : undefined}
                                    className={TOPBAR_NAV_ITEM_CLASSNAME}
                                    style={{
                                        backgroundColor: isActive('/ngs') ? 'color-mix(in srgb, var(--accent-secondary) 20%, transparent)' : 'transparent',
                                        color: isActive('/ngs') ? 'var(--accent-secondary)' : 'var(--text-secondary)'
                                    }}
                                    title="NGS Toolkit"
                                >
                                    NGS Toolkit
                                </Link>
                                <Link
                                    to="/stats"
                                    data-bms-primary-nav-active={isActive('/stats') ? 'true' : undefined}
                                    className={TOPBAR_NAV_ITEM_CLASSNAME}
                                    style={{
                                        backgroundColor: isActive('/stats') ? 'color-mix(in srgb, var(--accent-primary) 20%, transparent)' : 'transparent',
                                        color: isActive('/stats') ? 'var(--accent-primary)' : 'var(--text-secondary)'
                                    }}
                                    title="BioModStack Stats Toolkit"
                                >
                                    Stats Toolkit
                                </Link>

                                {showSystemAnalyticsTab && (
                                    <Link
                                        to="/infra"
                                        data-bms-primary-nav-active={isActive('/infra') ? 'true' : undefined}
                                        className={TOPBAR_NAV_ITEM_CLASSNAME}
                                        style={{
                                            backgroundColor: isActive('/infra') ? 'color-mix(in srgb, var(--accent-primary) 20%, transparent)' : 'transparent',
                                            color: isActive('/infra') ? 'var(--accent-primary)' : 'var(--text-secondary)'
                                        }}
                                        title="System Analytics"
                                    >
                                        System Analytics
                                    </Link>
                                )}
                                {showBioXpDevFeature && (
                                    <Link
                                        to="/bioxp"
                                        data-bms-primary-nav-active={isActive('/bioxp') ? 'true' : undefined}
                                        className={TOPBAR_NAV_ITEM_CLASSNAME}
                                        style={{
                                            backgroundColor: isActive('/bioxp') ? 'color-mix(in srgb, var(--warning) 20%, transparent)' : 'transparent',
                                            color: isActive('/bioxp') ? 'var(--warning)' : 'var(--text-secondary)'
                                        }}
                                        title="BioXP Handler Controls"
                                    >
                                        BioXP Handler
                                    </Link>
                                )}
                            </>
                        </DragScrollRail>

                        {!isMobileTopbar && (
                            <div
                                className="order-2 ml-auto flex max-w-[calc(100vw-1rem)] shrink-0 flex-wrap items-center justify-end gap-1.5 overflow-visible md:flex 2xl:order-3 2xl:max-w-full"
                                data-bms-topbar-utilities="true"
                            >
                                <TopbarUtilityControls
                                    showSystemMenus={showSystemMenus}
                                    showSystemAnalyticsTab={showSystemAnalyticsTab}
                                    showDevFeatures={showDevFeatures}
                                    onSetShowSystemAnalyticsTab={handleSetShowSystemAnalyticsTab}
                                    onSetShowDevFeatures={handleSetShowDevFeatures}
                                />
                            </div>
                        )}
                    </div>
                </div>
            </nav>

            {/* Main Content - takes remaining height, scrollable */}
            <main className="flex-1 overflow-auto">
                <ProjectReturnBanner />
                {children}
            </main>
        </div>
    );
}

function DiagnosticsMenu() {
    const [isOpen, setIsOpen] = useState(false);
    const [diagnostics, setDiagnostics] = useState<UiDiagnosticsPayload | null>(null);
    const [loading, setLoading] = useState(false);
    const [copyStatus, setCopyStatus] = useState<string | null>(null);
    const [runtimeAction, setRuntimeAction] = useState<string | null>(null);
    const [runtimeMessage, setRuntimeMessage] = useState<string | null>(null);
    const [tailnetEnvironment, setTailnetEnvironment] = useState<TailnetEnvironmentStatus | null>(null);
    const [tailnetStatusLoading, setTailnetStatusLoading] = useState(false);

    const refreshDiagnostics = async (): Promise<UiDiagnosticsPayload | null> => {
        setLoading(true);
        setCopyStatus(null);
        try {
            const payload = await collectUiDiagnosticsPayload();
            setDiagnostics(payload);
            return payload;
        } catch (error) {
            const fallback = buildUiDiagnosticsPayload({
                surfaceLabel: 'Diagnostics unavailable',
                origin: window.location.origin,
                href: window.location.href,
                routerBasename: window.__BMS_ROUTER_BASENAME__ ?? import.meta.env.BASE_URL ?? '/',
                viteMode: import.meta.env.MODE || 'unknown',
                viteBaseUrl: import.meta.env.BASE_URL || '/',
                apiHealth: `diagnostics error (${error instanceof Error ? error.message : String(error)})`,
                frontendBuildRevision: buildIdentity.revision,
            });
            setDiagnostics(fallback);
            return fallback;
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (!isOpen || diagnostics || loading) {
            return;
        }
        void refreshDiagnostics();
    }, [diagnostics, isOpen, loading]);

    useEffect(() => {
        if (!isOpen) {
            return;
        }
        let cancelled = false;
        setTailnetStatusLoading(true);
        void readTailnetEnvironmentStatus()
            .then((status) => {
                if (!cancelled) {
                    setTailnetEnvironment(status);
                }
            })
            .catch((error) => {
                if (!cancelled) {
                    setTailnetEnvironment(null);
                    setRuntimeMessage(`Tailnet environment status failed: ${error instanceof Error ? error.message : String(error)}`);
                }
            })
            .finally(() => {
                if (!cancelled) {
                    setTailnetStatusLoading(false);
                }
            });
        return () => {
            cancelled = true;
        };
    }, [isOpen]);

    const handleCopyDiagnostics = async () => {
        const payload = diagnostics ?? await refreshDiagnostics();
        if (!payload) {
            return;
        }
        await navigator.clipboard.writeText(payload.text);
        setCopyStatus('Copied diagnostics');
    };

    const handleStartRuntimeTarget = async (target: 'dev' | 'prod' | 'both') => {
        setRuntimeAction(`start-${target}`);
        setRuntimeMessage(null);
        try {
            await startRuntimeTarget(target);
            setRuntimeMessage(`Started runtime target: ${target}`);
        } catch (error) {
            setRuntimeMessage(`Runtime start failed: ${error instanceof Error ? error.message : String(error)}`);
        } finally {
            setRuntimeAction(null);
        }
    };

    const handleSwitchRuntime = async (targetMode: RuntimeMode) => {
        setRuntimeAction(`switch-${targetMode}`);
        setRuntimeMessage(null);
        try {
            if (tailnetEnvironment) {
                const environment = targetMode === 'dev' ? 'development' : 'production';
                await selectTailnetRuntimeEnvironment(targetMode);
                setTailnetEnvironment({ ...tailnetEnvironment, selected_environment: environment });
                setRuntimeMessage(`Selected Tailnet environment: ${environment}`);
                window.location.assign(new URL('/', window.location.origin).toString());
                return;
            }
            if (window.biomodstack?.switchRuntime) {
                await window.biomodstack.switchRuntime(targetMode);
                return;
            }
            const payload = diagnostics ?? await collectUiDiagnosticsPayload();
            const routerBasename = payload?.fields['Shell router basename']
                ?? payload?.fields['Router basename']
                ?? window.__BMS_ROUTER_BASENAME__
                ?? import.meta.env.BASE_URL
                ?? '/';
            const loopbackHostnames = new Set(['localhost', '127.0.0.1', '::1']);
            if (!loopbackHostnames.has(window.location.hostname)) {
                const targets = buildTailnetRuntimeSwitchTargets({
                    origin: window.location.origin,
                    currentPathname: window.location.pathname,
                    currentSearch: window.location.search,
                    currentHash: window.location.hash,
                    currentRouterBasename: routerBasename,
                });
                await selectTailnetRuntimeEnvironment(targetMode);
                window.location.assign(targetMode === 'dev' ? targets.dev.url : targets.stable.url);
                return;
            }

            const ports = await readRuntimePortSettings().catch(() => ({} as RuntimePortSettings));
            const targets = buildRuntimeSwitchTargets({
                ports,
                currentPathname: window.location.pathname,
                currentSearch: window.location.search,
                currentHash: window.location.hash,
                currentRouterBasename: routerBasename,
            });
            window.location.assign(targetMode === 'dev' ? targets.dev.url : targets.stable.url);
        } catch (error) {
            setRuntimeMessage(`Runtime switch failed: ${error instanceof Error ? error.message : String(error)}`);
            setRuntimeAction(null);
        }
    };

    return (
        <div className="relative shrink-0">
            <button
                onClick={() => {
                    setIsOpen(!isOpen);
                    setCopyStatus(null);
                }}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold uppercase tracking-wider transition-all border bg-emerald-950/40 text-emerald-200 border-emerald-700/70 hover:border-emerald-400 hover:bg-emerald-900/50"
                title="Diagnostics"
            >
                <span aria-hidden="true">ⓘ</span>
                <span>Diagnostics</span>
            </button>

            {isOpen && (
                <>
                    <div
                        className="fixed inset-0 z-40"
                        data-bms-drag-scroll-ignore="true"
                        onClick={() => setIsOpen(false)}
                    />
                    <div
                        className="absolute left-0 top-full mt-2 w-[26rem] max-w-[calc(100vw-1rem)] bg-slate-900 border border-emerald-700/60 rounded-lg shadow-xl z-50 p-3 space-y-3"
                        data-bms-drag-scroll-ignore="true"
                    >
                        <div className="flex items-center justify-between gap-3 border-b border-slate-700 pb-2">
                            <div>
                                <p className="text-xs font-semibold text-emerald-300 uppercase tracking-wider">Diagnostics</p>
                                <p className="text-[11px] text-slate-400">Surface + API</p>
                            </div>
                            <button
                                onClick={() => void refreshDiagnostics()}
                                disabled={loading}
                                className="px-2 py-1 rounded border border-slate-600 text-[11px] text-slate-200 disabled:opacity-50 hover:bg-slate-800"
                            >
                                {loading ? 'Refreshing…' : 'Refresh'}
                            </button>
                        </div>

                        <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded bg-slate-950/70 p-3 text-[11px] leading-relaxed text-slate-200 border border-slate-800">
                            {diagnostics?.text ?? (loading ? 'Collecting diagnostics…' : 'Open diagnostics to collect surface details.')}
                        </pre>

                        <div className="rounded-lg border border-slate-700 bg-slate-950/40 p-3 space-y-2">
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <p className="text-xs font-semibold text-slate-200">
                                        {tailnetEnvironment ? 'Tailnet environment' : 'Runtime channel'}
                                    </p>
                                    <p className="text-[11px] text-slate-400">
                                        {tailnetStatusLoading
                                            ? 'Checking global selector…'
                                            : tailnetEnvironment
                                                ? `Selected: ${tailnetEnvironment.selected_environment}`
                                                : 'Local runtime switch + start'}
                                    </p>
                                </div>
                                {!tailnetEnvironment && !tailnetStatusLoading && (
                                    <button
                                        onClick={() => void handleStartRuntimeTarget('both')}
                                        disabled={runtimeAction !== null}
                                        className="px-2 py-1 rounded border border-slate-600 text-[11px] text-slate-200 disabled:opacity-50 hover:bg-slate-800"
                                    >
                                        {runtimeAction === 'start-both' ? 'Starting…' : 'Start Dev + Stable'}
                                    </button>
                                )}
                            </div>
                            <div className="flex flex-wrap gap-2">
                                <button
                                    onClick={() => void handleSwitchRuntime('dev')}
                                    disabled={runtimeAction !== null || tailnetStatusLoading || tailnetEnvironment?.selected_environment === 'development'}
                                    className="px-3 py-1.5 rounded-lg text-xs font-medium bg-sky-700 text-white hover:bg-sky-600 disabled:opacity-50"
                                >
                                    {runtimeAction === 'switch-dev' ? 'Switching…' : 'Switch to Development'}
                                </button>
                                <button
                                    onClick={() => void handleSwitchRuntime('container')}
                                    disabled={runtimeAction !== null || tailnetStatusLoading || tailnetEnvironment?.selected_environment === 'production'}
                                    className="px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-700 text-white hover:bg-indigo-600 disabled:opacity-50"
                                >
                                    {runtimeAction === 'switch-container' ? 'Switching…' : 'Switch to Production'}
                                </button>
                            </div>
                            {runtimeMessage && <p className="text-[11px] text-slate-300">{runtimeMessage}</p>}
                        </div>

                        <div className="flex items-center justify-between gap-3">
                            <button
                                onClick={() => void handleCopyDiagnostics()}
                                className="px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-700 text-white hover:bg-emerald-600"
                            >
                                Copy diagnostics
                            </button>
                            {copyStatus && <span className="text-[11px] text-emerald-300">{copyStatus}</span>}
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}

function DebugMenu({
    showSystemAnalyticsTab,
    showDevFeatures,
    onSetShowSystemAnalyticsTab,
    onSetShowDevFeatures,
}: {
    showSystemAnalyticsTab: boolean;
    showDevFeatures: boolean;
    onSetShowSystemAnalyticsTab: (enabled: boolean) => void;
    onSetShowDevFeatures: (enabled: boolean) => void;
}) {
    const [isOpen, setIsOpen] = useState(false);
    const [loading, setLoading] = useState<string | null>(null);
    const [result, setResult] = useState<string | null>(null);
    const [featureAction, setFeatureAction] = useState<string | null>(null);
    const queryClient = useQueryClient();
    const bmsFeatures = useBmsFeatures();

    const runCleanup = async (days: number) => {
        const label = days === 0 ? 'full' : `${days}d`;
        setLoading(label);
        setResult(null);
        try {
            const res = await fetch(`/api/system/cleanup-work?days=${days}`, {
                method: 'POST'
            });
            const data = await res.json();
            if (data.success) {
                setResult(`✓ ${data.message}: ${data.files_before - data.files_after} files removed`);
            } else {
                setResult(`✗ ${data.message}`);
            }
        } catch (error) {
            setResult(`✗ Error: ${error}`);
        } finally {
            setLoading(null);
        }
    };

    const setBmsFeature = async (feature: BmsFeatureKey, enabled: boolean) => {
        const label = `${feature}-${enabled ? 'add' : 'remove'}`;
        setFeatureAction(label);
        setResult(null);
        try {
            const features = await putBmsFeature(feature, enabled);
            queryClient.setQueryData(['bms-install-features'], (previous: unknown) => ({
                ...(
                    previous && typeof previous === 'object'
                        ? previous as Record<string, unknown>
                        : {}
                ),
                features,
            }));
            setResult(`✓ ${feature} ${enabled ? 'added' : 'removed'}; restart required`);
        } catch (error) {
            setResult(`✗ Add-on update failed: ${error instanceof Error ? error.message : String(error)}`);
        } finally {
            setFeatureAction(null);
        }
    };

    const addOnButtonClass = 'px-2 py-1 rounded border border-slate-600 text-[11px] text-slate-200 disabled:opacity-50 hover:bg-slate-700';

    return (
        <div className="relative">
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold uppercase tracking-wider transition-all border bg-slate-800 text-slate-300 border-slate-700 hover:border-slate-500 hover:bg-slate-700/50"
            >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
                </svg>
                Debug
            </button>

            {isOpen && (
                <>
                    {/* Backdrop */}
                    <div
                        className="fixed inset-0 z-40"
                        data-bms-drag-scroll-ignore="true"
                        onClick={() => setIsOpen(false)}
                    />

                    {/* Dropdown */}
                    <div
                        className="absolute right-0 top-full mt-2 w-80 max-w-[calc(100vw-1rem)] bg-slate-800 border border-slate-700 rounded-lg shadow-xl z-50 py-2"
                        data-bms-drag-scroll-ignore="true"
                    >
                        <div className="px-3 py-2 border-b border-slate-700">
                            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Navigation</p>
                        </div>

                        <div className="p-2 border-b border-slate-700">
                            <label className="flex items-center justify-between gap-3 px-2 py-2 rounded-lg hover:bg-slate-700/40 transition-colors">
                                <span className="text-sm text-slate-300">Show System Analytics tab</span>
                                <input
                                    type="checkbox"
                                    checked={showSystemAnalyticsTab}
                                    onChange={(event) => onSetShowSystemAnalyticsTab(event.target.checked)}
                                    className="h-4 w-4"
                                />
                            </label>
                            <label className="flex items-center justify-between gap-3 px-2 py-2 rounded-lg hover:bg-slate-700/40 transition-colors">
                                <span>
                                    <span className="block text-sm text-slate-300">Show dev tools</span>
                                    <span className="block text-[11px] text-slate-500">Reveals BioXP development controls</span>
                                </span>
                                <input
                                    type="checkbox"
                                    checked={showDevFeatures}
                                    onChange={(event) => onSetShowDevFeatures(event.target.checked)}
                                    className="h-4 w-4"
                                />
                            </label>
                        </div>

                        <div className="px-3 py-2 border-b border-slate-700">
                            <div className="mb-2 flex items-center justify-between gap-3">
                                <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Install add-ons</p>
                                <span className="text-[10px] text-slate-500">restart required</span>
                            </div>
                            <div className="grid grid-cols-2 gap-2">
                                <button
                                    onClick={() => void setBmsFeature('bioxp', !bmsFeatures.bioxp)}
                                    disabled={featureAction === 'bioxp-add' || featureAction === 'bioxp-remove'}
                                    className={addOnButtonClass}
                                >
                                    {bmsFeatures.bioxp ? 'Remove BioXP' : 'Add BioXP'}
                                </button>

                            </div>
                        </div>

                        <div className="px-3 py-2 border-b border-slate-700">
                            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Cache Cleanup</p>
                        </div>

                        <div className="p-2 space-y-1">
                            <button
                                onClick={() => runCleanup(7)}
                                disabled={loading !== null}
                                className="w-full px-3 py-2 text-left text-sm text-slate-300 hover:bg-slate-700 rounded-lg transition-all flex items-center justify-between disabled:opacity-50"
                            >
                                <span>Purge 7+ day old cache</span>
                                {loading === '7d' && <span className="text-xs text-accent">Running...</span>}
                            </button>

                            <button
                                onClick={() => runCleanup(30)}
                                disabled={loading !== null}
                                className="w-full px-3 py-2 text-left text-sm text-slate-300 hover:bg-slate-700 rounded-lg transition-all flex items-center justify-between disabled:opacity-50"
                            >
                                <span>Purge 30+ day old cache</span>
                                {loading === '30d' && <span className="text-xs text-accent">Running...</span>}
                            </button>

                            <button
                                onClick={() => runCleanup(0)}
                                disabled={loading !== null}
                                className="w-full px-3 py-2 text-left text-sm text-red-400 hover:bg-red-500/10 rounded-lg transition-all flex items-center justify-between disabled:opacity-50"
                            >
                                <span>Full Purge (clear all)</span>
                                {loading === 'full' && <span className="text-xs text-red-400">Running...</span>}
                            </button>
                        </div>

                        {result && (
                            <div className="px-3 py-2 border-t border-slate-700">
                                <p className={`text-xs ${result.startsWith('✓') ? 'text-green-400' : 'text-red-400'}`}>
                                    {result}
                                </p>
                            </div>
                        )}
                    </div>
                </>
            )}
        </div>
    );
}

interface HardwarePowerLimits {
    min: number;
    default: number;
    max: number;
    eco: number;
    name?: string;
}

interface PerGpuPowerStatus {
    current_watts: number;
    saved_watts: number;
    min_watts: number;
    default_watts: number;
    max_watts: number;
    eco_watts: number;
    percentage: number;
    name?: string;
}

interface PerGpuFanStatus {
    gpu_index: number;
    gpu_name: string;
    settings_gpu_target?: number | null;
    fan_targets?: string[];
    mapping_source?: string;
    mode: 'auto' | 'manual' | 'unknown';
    target_percent: number | null;
    current_percent: number | null;
    current_rpm: number | null;
    min_percent: number;
    max_percent: number;
    profile_mode: 'auto' | 'manual' | 'unknown';
    profile_target_percent: number;
    writable: boolean;
    warning?: string | null;
}

interface PowerControlState {
    limits: Record<string, number>;
    saved_limits: Record<string, number>;
    enabled: boolean;
    eco_mode: boolean;
    power_percentage: number;
    total_current_watts: number;
    total_max_watts: number;
    total_default_watts?: number;
    hardware_limits: Record<string, HardwarePowerLimits>;
    per_gpu: Record<string, PerGpuPowerStatus>;
}

interface FanControlState {
    supported: boolean;
    message: string;
    backend: string;
    available_modes: string[];
    gpus: Record<string, PerGpuFanStatus>;
}

const toNumericRecord = (value: UntypedApiValue): Record<string, number> => {
    if (!value || typeof value !== 'object') return {};
    const out: Record<string, number> = {};
    Object.entries(value).forEach(([k, v]) => {
        const parsed = Number(v);
        if (!Number.isNaN(parsed)) out[String(k)] = parsed;
    });
    return out;
};

const normalizePowerState = (raw: UntypedApiValue): PowerControlState => {
    return {
        limits: toNumericRecord(raw?.limits),
        saved_limits: toNumericRecord(raw?.saved_limits),
        enabled: Boolean(raw?.enabled),
        eco_mode: Boolean(raw?.eco_mode),
        power_percentage: Number(raw?.power_percentage ?? 0),
        total_current_watts: Number(raw?.total_current_watts ?? 0),
        total_max_watts: Number(raw?.total_max_watts ?? 0),
        total_default_watts: Number(raw?.total_default_watts ?? 0),
        hardware_limits: (raw?.hardware_limits || {}) as Record<string, HardwarePowerLimits>,
        per_gpu: (raw?.per_gpu || {}) as Record<string, PerGpuPowerStatus>,
    };
};

const normalizeFanState = (raw: UntypedApiValue): FanControlState => {
    const gpus: Record<string, PerGpuFanStatus> = {};
    if (raw?.gpus && typeof raw.gpus === 'object') {
        Object.entries(raw.gpus).forEach(([key, value]: [string, UntypedApiValue]) => {
            gpus[String(key)] = {
                gpu_index: Number(value?.gpu_index ?? Number(key)),
                gpu_name: String(value?.gpu_name || `GPU ${key}`),
                settings_gpu_target: value?.settings_gpu_target != null ? Number(value.settings_gpu_target) : null,
                fan_targets: Array.isArray(value?.fan_targets)
                    ? value.fan_targets.map((v: UntypedApiValue) => String(v)).filter((v: string) => v.length > 0)
                    : [],
                mapping_source: value?.mapping_source ? String(value.mapping_source) : '',
                mode: (value?.mode || 'unknown') as 'auto' | 'manual' | 'unknown',
                target_percent: value?.target_percent != null ? Number(value.target_percent) : null,
                current_percent: value?.current_percent != null ? Number(value.current_percent) : null,
                current_rpm: value?.current_rpm != null ? Number(value.current_rpm) : null,
                min_percent: Number(value?.min_percent ?? 30),
                max_percent: Number(value?.max_percent ?? 100),
                profile_mode: (value?.profile_mode || 'unknown') as 'auto' | 'manual' | 'unknown',
                profile_target_percent: Number(value?.profile_target_percent ?? 35),
                writable: Boolean(value?.writable),
                warning: value?.warning ? String(value.warning) : null,
            };
        });
    }
    return {
        supported: Boolean(raw?.supported),
        message: String(raw?.message || ''),
        backend: String(raw?.backend || 'unknown'),
        available_modes: Array.isArray(raw?.available_modes)
            ? raw.available_modes.map((m: UntypedApiValue) => String(m)).filter((m: string) => m.length > 0)
            : [],
        gpus,
    };
};

function PowerControlMenu() {
    const queryClient = useQueryClient();
    const [isOpen, setIsOpen] = useState(false);
    const [loading, setLoading] = useState<string | null>(null);
    const [state, setState] = useState<PowerControlState | null>(null);
    const [fanState, setFanState] = useState<FanControlState | null>(null);
    const [draftLimits, setDraftLimits] = useState<Record<string, number>>({});
    const [draftFanTargets, setDraftFanTargets] = useState<Record<string, number>>({});
    const [draftFanModes, setDraftFanModes] = useState<Record<string, 'auto' | 'manual'>>({});
    const [message, setMessage] = useState<string | null>(null);

    const { data: powerControlData } = useQuery({
        queryKey: SHARED_POWER_CONTROL_QUERY_KEY,
        queryFn: fetchPowerControl,
        enabled: false,
        staleTime: Infinity,
        refetchOnWindowFocus: false,
    });

    const { data: fanControlData } = useQuery({
        queryKey: SHARED_FAN_CONTROL_QUERY_KEY,
        queryFn: fetchFanControl,
        enabled: false,
        staleTime: Infinity,
        refetchOnWindowFocus: false,
    });

    const syncFanDrafts = useCallback((nextFanState: FanControlState) => {
        const nextTargets: Record<string, number> = {};
        const nextModes: Record<string, 'auto' | 'manual'> = {};
        Object.entries(nextFanState.gpus).forEach(([gpuKey, gpu]) => {
            const liveMode: 'auto' | 'manual' = gpu.mode === 'manual' ? 'manual' : 'auto';
            const fallbackTarget = liveMode === 'manual'
                ? (gpu.target_percent ?? gpu.profile_target_percent ?? gpu.min_percent ?? 35)
                : (gpu.current_percent ?? gpu.min_percent ?? 35);
            nextTargets[gpuKey] = Number(fallbackTarget);
            nextModes[gpuKey] = liveMode;
        });
        setDraftFanTargets(nextTargets);
        setDraftFanModes(nextModes);
    }, []);

    const syncPowerFromCache = useCallback((syncDrafts: boolean) => {
        const cached = queryClient.getQueryData<UntypedApiValue>(SHARED_POWER_CONTROL_QUERY_KEY);
        if (!cached?.data) return;
        const normalized = normalizePowerState(cached.data);
        setState(normalized);
        if (syncDrafts) {
            setDraftLimits(normalized.limits);
        }
    }, [queryClient]);

    const syncFanFromCache = useCallback((syncDrafts: boolean) => {
        const cached = queryClient.getQueryData<UntypedApiValue>(SHARED_FAN_CONTROL_QUERY_KEY);
        if (!cached?.data) return;
        const normalized = normalizeFanState(cached.data);
        setFanState(normalized);
        if (syncDrafts) {
            syncFanDrafts(normalized);
        }
    }, [queryClient, syncFanDrafts]);

    const refreshHardwareState = useCallback(async (syncDrafts: boolean) => {
        try {
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: SHARED_POWER_CONTROL_QUERY_KEY }),
                queryClient.invalidateQueries({ queryKey: SHARED_FAN_CONTROL_QUERY_KEY }),
            ]);
            syncPowerFromCache(syncDrafts);
            syncFanFromCache(syncDrafts);
        } catch (error) {
            console.error('Failed to refresh hardware control state:', error);
        }
    }, [queryClient, syncFanFromCache, syncPowerFromCache]);

    useEffect(() => {
        if (!powerControlData?.data) return;
        syncPowerFromCache(false);
    }, [powerControlData, queryClient, syncPowerFromCache]);

    useEffect(() => {
        if (!fanControlData?.data) return;
        syncFanFromCache(false);
    }, [fanControlData, queryClient, syncFanFromCache]);

    useEffect(() => {
        if (!isOpen) return;
        setMessage(null);
        syncPowerFromCache(true);
        syncFanFromCache(true);
        void refreshHardwareState(true);
    }, [isOpen, refreshHardwareState, syncFanFromCache, syncPowerFromCache]);

    const applyPowerControl = async (payload: Record<string, UntypedApiValue>, loadingKey: string) => {
        setLoading(loadingKey);
        setMessage(null);
        try {
            let response;
            if (payload.preset === 'eco' || payload.preset === 'stock') {
                response = await setPowerControlPreset(payload.preset);
            } else {
                response = await setPowerControlManual(Number(payload.gpu_index), Number(payload.limit_watts));
            }
            await queryClient.invalidateQueries({ queryKey: SHARED_POWER_CONTROL_QUERY_KEY });
            syncPowerFromCache(true);
            const ok = response.data?.success !== false;
            setMessage(`${ok ? '✓' : '✗'} ${response.data?.message || 'Updated power limits'}`);
        } catch (error) {
            const msg =
                typeof error === 'object' && error && 'response' in error
                    ? String((error as UntypedApiValue).response?.data?.detail || (error as UntypedApiValue).response?.data?.message || (error as UntypedApiValue).message || error)
                    : error instanceof Error
                        ? error.message
                        : String(error);
            setMessage(`✗ ${msg}`);
        } finally {
            setLoading(null);
        }
    };

    const applyFanControl = async (payload: Record<string, UntypedApiValue>, loadingKey: string) => {
        setLoading(loadingKey);
        setMessage(null);
        try {
            const response = await setFanControl(
                Number(payload.gpu_index),
                payload.mode,
                payload.target_percent != null ? Number(payload.target_percent) : undefined,
            );
            await queryClient.invalidateQueries({ queryKey: SHARED_FAN_CONTROL_QUERY_KEY });
            syncFanFromCache(true);
            const ok = response.data?.success !== false;
            setMessage(`${ok ? '✓' : '✗'} ${response.data?.message || 'Updated fan control'}`);
        } catch (error) {
            const msg =
                typeof error === 'object' && error && 'response' in error
                    ? String((error as UntypedApiValue).response?.data?.detail || (error as UntypedApiValue).response?.data?.message || (error as UntypedApiValue).message || error)
                    : error instanceof Error
                        ? error.message
                        : String(error);
            setMessage(`✗ ${msg}`);
        } finally {
            setLoading(null);
        }
    };

    const setPreset = async (preset: 'eco' | 'stock') => {
        await applyPowerControl({ preset }, `preset-${preset}`);
    };

    const setGpuLimit = async (gpuKey: string, watts: number, source: string) => {
        await applyPowerControl(
            {
                gpu_index: Number(gpuKey),
                limit_watts: watts,
            },
            `${source}-${gpuKey}`,
        );
    };

    const commitSlider = async (gpuKey: string) => {
        if (!state) return;
        const current = Number(state.limits[gpuKey]);
        const draft = Number(draftLimits[gpuKey]);
        if (Number.isNaN(current) || Number.isNaN(draft) || current === draft) return;
        await setGpuLimit(gpuKey, draft, 'slider');
    };

    const setFanMode = async (gpuKey: string, mode: 'auto' | 'manual') => {
        const currentTarget = Number(
            draftFanTargets[gpuKey]
            ?? fanState?.gpus[gpuKey]?.target_percent
            ?? fanState?.gpus[gpuKey]?.profile_target_percent
            ?? 35
        );
        setDraftFanModes((prev) => ({ ...prev, [gpuKey]: mode }));
        await applyFanControl(
            { gpu_index: Number(gpuKey), mode, target_percent: currentTarget },
            `fan-mode-${gpuKey}`,
        );
    };

    const commitFanSlider = async (gpuKey: string) => {
        const fan = fanState?.gpus[gpuKey];
        if (!fan) return;
        const draftTarget = Number(draftFanTargets[gpuKey]);
        if (Number.isNaN(draftTarget)) return;
        if (!fan.writable) {
            setMessage(`✗ Fan control is not writable on GPU ${gpuKey}`);
            return;
        }
        const effectiveMode = (fan.mode === 'manual' || fan.profile_mode === 'manual') ? 'manual' : 'auto';
        const current = Number(fan.target_percent ?? fan.profile_target_percent ?? draftTarget);
        if (effectiveMode === 'manual' && current === draftTarget) return;
        await applyFanControl(
            { gpu_index: Number(gpuKey), mode: 'manual', target_percent: draftTarget },
            `fan-slider-${gpuKey}`,
        );
    };

    const commitFanPreset = async (gpuKey: string, pct: number) => {
        const fan = fanState?.gpus[gpuKey];
        if (!fan) return;
        if (!fan.writable) {
            setMessage(`✗ Fan control is not writable on GPU ${gpuKey}`);
            return;
        }
        const fanMin = Number(fan.min_percent ?? 30);
        const fanMax = Number(fan.max_percent ?? 100);
        const clamped = Math.max(fanMin, Math.min(fanMax, pct));
        setDraftFanTargets((prev) => ({ ...prev, [gpuKey]: clamped }));
        setDraftFanModes((prev) => ({ ...prev, [gpuKey]: 'manual' }));
        await applyFanControl(
            { gpu_index: Number(gpuKey), mode: 'manual', target_percent: clamped },
            `fan-preset-${gpuKey}`,
        );
    };

    const rows = state
        ? Object.keys(state.hardware_limits).sort((a, b) => Number(a) - Number(b))
        : [];

    const anyFanManual = fanState
        ? Object.values(fanState.gpus).some((g) => g.mode === 'manual' || g.profile_mode === 'manual')
        : false;
    const fanBackend = fanState?.backend || 'unknown';
    const fanBackendLabel = fanBackend === 'coolercontrol' ? 'CoolerControl' : fanBackend;
    const coolerControlActive = fanBackend === 'coolercontrol';
    const controlsEnabled = Boolean(state?.enabled || anyFanManual);
    const buttonIndicatorClass = controlsEnabled ? 'bg-emerald-400' : 'bg-slate-600';
    const buttonClass = controlsEnabled
        ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40 hover:bg-emerald-500/25'
        : 'bg-slate-800 text-slate-300 border-slate-700 hover:border-slate-500';

    return (
        <div className="relative">
            <button
                onClick={() => setIsOpen(!isOpen)}
                className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold uppercase tracking-wider transition-all border ${buttonClass}`}
            >
                <div className={`w-2 h-2 rounded-full ${buttonIndicatorClass}`} />
                POWER LIMITS
            </button>

            {isOpen && (
                <>
                    <div className="fixed inset-0 z-40" data-bms-drag-scroll-ignore="true" onClick={() => setIsOpen(false)} />
                    <div
                        className="fixed right-2 top-12 w-[min(1060px,calc(100vw-1rem))] max-h-[calc(100vh-4rem)] overflow-y-auto bg-slate-800 border border-slate-700 rounded-lg shadow-xl z-50 p-3 space-y-3"
                        data-bms-drag-scroll-ignore="true"
                    >
                        <div className="flex items-center justify-between border-b border-slate-700 pb-2">
                            <div>
                                <p className="text-xs font-semibold text-slate-300 uppercase tracking-wider">GPU Hardware Controls</p>
                                <p className="text-[11px] text-slate-400 leading-4">
                                    {state
                                        ? `${state.total_current_watts}W current / ${state.total_max_watts}W max`
                                        : 'Loading...'}
                                    {fanState ? ` | Fan backend: ${fanBackendLabel}` : ''}
                                    {fanState && !fanState.supported ? ` | Fan control unavailable: ${fanState.message || 'unsupported'}` : ''}
                                </p>
                            </div>
                            <div className="flex items-center gap-2">
                                <button
                                    onClick={() => {
                                        void refreshHardwareState(true);
                                    }}
                                    disabled={loading !== null}
                                    className="px-2 py-1 text-xs rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                                >
                                    Refresh
                                </button>
                                <button
                                    onClick={() => setPreset('eco')}
                                    disabled={loading !== null}
                                    className="px-2.5 py-1 text-xs rounded border border-emerald-500/40 text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
                                >
                                    Eco All
                                </button>
                                <button
                                    onClick={() => setPreset('stock')}
                                    disabled={loading !== null}
                                    className="px-2.5 py-1 text-xs rounded border border-slate-500/50 text-slate-200 hover:bg-slate-700 disabled:opacity-50"
                                >
                                    Stock All
                                </button>
                            </div>
                        </div>

                        {coolerControlActive && (
                            <div className="rounded border border-cyan-500/40 bg-cyan-500/10 px-2 py-1.5 text-[11px] text-cyan-100">
                                <span className="font-semibold text-cyan-200">CoolerControl active.</span>{' '}
                                Per-GPU channel control.
                                {fanState?.available_modes?.length ? (
                                    <span className="text-cyan-200"> Modes available: {fanState.available_modes.join(', ')}</span>
                                ) : null}
                            </div>
                        )}

                        <div className="grid grid-cols-[2fr_1.1fr_1.8fr_3.2fr_3.6fr] gap-2 px-1 text-[11px] uppercase tracking-wider text-slate-400">
                            <div>GPU</div>
                            <div>Power</div>
                            <div>Power Quick</div>
                            <div>Power Slider (stock marker shown as |)</div>
                            <div>Fan Control</div>
                        </div>

                        <div className="max-h-[420px] overflow-auto space-y-2 pr-1">
                            {rows.map((gpuKey) => {
                                const hw = state?.hardware_limits[gpuKey];
                                if (!hw) return null;

                                const minW = Number(hw.min);
                                const maxW = Number(hw.max);
                                const defaultW = Number(hw.default);
                                const ecoW = Number(hw.eco);
                                const currentW = Number(draftLimits[gpuKey] ?? state?.limits[gpuKey] ?? defaultW);
                                const range = Math.max(1, maxW - minW);
                                const currentPct = Math.round(((currentW - minW) / range) * 100);
                                const stockPct = ((defaultW - minW) / range) * 100;
                                const isPowerLoading = loading?.endsWith(`-${gpuKey}`) && !loading.startsWith('fan-');
                                const fan = fanState?.gpus[gpuKey];
                                const fanMode = draftFanModes[gpuKey] || (fan?.mode === 'manual' ? 'manual' : 'auto');
                                const fanMin = Number(fan?.min_percent ?? 30);
                                const fanMax = Number(fan?.max_percent ?? 100);
                                const fanTarget = Number(
                                    draftFanTargets[gpuKey]
                                    ?? fan?.target_percent
                                    ?? fan?.profile_target_percent
                                    ?? 35
                                );
                                const fanCurrent = fan?.current_percent != null ? `${fan.current_percent}%` : 'n/a';
                                const fanRpm = fan?.current_rpm != null ? `${fan.current_rpm} rpm` : 'n/a';
                                const isFanLoading = loading?.startsWith('fan-') && loading?.endsWith(`-${gpuKey}`);
                                const isGlobalPowerLoading = Boolean(loading?.startsWith('preset-'));
                                const disablePowerControls = isPowerLoading || isGlobalPowerLoading;
                                const disableFanControls = isFanLoading;

                                return (
                                    <div key={gpuKey} className="grid grid-cols-[2fr_1.1fr_1.8fr_3.2fr_3.6fr] gap-2 items-center bg-slate-900/60 border border-slate-700 rounded-lg px-2 py-2">
                                        <div className="text-sm text-slate-200">
                                            <span className="text-slate-400 mr-2">GPU {gpuKey}</span>
                                            <span className="text-slate-200">| {hw.name || `GPU ${gpuKey}`}</span>
                                        </div>

                                        <div className="text-sm text-slate-100">
                                            {currentW}W <span className="text-slate-400">({currentPct}%)</span>
                                        </div>

                                        <div className="flex items-center gap-1">
                                            <button
                                                onClick={() => setGpuLimit(gpuKey, minW, 'quick-min')}
                                                disabled={disablePowerControls}
                                                className="px-1.5 py-0.5 text-[11px] rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                                            >
                                                Min
                                            </button>
                                            <button
                                                onClick={() => setGpuLimit(gpuKey, ecoW, 'quick-eco')}
                                                disabled={disablePowerControls}
                                                className="px-1.5 py-0.5 text-[11px] rounded border border-emerald-500/40 text-emerald-300 hover:bg-emerald-500/15 disabled:opacity-50"
                                            >
                                                Eco
                                            </button>
                                            <button
                                                onClick={() => setGpuLimit(gpuKey, defaultW, 'quick-stock')}
                                                disabled={disablePowerControls}
                                                className="px-1.5 py-0.5 text-[11px] rounded border border-slate-500/50 text-slate-200 hover:bg-slate-700 disabled:opacity-50"
                                            >
                                                Stock
                                            </button>
                                            <button
                                                onClick={() => setGpuLimit(gpuKey, maxW, 'quick-max')}
                                                disabled={disablePowerControls}
                                                className="px-1.5 py-0.5 text-[11px] rounded border border-amber-500/40 text-amber-300 hover:bg-amber-500/15 disabled:opacity-50"
                                            >
                                                Max
                                            </button>
                                        </div>

                                        <div className="space-y-1">
                                            <div className="relative">
                                                <div
                                                    className="absolute inset-y-0 pointer-events-none"
                                                    style={{ left: `${Math.max(0, Math.min(100, stockPct))}%` }}
                                                >
                                                    <div className="h-full w-px bg-slate-300/80" />
                                                </div>
                                                <input
                                                    type="range"
                                                    min={minW}
                                                    max={maxW}
                                                    step={1}
                                                    value={currentW}
                                                    onChange={(e) => {
                                                        const next = Number(e.target.value);
                                                        setDraftLimits((prev) => ({ ...prev, [gpuKey]: next }));
                                                    }}
                                                    onMouseUp={() => commitSlider(gpuKey)}
                                                    onTouchEnd={() => commitSlider(gpuKey)}
                                                    onKeyUp={(e) => {
                                                        if (e.key === 'Enter') {
                                                            commitSlider(gpuKey);
                                                        }
                                                    }}
                                                    disabled={disablePowerControls}
                                                    className="w-full accent-cyan-400 disabled:opacity-50"
                                                />
                                            </div>
                                            <div className="flex items-center justify-between text-[11px] text-slate-500">
                                                <span>{minW}W</span>
                                                <span>Stock {defaultW}W</span>
                                                <span>{maxW}W</span>
                                            </div>
                                            {isPowerLoading && (
                                                <div className="text-[11px] text-cyan-300">Applying...</div>
                                            )}
                                        </div>

                                        <div className="space-y-1">
                                            {!fanState?.supported || !fan ? (
                                                <div className="text-[11px] text-slate-500">
                                                    {fanState?.message || 'Fan control unavailable'}
                                                </div>
                                            ) : (
                                                <>
                                                    <div className="flex items-center gap-1 flex-wrap">
                                                        <button
                                                            onClick={() => setFanMode(gpuKey, 'auto')}
                                                            disabled={disableFanControls || !fan.writable}
                                                            className={`px-1.5 py-0.5 text-[11px] rounded border disabled:opacity-50 ${fanMode === 'auto' ? 'border-emerald-500/50 text-emerald-300 bg-emerald-500/15' : 'border-slate-600 text-slate-300 hover:bg-slate-700'}`}
                                                        >
                                                            Auto
                                                        </button>
                                                        <button
                                                            onClick={() => setFanMode(gpuKey, 'manual')}
                                                            disabled={disableFanControls || !fan.writable}
                                                            className={`px-1.5 py-0.5 text-[11px] rounded border disabled:opacity-50 ${fanMode === 'manual' ? 'border-amber-500/50 text-amber-300 bg-amber-500/15' : 'border-slate-600 text-slate-300 hover:bg-slate-700'}`}
                                                        >
                                                            Manual
                                                        </button>
                                                        <span className="border-l border-slate-600 h-4 mx-0.5" />
                                                        <button
                                                            onClick={() => commitFanPreset(gpuKey, 25)}
                                                            disabled={disableFanControls || !fan.writable}
                                                            className="px-1.5 py-0.5 text-[11px] rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                                                        >
                                                            25%
                                                        </button>
                                                        <button
                                                            onClick={() => commitFanPreset(gpuKey, 50)}
                                                            disabled={disableFanControls || !fan.writable}
                                                            className="px-1.5 py-0.5 text-[11px] rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                                                        >
                                                            50%
                                                        </button>
                                                        <button
                                                            onClick={() => commitFanPreset(gpuKey, 75)}
                                                            disabled={disableFanControls || !fan.writable}
                                                            className="px-1.5 py-0.5 text-[11px] rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                                                        >
                                                            75%
                                                        </button>
                                                        <span className="text-[11px] text-slate-500 ml-1">
                                                            now {fanCurrent} ({fanRpm})
                                                        </span>
                                                    </div>

                                                    <input
                                                        type="range"
                                                        min={fanMin}
                                                        max={fanMax}
                                                        step={1}
                                                        value={fanTarget}
                                                        disabled={disableFanControls}
                                                        onChange={(e) => {
                                                            const next = Number(e.target.value);
                                                            setDraftFanTargets((prev) => ({ ...prev, [gpuKey]: next }));
                                                            // Dragging the fan slider implies manual target control.
                                                            setDraftFanModes((prev) => ({ ...prev, [gpuKey]: 'manual' }));
                                                        }}
                                                        onMouseUp={() => commitFanSlider(gpuKey)}
                                                        onTouchEnd={() => commitFanSlider(gpuKey)}
                                                        onKeyUp={(e) => {
                                                            if (e.key === 'Enter') commitFanSlider(gpuKey);
                                                        }}
                                                        className="w-full accent-amber-400 disabled:opacity-50"
                                                    />
                                                    <div className="flex items-center justify-between text-[11px] text-slate-500">
                                                        <span>{fanMin}%</span>
                                                        <span>target {fanTarget}%</span>
                                                        <span>{fanMax}%</span>
                                                    </div>
                                                    {coolerControlActive ? (
                                                        <div className="text-[10px] text-cyan-300/90">
                                                            CoolerControl channels: {fan.fan_targets && fan.fan_targets.length > 0 ? fan.fan_targets.join(',') : 'none'}
                                                        </div>
                                                    ) : (
                                                        <div className="text-[10px] text-slate-500">
                                                            map gpu:{fan.settings_gpu_target != null ? fan.settings_gpu_target : 'n/a'} fans:{fan.fan_targets && fan.fan_targets.length > 0 ? fan.fan_targets.join(',') : 'none'} ({fan.mapping_source || 'n/a'})
                                                        </div>
                                                    )}
                                                    {fan.warning && (
                                                        <div className="text-[11px] text-amber-300">{fan.warning}</div>
                                                    )}
                                                    {isFanLoading && (
                                                        <div className="text-[11px] text-amber-300">Applying...</div>
                                                    )}
                                                </>
                                            )}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>

                        {message && (
                            <div className={`text-xs ${message.startsWith('✗') ? 'text-rose-300' : 'text-emerald-300'}`}>
                                {message}
                            </div>
                        )}
                    </div>
                </>
            )}
        </div>
    );
}

function MSAServerSettingsMenu() {
    const [isOpen, setIsOpen] = useState(false);
    const [loading, setLoading] = useState<string | null>(null);
    const [status, setStatus] = useState<UntypedApiValue>(null);
    const [availableGpus, setAvailableGpus] = useState<Array<{ index: number; name: string; memory_total_mb?: number }>>([]);
    const [settings, setSettings] = useState({
        include_envdb_on_start: false,
        auto_stop_idle_enabled: false,
        auto_stop_idle_minutes: 10,
        pinned_gpu_id: null as number | null,
    });

    const fetchState = async () => {
        try {
            const [statusRes, settingsRes, gpuRes] = await Promise.all([
                fetch('/api/msa/server/status'),
                fetch('/api/msa/server/settings'),
                fetch('/api/gpu/gpus'),
            ]);

            if (statusRes.ok) {
                const statusData = await statusRes.json();
                setStatus(statusData);
                if (statusData?.settings) {
                    setSettings((prev) => ({ ...prev, ...statusData.settings }));
                }
            }

            if (settingsRes.ok) {
                const settingsData = await settingsRes.json();
                if (settingsData?.settings) {
                    setSettings((prev) => ({ ...prev, ...settingsData.settings }));
                }
            }

            if (gpuRes.ok) {
                const gpuData = await gpuRes.json();
                if (Array.isArray(gpuData?.gpus)) {
                    setAvailableGpus(gpuData.gpus);
                }
            }
        } catch (error) {
            console.error('Failed to fetch MSA server state:', error);
        }
    };

    useEffect(() => {
        if (!isOpen) {
            return;
        }
        fetchState();
        const interval = setInterval(fetchState, 10000);
        return () => clearInterval(interval);
    }, [isOpen]);

    const saveSettings = async (patch: Partial<typeof settings>) => {
        const next = { ...settings, ...patch };
        setSettings(next);
        setLoading('save');
        try {
            await fetch('/api/msa/server/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patch)
            });
            await fetchState();
        } catch (error) {
            console.error('Failed to save MSA server settings:', error);
        } finally {
            setLoading(null);
        }
    };

    const startServers = async () => {
        setLoading('start');
        try {
            await fetch('/api/msa/server/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    include_envdb: settings.include_envdb_on_start,
                    gpu_id: settings.pinned_gpu_id ?? undefined,
                })
            });
            await fetchState();
        } catch (error) {
            console.error('Failed to start MSA server:', error);
        } finally {
            setLoading(null);
        }
    };

    const stopServers = async () => {
        setLoading('stop');
        try {
            await fetch('/api/msa/server/stop', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    gpu_id: settings.pinned_gpu_id ?? undefined,
                })
            });
            await fetchState();
        } catch (error) {
            console.error('Failed to stop MSA server:', error);
        } finally {
            setLoading(null);
        }
    };

    const running = Boolean(status?.running);
    const allRunning = Boolean(status?.all_running);
    const indicatorClass = allRunning ? 'bg-blue-400' : running ? 'bg-amber-400' : 'bg-slate-600';
    const serverLines = Array.isArray(status?.servers)
        ? status.servers.map((srv: UntypedApiValue) => {
            const state = srv?.running ? 'RUNNING' : 'STOPPED';
            const alias = srv?.db_alias || 'unknown';
            const pid = srv?.pid ?? 'n/a';
            const gpu = srv?.cuda_visible_devices ?? 'n/a';
            return `${alias.padEnd(7)} ${state.padEnd(8)} pid=${pid} gpu=${gpu}`;
        })
        : [];
    const summary = [
        `Pinned GPU: ${settings.pinned_gpu_id ?? 'auto'}`,
        `GPU: ${status?.effective_gpu_id ?? 'n/a'}`,
        `Running: ${running ? 'yes' : 'no'} | Full target set: ${allRunning ? 'yes' : 'no'}`,
        `Expected DBs: ${(status?.expected_aliases || []).join(', ') || 'n/a'}`,
        `Include EnvDB on start: ${settings.include_envdb_on_start ? 'yes' : 'no'}`,
        `Idle auto-stop: ${settings.auto_stop_idle_enabled ? `enabled (${settings.auto_stop_idle_minutes} min)` : 'disabled'}`,
        `Idle seconds: ${typeof status?.idle_seconds === 'number' ? Math.round(status.idle_seconds) : 'n/a'}`,
        `Last query: ${status?.query_activity?.updated_at || 'n/a'}`,
        status?.auto_stop_reason ? `Auto-stop note: ${status.auto_stop_reason}` : '',
        '',
        'Servers:',
        ...(serverLines.length > 0 ? serverLines : ['none'])
    ].filter(Boolean).join('\n');

    return (
        <div className="relative">
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold uppercase tracking-wider transition-all border bg-slate-800 text-slate-300 border-slate-700 hover:border-slate-500"
            >
                <div className={`w-2 h-2 rounded-full ${indicatorClass}`} />
                MSA SERVER
            </button>

            {isOpen && (
                <>
                    <div className="fixed inset-0 z-40" data-bms-drag-scroll-ignore="true" onClick={() => setIsOpen(false)} />

                    <div
                        className="absolute right-0 top-full mt-2 w-[460px] max-w-[calc(100vw-1rem)] bg-slate-800 border border-slate-700 rounded-lg shadow-xl z-50 p-3 space-y-3"
                        data-bms-drag-scroll-ignore="true"
                    >
                        <div className="flex items-center justify-between border-b border-slate-700 pb-2">
                            <p className="text-xs font-semibold text-slate-300 uppercase tracking-wider">MSA Server Settings</p>
                            <button
                                onClick={fetchState}
                                disabled={loading !== null}
                                className="px-2 py-1 text-xs rounded border border-slate-600 text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                            >
                                Refresh
                            </button>
                        </div>

                        <div className="flex gap-2">
                            <button
                                onClick={startServers}
                                disabled={loading !== null}
                                className="flex-1 px-3 py-2 text-xs font-semibold rounded border border-blue-500/50 text-blue-300 hover:bg-blue-500/20 disabled:opacity-50"
                            >
                                {loading === 'start' ? 'Starting...' : 'Start Server'}
                            </button>
                            <button
                                onClick={stopServers}
                                disabled={loading !== null}
                                className="flex-1 px-3 py-2 text-xs font-semibold rounded border border-rose-500/50 text-rose-300 hover:bg-rose-500/20 disabled:opacity-50"
                            >
                                {loading === 'stop' ? 'Stopping...' : 'Stop Server'}
                            </button>
                        </div>

                        <label className="flex items-center gap-2 text-sm text-slate-300">
                            <input
                                type="checkbox"
                                checked={settings.include_envdb_on_start}
                                onChange={(e) => saveSettings({ include_envdb_on_start: e.target.checked })}
                                disabled={loading !== null}
                                className="rounded border-slate-500 bg-slate-700"
                            />
                            Start EnvDB server with UniRef (higher VRAM/IO)
                        </label>

                        <div className="space-y-1">
                            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400">
                                Pinned GPU
                            </label>
                            <select
                                value={settings.pinned_gpu_id ?? ''}
                                onChange={(e) => {
                                    const raw = e.target.value;
                                    saveSettings({ pinned_gpu_id: raw === '' ? null : Number(raw) });
                                }}
                                disabled={loading !== null}
                                className="w-full px-3 py-2 rounded border border-slate-600 bg-slate-900 text-slate-100 text-sm"
                            >
                                <option value="">Auto-select from scheduler</option>
                                {availableGpus.map((gpu) => (
                                    <option key={gpu.index} value={gpu.index}>
                                        GPU {gpu.index}: {gpu.name}{typeof gpu.memory_total_mb === 'number' ? ` (${(gpu.memory_total_mb / 1024).toFixed(0)} GB)` : ''}
                                    </option>
                                ))}
                            </select>
                            <p className="text-xs text-slate-400">
                                Default GPU for MSA controls.
                            </p>
                        </div>

                        <div className="space-y-2 border border-slate-700 rounded-lg p-2">
                            <label className="flex items-center gap-2 text-sm text-slate-300">
                                <input
                                    type="checkbox"
                                    checked={settings.auto_stop_idle_enabled}
                                    onChange={(e) => saveSettings({ auto_stop_idle_enabled: e.target.checked })}
                                    disabled={loading !== null}
                                    className="rounded border-slate-500 bg-slate-700"
                                />
                                Auto-stop when idle
                            </label>
                            <div className="flex items-center gap-2 text-xs text-slate-400">
                                <span>Idle threshold (minutes):</span>
                                <input
                                    type="number"
                                    min={1}
                                    value={settings.auto_stop_idle_minutes}
                                    disabled={!settings.auto_stop_idle_enabled || loading !== null}
                                    onChange={(e) => {
                                        const val = parseInt(e.target.value || '10', 10);
                                        if (!Number.isNaN(val)) {
                                            setSettings((prev) => ({ ...prev, auto_stop_idle_minutes: val }));
                                        }
                                    }}
                                    onBlur={() => saveSettings({ auto_stop_idle_minutes: settings.auto_stop_idle_minutes })}
                                    className="w-20 px-2 py-1 rounded bg-slate-900 border border-slate-600 text-slate-100"
                                />
                            </div>
                        </div>

                        <textarea
                            readOnly
                            value={summary}
                            className="w-full h-44 p-2 rounded-lg border border-slate-700 bg-slate-900 text-slate-200 text-xs font-mono resize-none"
                        />
                    </div>
                </>
            )}
        </div>
    );
}

export default Layout;
