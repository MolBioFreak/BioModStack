import { useEffect, useState } from 'react';

// The Cordova preflight owner remains independent of development issue reporting.
export function MobilePreflightSettings() {
    const [compact, setCompact] = useState(() => document.documentElement.classList.contains('bms-cordova-compact'));
    useEffect(() => {
        const observer = new MutationObserver(() => setCompact(document.documentElement.classList.contains('bms-cordova-compact')));
        observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
        return () => observer.disconnect();
    }, []);
    if (!compact) return null;
    return (
        <div className="bms-mobile-operations-dock fixed z-[90] flex items-center gap-2 rounded-2xl border border-slate-700 bg-slate-950/95 p-2 shadow-2xl backdrop-blur" data-bms-mobile-operations-dock="true" role="group" aria-label="Mobile operations">
            <button type="button" onClick={() => document.getElementById('bms-cordova-preflight-toggle')?.click()} className="min-h-12 rounded-xl border border-cyan-400/45 px-4 text-sm font-semibold text-cyan-100">
                Settings
            </button>
        </div>
    );
}
