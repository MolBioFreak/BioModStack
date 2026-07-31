import { useBioXpOperatorDashboard } from '../lib/bioxpClient.js';

interface BioXpQuickDashboardProps {
    connected: boolean;
}

const panelStyle = {
    border: '1px solid var(--border-subtle, #334155)',
    borderRadius: 8,
    padding: 12,
    background: 'var(--surface-raised, #111827)',
} as const;

const value = (candidate: unknown, fallback = '—') => (
    candidate === null || candidate === undefined || candidate === '' ? fallback : String(candidate)
);

const yesNoUnknown = (candidate: boolean | null | undefined) => (
    candidate === true ? 'Yes' : candidate === false ? 'No' : 'Not reported'
);

export function BioXpQuickDashboard({ connected }: BioXpQuickDashboardProps) {
    const dashboard = useBioXpOperatorDashboard(connected);
    const data = dashboard.error ? undefined : dashboard.data;

    return (
        <section aria-label="Live Robot Dashboard" style={{ marginTop: 12 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
                <h3 style={{ margin: '0 0 8px' }}>Live Robot Dashboard</h3>
                <span style={{ fontSize: 12, opacity: 0.75 }}>
                    Cached robot state only · no automatic diagnostics
                </span>
            </div>
            {!connected && <div style={panelStyle}>Connect to view robot state.</div>}
            {connected && dashboard.isLoading && <div style={panelStyle}>Loading live state…</div>}
            {connected && dashboard.error && (
                <div style={{ ...panelStyle, color: '#fca5a5' }}>Dashboard unavailable: {String(dashboard.error)}</div>
            )}
            {data && (
                <>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 8 }}>
                        <div style={panelStyle}>
                            <strong>Connection</strong>
                            <div>{data.connection.live ? 'Live / owned' : 'Not live'}</div>
                        </div>
                        <div style={panelStyle}>
                            <strong>Motion</strong>
                            <div style={{ color: data.motion.enabled ? '#86efac' : '#fca5a5' }}>
                                {data.motion.enabled ? 'Enabled' : 'Inactive'}
                            </div>
                            {!data.motion.enabled && <small>{value(data.motion.reason, 'Motion is inactive.')}</small>}
                        </div>
                        <div style={panelStyle}>
                            <strong>Door / latch</strong>
                            <div>Door closed: {yesNoUnknown(data.enclosure.door_closed)}</div>
                            <div>Latch closed: {yesNoUnknown(data.enclosure.latch_closed)}</div>
                        </div>
                        <div style={panelStyle}>
                            <strong>Snapshot</strong>
                            <div>{value(data.snapshot.freshness.state, 'missing')}</div>
                            <small>Age: {value(data.snapshot.freshness.age_s)} s</small>
                        </div>
                    </div>

                    <h4 style={{ marginBottom: 6 }}>Axis Analytics</h4>
                    <div style={{ overflowX: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                            <thead><tr>
                                <th align="left">Axis</th><th align="left">Reference</th><th align="right">Position</th>
                                <th align="right">Speed</th><th align="right">Run / standby current</th>
                                <th align="left">Limits L / R</th><th align="left">Temperature</th>
                            </tr></thead>
                            <tbody>{data.axes.map((axis) => (
                                <tr key={axis.axis}>
                                    <td>{axis.axis.toUpperCase()}</td>
                                    <td>{axis.reference}</td>
                                    <td align="right">{value(axis.position_steps)} steps</td>
                                    <td align="right">{value(axis.speed_steps_s)} steps/s</td>
                                    <td align="right">{value(axis.run_current)} / {value(axis.standby_current)}</td>
                                    <td>{yesNoUnknown(axis.left_switch_active)} / {yesNoUnknown(axis.right_switch_active)}</td>
                                    <td>{axis.motor_temperature_available
                                        ? `${value(axis.motor_temperature_c)} °C`
                                        : 'Motor temperature not reported'}</td>
                                </tr>
                            ))}</tbody>
                        </table>
                    </div>

                    <h4 style={{ marginBottom: 6 }}>Temperatures</h4>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                        {data.temperatures.length === 0 && <span>Not reported</span>}
                        {data.temperatures.map((sensor) => (
                            <span key={sensor.sensor} style={panelStyle}>
                                <strong>{sensor.label}</strong>: {sensor.available ? `${value(sensor.temperature_c)} ${sensor.unit}` : 'Not reported'}
                            </span>
                        ))}
                    </div>

                    <h4 style={{ marginBottom: 6 }}>Pipettes</h4>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8 }}>
                        {(data.pipettes.channels ?? []).map((channel, index) => (
                            <div key={String(channel.channel ?? index)} style={panelStyle}>
                                <strong>Pipette {Number(channel.channel ?? index) + 1}</strong>
                                <div>{channel.available === false ? 'Unavailable' : 'Live'}</div>
                                <div>Tip loaded: {yesNoUnknown(channel.tip_loaded as boolean | null | undefined)}</div>
                                <div>Position: {value(channel.position_steps)} steps</div>
                                <div>Pressure: {value(channel.pressure)}</div>
                            </div>
                        ))}
                        {(data.pipettes.channels ?? []).length === 0 && <div style={panelStyle}>Pipette status not reported.</div>}
                    </div>
                </>
            )}
        </section>
    );
}
