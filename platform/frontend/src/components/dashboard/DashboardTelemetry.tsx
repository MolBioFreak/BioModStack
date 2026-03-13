import { InfraLiveTelemetry } from '../InfraLiveTelemetry';

export function DashboardTelemetry() {
    return (
        <section className="mb-6 rounded-3xl border border-[var(--border-primary)] bg-[var(--bg-secondary)]/78 p-4 shadow-2xl shadow-black/10">
            <InfraLiveTelemetry
                showXAxisLabels={false}
                defaultPollIntervalMs={1000}
                defaultWindowMinutes={3}
                variant="dashboard"
            />
        </section>
    );
}

export default DashboardTelemetry;
