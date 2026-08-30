import { useState } from 'react';

import { InfraLiveTelemetry } from './InfraLiveTelemetry';
import { RemoteGpuTelemetry } from './RemoteGpuTelemetry';

export function InfraMonitorPage() {
    const [scope, setScope] = useState<'local' | 'vast'>('local');

    return (
        <div className="min-h-screen bg-slate-950 p-6">
            <header className="mb-6 flex flex-col gap-3">
                <div className="flex flex-wrap items-center gap-2">
                    <span
                        className="inline-flex rounded-full border border-cyan-500/30 bg-cyan-500/10 px-3 py-1 text-xs font-semibold uppercase text-cyan-200"
                    >
                        Native BMS Telemetry
                    </span>
                </div>
                <div>
                    <h1 className="text-4xl font-bold bg-gradient-to-r from-cyan-300 via-blue-300 to-emerald-300 bg-clip-text text-transparent">
                        System Analytics
                    </h1>
                    <p className="mt-2 max-w-3xl text-sm text-slate-400">
                        Select the local workstation or the active Vast execution worker. Remote telemetry is read-only.
                    </p>
                </div>
                <div className="inline-flex w-fit rounded-lg border border-slate-700 bg-slate-900 p-1" role="group" aria-label="Telemetry source">
                    <button
                        type="button"
                        onClick={() => setScope('local')}
                        className={`rounded-md px-4 py-2 text-sm font-semibold ${scope === 'local'
                            ? 'bg-blue-500/20 text-blue-100'
                            : 'text-slate-400'}`}
                    >
                        Local
                    </button>
                    <button
                        type="button"
                        onClick={() => setScope('vast')}
                        className={`rounded-md px-4 py-2 text-sm font-semibold ${scope === 'vast'
                            ? 'bg-emerald-500/20 text-emerald-100'
                            : 'text-slate-400'}`}
                    >
                        Active Vast
                    </button>
                </div>
            </header>

            {scope === 'local' ? <InfraLiveTelemetry /> : <RemoteGpuTelemetry />}
        </div>
    );
}
