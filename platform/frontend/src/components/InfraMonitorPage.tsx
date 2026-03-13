import { InfraLiveTelemetry } from './InfraLiveTelemetry';

export function InfraMonitorPage() {
    return (
        <div className="min-h-screen bg-slate-950 p-6">
            <header className="mb-6 flex flex-col gap-3">
                <div className="flex flex-wrap items-center gap-2">
                    <span
                        className="inline-flex rounded-full border border-cyan-500/30 bg-cyan-500/10 px-3 py-1 text-xs font-semibold uppercase text-cyan-200"
                        style={{ letterSpacing: '0.18em' }}
                    >
                        Live Monitor
                    </span>
                    <span className="inline-flex rounded-full border border-slate-700 bg-slate-900/80 px-3 py-1 text-xs font-medium text-slate-300">
                        Native BMS Telemetry
                    </span>
                </div>
                <div>
                    <h1 className="text-4xl font-bold bg-gradient-to-r from-cyan-300 via-blue-300 to-emerald-300 bg-clip-text text-transparent">
                        Infra Monitor
                    </h1>
                    <p className="mt-2 max-w-3xl text-sm text-slate-400">
                        Dedicated workstation telemetry for CPU, memory, and GPUs. This page stays separate from the main dashboard until the upgraded monitor is ready to replace the legacy system block.
                    </p>
                </div>
            </header>

            <InfraLiveTelemetry />
        </div>
    );
}

export default InfraMonitorPage;
