import { useState, useEffect } from 'react';
import {
    useBioXpStatus,
    useAxisStatus,
    useMoveRelative,
    useHomeAxis,
    useSetThermalTemp,
    useGetLinkage,
    useSetLinkage,
    useDisconnectLinkage,
    useCameraSnapshot,
    useDaemonStatus,
    useDaemonStart,
    useDaemonStop
} from '../lib/bioxpClient';
import type { AxisName, ThermalBankName } from '../lib/bioxpClient';

const AxisControls = ({ axis, label }: { axis: AxisName, label: string }) => {
    const { data: status, isLoading, isError } = useAxisStatus(axis);
    const moveRel = useMoveRelative();
    const home = useHomeAxis();
    const [steps, setSteps] = useState<number>(1000);

    return (
        <div className="p-3 bg-surface-tertiary rounded-lg border border-accent/20 space-y-3">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <span className="text-xs text-accent font-semibold">{label} Axis</span>
                    {isLoading && <span className="text-xs text-content-muted animate-pulse">Loading...</span>}
                </div>
                {status?.status && (
                    <span className={`text-[10px] px-2 py-0.5 rounded-full ${status.status.standstill ? 'bg-success/20 text-success border border-success/30' : 'bg-warning/20 text-warning border border-warning/30'}`}>
                        {status.status.standstill ? "IDLE" : "MOVING"}
                    </span>
                )}
            </div>

            {isError && <div className="text-xs text-error">Unable to reach hardware node.</div>}

            <div className="flex gap-2 items-center">
                <span className="text-xs text-content-muted w-12">Steps:</span>
                <input
                    type="number"
                    value={steps}
                    onChange={e => setSteps(Number(e.target.value))}
                    className="bg-surface border border-accent/10 rounded-lg px-3 py-1.5 text-content text-sm w-24"
                />
                <button
                    onClick={() => moveRel.mutate({ axis, steps: -steps })}
                    disabled={moveRel.isPending}
                    className="px-3 py-1.5 bg-surface-secondary hover:bg-surface border border-accent/20 text-content text-xs rounded-lg transition-colors flex items-center justify-center font-mono"
                >
                    ◄
                </button>
                <button
                    onClick={() => moveRel.mutate({ axis, steps })}
                    disabled={moveRel.isPending}
                    className="px-3 py-1.5 bg-surface-secondary hover:bg-surface border border-accent/20 text-content text-xs rounded-lg transition-colors flex items-center justify-center font-mono"
                >
                    ►
                </button>
                <button
                    onClick={() => home.mutate({ axis })}
                    disabled={home.isPending}
                    className="ml-auto px-3 py-1.5 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors"
                >
                    ⌂ Home
                </button>
            </div>
            {status?.status && (
                <div className="text-[10px] text-content-muted font-mono flex justify-between">
                    <span>Pos: {status.status.raw}</span>
                    <span>Switches: [L:{status.status.switch_left ? '1' : '0'} R:{status.status.switch_right ? '1' : '0'}]</span>
                </div>
            )}
        </div>
    );
};

const ThermalControls = ({ bank, label }: { bank: ThermalBankName, label: string }) => {
    const setTemp = useSetThermalTemp();
    const [temp, setTempState] = useState<number>(37.0);

    return (
        <div className="p-3 bg-surface-tertiary rounded-lg border border-accent/20 space-y-3">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <span className="text-xs text-accent font-semibold">{label} Thermal</span>
                </div>
            </div>
            <div className="flex gap-2 items-center">
                <span className="text-xs text-content-muted w-16">Target °C:</span>
                <input
                    type="number"
                    value={temp}
                    onChange={e => setTempState(Number(e.target.value))}
                    step="0.1"
                    className="bg-surface border border-accent/10 rounded-lg px-3 py-1.5 text-content text-sm w-24"
                />
                <button
                    onClick={() => setTemp.mutate({ bank, target_temp_c: temp })}
                    disabled={setTemp.isPending}
                    className="ml-auto px-4 py-1.5 bg-accent hover:bg-accent/80 text-white text-xs rounded-lg transition-colors"
                >
                    Set Point
                </button>
            </div>
        </div>
    );
};

export const BioXpCockpit = () => {
    const [activeTab, setActiveTab] = useState<'connection' | 'controls' | 'camera'>('connection');

    // Status and Linkage Queries
    const { data: linkage, isLoading: linkageLoading } = useGetLinkage();
    const { data: status, isLoading: statusLoading, isError: statusError, error: statusErrorDetails } = useBioXpStatus();

    const setLinkage = useSetLinkage();
    const disconnectLinkage = useDisconnectLinkage();
    const [linkageInput, setLinkageInput] = useState("");

    // Daemon control
    const { data: daemon, isLoading: daemonLoading } = useDaemonStatus();
    const daemonStart = useDaemonStart();
    const daemonStop = useDaemonStop();

    // Camera polling
    const camera = useCameraSnapshot();
    const [snapshot, setSnapshot] = useState<string | null>(null);
    const [pollCamera, setPollCamera] = useState(false);

    // Sync input with fetched linkage initially
    useEffect(() => {
        if (linkage?.url && !linkageInput) {
            setLinkageInput(linkage.url);
        }
    }, [linkage, linkageInput]);

    // Simple poller for the camera when active
    useEffect(() => {
        if (!pollCamera) return;
        const interval = setInterval(() => {
            camera.mutate(undefined, {
                onSuccess: (data) => {
                    if (data.image_b64) {
                        setSnapshot(data.image_b64);
                    }
                }
            });
        }, 1000);
        return () => clearInterval(interval);
    }, [pollCamera, camera]);

    const isConnected = !!status && (status.status === 'ok' || status.status === 'degraded') && !statusError;
    const isDegraded = !!status && status.status === 'degraded' && !statusError;

    return (
        <div className="flex flex-col h-full overflow-y-auto p-8 space-y-6 bg-surface">
            {/* Header */}
            <div className="flex justify-between items-start border-b border-border-secondary pb-4">
                <div>
                    <h2 className="text-lg font-semibold text-content">BioXP Hardware Interface</h2>
                    <p className="text-sm text-content-muted">Direct telemetry & control proxy</p>
                </div>
                <div className="flex items-center gap-3">
                    {/* Strict Connection Status - No Fallbacks - Directly reports true state */}
                    <div className={`px-4 py-1.5 rounded-sm text-xs font-mono font-semibold border ${isConnected
                            ? isDegraded
                                ? 'bg-warning/10 text-warning border-warning/30'
                                : 'bg-success/10 text-success border-success/30'
                            : 'bg-error/10 text-error border-error/30'
                        }`}>
                        HARDWARE: {statusLoading ? 'PINGING...' : isConnected ? (isDegraded ? 'DEGRADED' : 'ONLINE') : 'OFFLINE'}
                    </div>
                </div>
            </div>

            {/* Tab Navigation */}
            <div className="flex gap-1 border-b border-border-secondary">
                <button
                    onClick={() => setActiveTab('connection')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === 'connection' ? 'border-accent text-accent' : 'border-transparent text-content-muted hover:text-content hover:border-border-primary'}`}
                >
                    Linkage & Status
                </button>
                <button
                    onClick={() => setActiveTab('controls')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === 'controls' ? 'border-accent text-accent' : 'border-transparent text-content-muted hover:text-content hover:border-border-primary'}`}
                >
                    Motion & Thermals
                </button>
                <button
                    onClick={() => setActiveTab('camera')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === 'camera' ? 'border-accent text-accent' : 'border-transparent text-content-muted hover:text-content hover:border-border-primary'}`}
                >
                    Camera Feed
                </button>
            </div>

            {/* Tab Contents */}
            <div className="flex-1 mt-4">

                {/* Connection Tab */}
                {activeTab === 'connection' && (
                    <div className="space-y-6 max-w-2xl">
                        {/* Daemon Control Panel */}
                        <div className="p-4 bg-surface-secondary border border-border-primary rounded-lg space-y-4">
                            <h3 className="text-sm font-semibold text-content border-b border-border-secondary pb-2">Remote Daemon Control</h3>
                            <p className="text-xs text-content-muted">Start or stop the BioXP API daemon running on the hardware node (<span className="font-mono">{daemon?.host ?? 'robot'}:{daemon?.port ?? 8123}</span>) via SSH.</p>

                            <div className="flex items-center gap-4">
                                <div className={`px-3 py-1.5 rounded-sm text-xs font-mono font-semibold border ${daemon?.running ? 'bg-success/10 text-success border-success/30' : 'bg-error/10 text-error border-error/30'}`}>
                                    DAEMON: {daemonLoading ? 'CHECKING...' : daemon?.running ? 'RUNNING' : 'STOPPED'}
                                </div>

                                {daemon?.running ? (
                                    <button
                                        onClick={() => daemonStop.mutate()}
                                        disabled={daemonStop.isPending}
                                        className="px-4 py-1.5 bg-error/20 hover:bg-error/30 text-error text-xs font-semibold rounded-lg transition-colors"
                                    >
                                        {daemonStop.isPending ? 'STOPPING...' : 'STOP SERVER'}
                                    </button>
                                ) : (
                                    <button
                                        onClick={() => daemonStart.mutate()}
                                        disabled={daemonStart.isPending}
                                        className="px-4 py-1.5 bg-success/20 hover:bg-success/30 text-success text-xs font-semibold rounded-lg transition-colors"
                                    >
                                        {daemonStart.isPending ? 'STARTING...' : 'START SERVER'}
                                    </button>
                                )}

                                {daemonStart.isError && <span className="text-[10px] text-error">{daemonStart.error.message}</span>}
                                {daemonStop.isError && <span className="text-[10px] text-error">{daemonStop.error.message}</span>}
                            </div>
                        </div>

                        {/* Linkage Control */}
                        <div className="p-4 bg-surface-secondary border border-border-primary rounded-lg space-y-4">
                            <h3 className="text-sm font-semibold text-content border-b border-border-secondary pb-2">Proxy Linkage</h3>
                            <p className="text-xs text-content-muted">Configure the target URL for the BioXP hardware daemon proxy. Requests will fail strictly if the node is unresponsive.</p>

                            <div className="flex gap-2">
                                <input
                                    type="text"
                                    value={linkageInput}
                                    onChange={e => setLinkageInput(e.target.value)}
                                    placeholder="http://100.124.140.56:8123"
                                    className="flex-1 bg-surface border border-accent/20 rounded-lg px-3 py-2 text-content text-sm font-mono"
                                />
                                <button
                                    onClick={() => setLinkage.mutate(linkageInput)}
                                    disabled={setLinkage.isPending || linkageLoading}
                                    className="px-4 py-2 bg-accent hover:bg-accent/80 text-white text-sm rounded-lg transition-colors font-medium"
                                >
                                    {setLinkage.isPending ? 'Connecting...' : 'Connect'}
                                </button>
                                <button
                                    onClick={() => { disconnectLinkage.mutate(); setLinkageInput(''); }}
                                    disabled={disconnectLinkage.isPending || !linkage?.url}
                                    className="px-4 py-2 bg-error/20 hover:bg-error/30 text-error text-sm rounded-lg transition-colors font-medium disabled:opacity-40"
                                >
                                    {disconnectLinkage.isPending ? '...' : 'Disconnect'}
                                </button>
                            </div>

                            {linkage?.url && (
                                <div className="text-xs font-mono text-content-muted">Active linkage: <span className="text-accent">{linkage.url}</span></div>
                            )}
                        </div>

                        {/* Telemetry Debug Box */}
                        <div className="p-4 bg-surface-tertiary border border-border-primary rounded-lg space-y-2">
                            <h3 className="text-sm font-semibold text-content">Telemetry Payload</h3>
                            <pre className="text-[10px] font-mono text-content-muted p-2 bg-[#000000] rounded border border-border-primary overflow-x-auto min-h-[100px]">
                                {statusLoading ? "Polling..." :
                                    statusError ? `Connection Error:\n${statusErrorDetails?.message}` :
                                        JSON.stringify(status, null, 2)}
                            </pre>
                        </div>
                    </div>
                )}

                {/* Controls Tab */}
                {activeTab === 'controls' && (
                    !isConnected ? (
                        <div className="p-6 bg-error/5 border border-error/20 rounded-lg text-center max-w-lg">
                            <p className="text-sm text-error font-semibold">HARDWARE OFFLINE</p>
                            <p className="text-xs text-content-muted mt-2">Configure a valid hardware node linkage in the Linkage & Status tab to enable motion and thermal controls.</p>
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                            <div className="space-y-4">
                                <h3 className="text-sm font-semibold text-content border-b border-border-secondary pb-2">Motion Control System</h3>
                                <AxisControls axis="x" label="Gantry X" />
                                <AxisControls axis="y" label="Gantry Y" />
                                <AxisControls axis="z" label="Pipette Z" />
                                <AxisControls axis="door" label="Thermal Door" />
                            </div>
                            <div className="space-y-4">
                                <h3 className="text-sm font-semibold text-content border-b border-border-secondary pb-2">Thermal Management</h3>
                                <ThermalControls bank="nest" label="Main Nest" />
                                <ThermalControls bank="lid" label="Heated Lid" />
                                <ThermalControls bank="pedestal" label="Chiller Pedestal" />
                            </div>
                        </div>
                    )
                )}

                {/* Camera Tab */}
                {activeTab === 'camera' && !isConnected && (
                    <div className="p-6 bg-error/5 border border-error/20 rounded-lg text-center max-w-lg">
                        <p className="text-sm text-error font-semibold">HARDWARE OFFLINE</p>
                        <p className="text-xs text-content-muted mt-2">Configure a valid hardware node linkage in the Linkage & Status tab to enable the camera feed.</p>
                    </div>
                )}
                {activeTab === 'camera' && isConnected && (
                    <div className="space-y-4 max-w-3xl">
                        <div className="flex justify-between items-center bg-surface-tertiary p-3 rounded-t-lg border border-border-primary border-b-0">
                            <h3 className="text-sm font-semibold text-content">Internal Deck View</h3>
                            <button
                                onClick={() => setPollCamera(!pollCamera)}
                                className={`px-4 py-1.5 text-xs font-semibold rounded-lg transition-colors ${pollCamera ? 'bg-error/20 text-error hover:bg-error/30' : 'bg-success/20 text-success hover:bg-success/30'}`}
                            >
                                {pollCamera ? "⏹ STOP STREAM" : "▶ START STREAM"}
                            </button>
                        </div>

                        <div className="w-full aspect-video bg-[#000000] rounded-b-lg border border-border-primary flex items-center justify-center overflow-hidden relative mt-0">
                            {snapshot ? (
                                <img src={`data:image/jpeg;base64,${snapshot}`} alt="BioXP Deck" className="w-full h-full object-contain" />
                            ) : (
                                <div className="text-content-muted text-sm font-mono flex flex-col items-center gap-2">
                                    <span className="text-2xl">📷</span>
                                    <span>{camera.isPending ? "CAPTURING FRAME..." : pollCamera ? "WAITING FOR SIGNAL..." : "STREAM INACTIVE"}</span>
                                </div>
                            )}

                            {/* OSD Overlay */}
                            <div className="absolute top-4 left-4 flex flex-col gap-1 text-[10px] font-mono text-[#00ff00] bg-black/50 p-2 rounded">
                                <div>CAM: /dev/video0</div>
                                <div>STATUS: {pollCamera ? "LIVE (1FPS)" : "IDLE"}</div>
                                {camera.error && <div className="text-error mt-2">{camera.error.message}</div>}
                            </div>
                        </div>

                        <div className="flex justify-end gap-2 text-[10px] text-content-muted">
                            <p>Note: Stream is achieved via rapid JPEG snapshot polling directly from the hardware node proxy.</p>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
