import React, { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    fetchSystemStatus,
    fetchPowerControl,
    setPowerControlManual,
    fetchSchedulerConfig,
    toggleGpuDisabled
} from '../../lib/api';
import type { GPUStatus, CPUStatus, RAMStatus } from '../../lib/api';

// --- Helper Components ---

function Sparkline({ data, color, height = 24 }: { data: number[]; color: string; height?: number }) {
    if (data.length < 2) return null;

    const width = 100;
    const max = Math.max(...data, 100);
    const min = Math.min(...data, 0);
    const range = max - min || 1;

    const points = data.map((value, i) => {
        const x = (i / (data.length - 1)) * width;
        const y = height - ((value - min) / range) * height;
        return `${x},${y}`;
    }).join(' ');

    const colorMap: Record<string, string> = {
        green: '#22c55e',
        purple: '#a855f7',
        blue: '#3b82f6',
        yellow: '#eab308'
    };

    return (
        <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
            <polyline
                fill="none"
                stroke={colorMap[color] || color}
                strokeWidth="1.5"
                points={points}
            />
        </svg>
    );
}

function CPUCard({ cpu, history }: { cpu: CPUStatus; history: number[] }) {
    return (
        <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
                <span className="text-sm text-slate-400">CPU</span>
                <div className="flex gap-2">
                    {cpu.temperature !== null && (
                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${cpu.temperature > 80 ? 'bg-red-500/20 text-red-400' :
                            cpu.temperature > 60 ? 'bg-yellow-500/20 text-yellow-400' :
                                'bg-blue-500/20 text-blue-400'
                            }`}>
                            {cpu.temperature.toFixed(0)}°C
                        </span>
                    )}
                    <span className={`px-2 py-1 rounded-full text-xs font-medium ${cpu.utilization > 80 ? 'bg-red-500/20 text-red-400' : cpu.utilization > 50 ? 'bg-yellow-500/20 text-yellow-400' : 'bg-green-500/20 text-green-400'}`}>
                        {cpu.utilization.toFixed(1)}%
                    </span>
                </div>
            </div>
            <h3 className="text-sm font-medium text-white truncate mb-3">{cpu.name}</h3>

            <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                    <span className="text-slate-400">Cores:</span>
                    <span className="text-slate-200 ml-1">{cpu.cores_physical}P / {cpu.cores_logical}T</span>
                </div>
                <div>
                    <span className="text-slate-400">Freq:</span>
                    <span className="text-slate-200 ml-1">{cpu.frequency_current_mhz.toFixed(0)} MHz</span>
                </div>
            </div>

            {/* CPU Load Sparkline */}
            {history.length > 1 && (
                <div className="mt-3">
                    <Sparkline data={history} color="green" height={24} />
                </div>
            )}

            {/* Per-core utilization mini bars */}
            <div className="mt-3 flex gap-0.5">
                {cpu.per_core_utilization.slice(0, 24).map((util, i) => (
                    <div
                        key={i}
                        className="flex-1 bg-slate-700 rounded-sm h-3 overflow-hidden"
                        title={`Core ${i}: ${util.toFixed(0)}%`}
                    >
                        <div
                            className={`h-full transition-all ${util > 80 ? 'bg-red-500' : util > 50 ? 'bg-yellow-500' : 'bg-green-500'}`}
                            style={{ height: `${util}%` }}
                        />
                    </div>
                ))}
            </div>
        </div>
    );
}

function RAMCard({ ram, history }: { ram: RAMStatus; history: number[] }) {
    return (
        <div className="bg-slate-800/50 backdrop-blur-sm border border-slate-700 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
                <span className="text-sm text-slate-400">Memory</span>
                <div className="flex gap-2">
                    {ram.swap_percent > 0 && (
                        <span className="px-2 py-1 rounded-full text-xs font-medium bg-orange-500/20 text-orange-400">
                            Swap: {ram.swap_percent.toFixed(0)}%
                        </span>
                    )}
                    <span className={`px-2 py-1 rounded-full text-xs font-medium ${ram.utilization > 90 ? 'bg-red-500/20 text-red-400' : ram.utilization > 70 ? 'bg-yellow-500/20 text-yellow-400' : 'bg-green-500/20 text-green-400'}`}>
                        {ram.utilization.toFixed(1)}%
                    </span>
                </div>
            </div>

            <div className="grid grid-cols-3 gap-2 text-center mb-3">
                <div>
                    <div className="text-lg font-semibold text-white">{ram.used_gb}</div>
                    <div className="text-xs text-slate-400">Used GB</div>
                </div>
                <div>
                    <div className="text-lg font-semibold text-green-400">{ram.available_gb}</div>
                    <div className="text-xs text-slate-400">Free GB</div>
                </div>
                <div>
                    <div className="text-lg font-semibold text-slate-300">{ram.total_gb}</div>
                    <div className="text-xs text-slate-400">Total GB</div>
                </div>
            </div>

            {/* RAM Usage Sparkline */}
            {history.length > 1 && (
                <div className="mb-3">
                    <Sparkline data={history} color="purple" height={24} />
                </div>
            )}

            <div className="w-full bg-slate-700 rounded-full h-3">
                <div
                    className={`h-3 rounded-full transition-all ${ram.utilization > 90 ? 'bg-red-500' : ram.utilization > 70 ? 'bg-yellow-500' : 'bg-green-500'}`}
                    style={{ width: `${ram.utilization}%` }}
                />
            </div>
        </div>
    );
}

function GPUCard({ gpu, currentLimit, onSetLimit, isPending, disabled, onToggleDisable }: {
    gpu: GPUStatus;
    currentLimit: number;
    onSetLimit: (watts: number) => void;
    isPending: boolean;
    disabled: boolean;
    onToggleDisable: () => void;
}) {
    const [inputValue, setInputValue] = useState(String(Math.round(currentLimit)));
    const memoryPercent = (gpu.memory_used_mb / gpu.memory_total_mb) * 100;
    const powerPercent = currentLimit > 0 ? (gpu.power_draw_w / currentLimit) * 100 : 0;

    const handleApply = () => {
        const watts = parseInt(inputValue, 10);
        if (!isNaN(watts) && watts >= gpu.min_power_watts && watts <= gpu.max_power_watts) {
            onSetLimit(watts);
        }
    };

    const handleIncrement = () => {
        const current = parseInt(inputValue, 10) || currentLimit;
        const newVal = Math.min(current + 5, gpu.max_power_watts);
        setInputValue(String(newVal));
    };

    const handleDecrement = () => {
        const current = parseInt(inputValue, 10) || currentLimit;
        const newVal = Math.max(current - 5, gpu.min_power_watts);
        setInputValue(String(newVal));
    };

    const isOutOfRange = (() => {
        const v = parseInt(inputValue, 10);
        return !isNaN(v) && (v < gpu.min_power_watts || v > gpu.max_power_watts);
    })();

    const isDirty = parseInt(inputValue, 10) !== currentLimit;

    return (
        <div className={`bg-slate-800/50 backdrop-blur-sm border rounded-lg p-3 transition-all duration-300 ${disabled ? 'border-red-500/50 opacity-60' : 'border-slate-700 hover:border-purple-500/50'
            }`}>
            {/* Header - compact */}
            <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2">
                    <span className="text-xs text-slate-500">GPU {gpu.index}</span>
                    <span className="text-sm font-medium text-white truncate">{gpu.name}</span>
                    {disabled && (
                        <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-red-500/20 text-red-400">
                            Disabled
                        </span>
                    )}
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={onToggleDisable}
                        className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${disabled
                            ? 'bg-green-500/20 text-green-400 hover:bg-green-500/30'
                            : 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                            }`}
                        title={disabled ? 'Enable GPU for inference' : 'Disable GPU from inference'}
                    >
                        {disabled ? 'Enable' : 'Disable'}
                    </button>
                    <span
                        className={`px-1.5 py-0.5 rounded text-xs font-medium ${gpu.utilization > 80
                            ? 'bg-green-500/20 text-green-400'
                            : gpu.utilization > 20
                                ? 'bg-yellow-500/20 text-yellow-400'
                                : 'bg-slate-500/20 text-slate-400'
                            }`}
                    >
                        {gpu.utilization}%
                    </span>
                </div>
            </div>

            {/* Stats Row - inline compact */}
            <div className="flex items-center gap-3 text-xs mb-2">
                <span className={`${gpu.temperature > 80 ? 'text-red-400' : gpu.temperature > 60 ? 'text-yellow-400' : 'text-green-400'}`}>
                    {gpu.temperature}°C
                </span>
                <span className="text-blue-400">{gpu.fan_speed}% Fan</span>
                <span className="text-purple-400">{gpu.clock_graphics_mhz}MHz</span>
            </div>

            {/* VRAM Bar - compact */}
            <div className="mb-2">
                <div className="flex justify-between text-xs text-slate-500 mb-0.5">
                    <span>VRAM</span>
                    <span>{((gpu.memory_used_mb + gpu.reserved_memory_mb) / 1024).toFixed(1)}/{(gpu.memory_total_mb / 1024).toFixed(0)}GB</span>
                </div>
                <div className="w-full bg-slate-700 rounded-full h-1.5 relative overflow-hidden">
                    <div
                        className="absolute top-0 left-0 h-full bg-gradient-to-r from-blue-500 to-purple-500 rounded-full transition-all z-20"
                        style={{ width: `${memoryPercent}%` }}
                    />
                    {gpu.reserved_memory_mb > 0 && (
                        <div
                            className="absolute top-0 h-full bg-orange-500/30 z-10"
                            style={{ left: `${memoryPercent}%`, width: `${(gpu.reserved_memory_mb / gpu.memory_total_mb) * 100}%` }}
                        />
                    )}
                </div>
            </div>

            {/* Power Row - inline compact */}
            <div className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                    <span className="text-orange-400 font-medium">{gpu.power_draw_w}W</span>
                    <div className="w-16 bg-slate-700 rounded-full h-1">
                        <div
                            className={`h-1 rounded-full ${powerPercent > 90 ? 'bg-red-500' : powerPercent > 70 ? 'bg-yellow-500' : 'bg-orange-500'}`}
                            style={{ width: `${Math.min(powerPercent, 100)}%` }}
                        />
                    </div>
                </div>
                <div className="flex items-center gap-1">
                    <button onClick={handleDecrement} className="w-5 h-5 flex items-center justify-center bg-slate-700 hover:bg-slate-600 rounded text-slate-300 text-xs">−</button>
                    <input
                        type="text"
                        inputMode="numeric"
                        pattern="[0-9]*"
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value.replace(/[^0-9]/g, ''))}
                        className={`w-12 px-1 py-0.5 bg-slate-700 border rounded text-white text-xs text-center ${isOutOfRange ? 'border-red-500' : isDirty ? 'border-yellow-500' : 'border-slate-600'}`}
                    />
                    <button onClick={handleIncrement} className="w-5 h-5 flex items-center justify-center bg-slate-700 hover:bg-slate-600 rounded text-slate-300 text-xs">+</button>
                    {isDirty && (
                        <button
                            onClick={handleApply}
                            disabled={isPending || isOutOfRange}
                            className="px-2 py-0.5 bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 rounded text-xs disabled:opacity-50"
                        >
                            ✓
                        </button>
                    )}
                </div>
            </div>

            {/* Processes - minimal */}
            {gpu.processes.length > 0 && (
                <div className="border-t border-slate-700/50 pt-1.5 mt-2 text-xs text-slate-400">
                    {gpu.processes.slice(0, 2).map((proc) => (
                        <div key={proc.pid} className="flex justify-between truncate">
                            <span className="truncate max-w-[70%]">{proc.name}</span>
                            <span className="text-slate-500">{proc.memory_mb}MB</span>
                        </div>
                    ))}
                    {gpu.processes.length > 2 && <span className="text-slate-500">+{gpu.processes.length - 2} more</span>}
                </div>
            )}
        </div>
    );
}

// GPU Scheduler Settings Panel
interface SchedulerConfig {
    global: {
        busy_threshold: number;
        cooldown_ms: number;
        enabled: boolean;
        target_vram_fill: number;
        capacity_weight: number;
        emptiness_weight: number;
        msa_concurrency_limit: number;
    };
    overrides: Record<string, {
        force_available: boolean;
        quick_enable: boolean;
        threshold: number | null;
        disabled?: boolean;
        priority_tier?: number | null;
        vram_safety_margin_mb?: number;
        max_concurrent_jobs?: number | null;
    }>;
}

function GPUSchedulerSettings({ gpus }: { gpus: GPUStatus[] }) {
    const [config, setConfig] = useState<SchedulerConfig | null>(null);
    const [loading, setLoading] = useState(false);
    const [localThreshold, setLocalThreshold] = useState(75);
    const [localCooldown, setLocalCooldown] = useState(10);
    const [localCapacityWeight, setLocalCapacityWeight] = useState(3.0);
    const [localEmptinessWeight, setLocalEmptinessWeight] = useState(5.0);
    const [expanded, setExpanded] = useState(false);
    const [debugExpanded, setDebugExpanded] = useState(false);

    // GPU specs: name and max VRAM in MB
    const GPU_SPECS: Record<number, { name: string; maxVramMb: number }> = {
        0: { name: 'RTX 5090', maxVramMb: 32768 },      // 32GB
        1: { name: 'RTX 5060 Ti', maxVramMb: 16384 },   // 16GB
        2: { name: 'RTX 3090 #1', maxVramMb: 24576 },   // 24GB
        3: { name: 'RTX 3090 #2', maxVramMb: 24576 },   // 24GB
    };

    // Per-GPU local state for overrides (stores pending changes)
    const [localGpuOverrides, setLocalGpuOverrides] = useState<Record<string, {
        vramLimitMb: number;
        priorityTier: number | null;
    }>>({});

    // Fetch config on mount
    useEffect(() => {
        fetch('/api/gpu/scheduler-config')
            .then(res => res.json())
            .then(data => {
                setConfig(data);
                setLocalThreshold(Math.round((data.global?.target_vram_fill ?? 0.75) * 100));
                setLocalCooldown(Math.round((data.global?.cooldown_ms ?? 10000) / 1000));
                setLocalCapacityWeight(data.global?.capacity_weight ?? 3.0);
                setLocalEmptinessWeight(data.global?.emptiness_weight ?? 5.0);

                // Initialize per-GPU local state from config
                const gpuStates: typeof localGpuOverrides = {};
                for (const gpuIdStr of Object.keys(data.overrides || {})) {
                    const gpuIdx = parseInt(gpuIdStr);
                    const override = data.overrides[gpuIdStr] || {};
                    const maxVram = GPU_SPECS[gpuIdx]?.maxVramMb ?? 24576;
                    // Calculate vramLimit from threshold and safety margin
                    const thresholdPct = override.threshold ?? (data.global?.target_vram_fill ?? 0.75);
                    const safetyMb = override.vram_safety_margin_mb ?? 0;
                    gpuStates[gpuIdStr] = {
                        vramLimitMb: Math.round(maxVram * thresholdPct - safetyMb),
                        priorityTier: override.priority_tier ?? null,
                    };
                }
                setLocalGpuOverrides(gpuStates);
            })
            .catch(console.error);
    }, []);

    // Get or initialize local GPU override
    const getLocalGpuOverride = (gpuId: string) => {
        const gpuIdx = parseInt(gpuId);
        const maxVram = GPU_SPECS[gpuIdx]?.maxVramMb ?? 24576;

        if (localGpuOverrides[gpuId]) return localGpuOverrides[gpuId];

        const override = (config?.overrides[gpuId] || {}) as {
            threshold?: number | null;
            vram_safety_margin_mb?: number;
            priority_tier?: number | null;
        };

        // Calculate vramLimit from threshold and safety margin
        const thresholdPct = override.threshold ?? (config?.global?.target_vram_fill ?? 0.75);
        const safetyMb = override.vram_safety_margin_mb ?? 0;

        return {
            vramLimitMb: Math.round(maxVram * thresholdPct - safetyMb),
            priorityTier: override.priority_tier ?? null,
        };
    };

    // Update local GPU override state
    const updateLocalGpuOverride = (gpuId: string, field: string, value: number | null) => {
        setLocalGpuOverrides(prev => ({
            ...prev,
            [gpuId]: {
                ...getLocalGpuOverride(gpuId),
                [field]: value,
            }
        }));
    };

    // Save per-GPU override to backend
    const saveGpuOverride = async (gpuId: string) => {
        if (!config) return;
        const local = getLocalGpuOverride(gpuId);
        const existing = config.overrides[gpuId] || {};
        const gpuIdx = parseInt(gpuId);
        const maxVram = GPU_SPECS[gpuIdx]?.maxVramMb ?? 24576;

        // Convert vramLimitMb back to threshold percentage
        const thresholdPct = local.vramLimitMb / maxVram;

        try {
            const res = await fetch(`/api/gpu/scheduler-config/gpu/${gpuId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    force_available: existing.force_available ?? false,
                    quick_enable: existing.quick_enable ?? false,
                    threshold: thresholdPct,
                    disabled: existing.disabled ?? false,
                    priority_tier: local.priorityTier,
                    vram_safety_margin_mb: 0, // No longer using separate safety margin
                    max_concurrent_jobs: existing.max_concurrent_jobs ?? null,
                })
            });
            if (res.ok) {
                const data = await res.json();
                setConfig(prev => prev ? { ...prev, overrides: data.overrides } : null);
            }
        } catch (error) {
            console.error('Failed to update GPU override:', error);
        }
    };

    // Apply VRAM preset to all GPUs (percentage of max)
    const applyVramPreset = async (percentage: number) => {
        if (!config) return;
        setLoading(true);

        for (const gpu of gpus) {
            const gpuId = String(gpu.index);
            const maxVram = GPU_SPECS[gpu.index]?.maxVramMb ?? 24576;
            const vramLimitMb = Math.round(maxVram * (percentage / 100));
            const thresholdPct = percentage / 100;
            const existing = config.overrides[gpuId] || {};

            try {
                await fetch(`/api/gpu/scheduler-config/gpu/${gpuId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        force_available: existing.force_available ?? false,
                        quick_enable: existing.quick_enable ?? false,
                        threshold: thresholdPct,
                        disabled: existing.disabled ?? false,
                        priority_tier: null,
                        vram_safety_margin_mb: 0,
                        max_concurrent_jobs: null,
                    })
                });

                // Update local state
                setLocalGpuOverrides(prev => ({
                    ...prev,
                    [gpuId]: { vramLimitMb, priorityTier: null }
                }));
            } catch (error) {
                console.error(`Failed to set preset for GPU ${gpuId}:`, error);
            }
        }

        // Refresh config
        const res = await fetch('/api/gpu/scheduler-config');
        if (res.ok) {
            const data = await res.json();
            setConfig(data);
        }
        setLoading(false);
    };

    const updateGlobal = async () => {
        setLoading(true);
        try {
            const res = await fetch('/api/gpu/scheduler-config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    busy_threshold: config?.global?.busy_threshold ?? 0.5,
                    cooldown_ms: localCooldown * 1000,
                    enabled: config?.global?.enabled ?? true,
                    target_vram_fill: localThreshold / 100,
                    capacity_weight: localCapacityWeight,
                    emptiness_weight: localEmptinessWeight,
                    msa_concurrency_limit: config?.global?.msa_concurrency_limit ?? 1,
                })
            });
            if (res.ok) {
                const data = await res.json();
                setConfig({ global: data.global, overrides: data.overrides });
            }
        } catch (error) {
            console.error('Failed to update scheduler config:', error);
        } finally {
            setLoading(false);
        }
    };

    // Quick Enable - toggle: if off, enable one-shot. If on, clear it.
    const toggleQuickEnable = async (gpuId: string) => {
        if (!config) return;
        const current = config.overrides[gpuId]?.quick_enable ?? false;
        try {
            const res = await fetch(`/api/gpu/scheduler-config/gpu/${gpuId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    force_available: config.overrides[gpuId]?.force_available ?? false,
                    quick_enable: !current,  // Toggle
                    threshold: null
                })
            });
            if (res.ok) {
                const data = await res.json();
                setConfig(prev => prev ? { ...prev, overrides: data.overrides } : null);
            }
        } catch (error) {
            console.error('Failed to toggle quick enable:', error);
        }
    };

    // Debug mode - permanent force available (dangerous!)
    const toggleForceAvailable = async (gpuId: string) => {
        if (!config) return;
        const current = config.overrides[gpuId]?.force_available ?? false;
        try {
            const res = await fetch(`/api/gpu/scheduler-config/gpu/${gpuId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    force_available: !current,
                    quick_enable: false,
                    threshold: null
                })
            });
            if (res.ok) {
                const data = await res.json();
                setConfig(prev => prev ? { ...prev, overrides: data.overrides } : null);
            }
        } catch (error) {
            console.error('Failed to toggle force available:', error);
        }
    };

    if (!config) return null;

    const isDirty =
        localThreshold !== Math.round((config.global.target_vram_fill ?? 0.75) * 100) ||
        localCooldown !== Math.round(config.global.cooldown_ms / 1000) ||
        localCapacityWeight !== (config.global.capacity_weight ?? 3.0) ||
        localEmptinessWeight !== (config.global.emptiness_weight ?? 5.0);

    return (
        <div className="bg-slate-800/30 backdrop-blur-sm border border-slate-700 rounded-xl p-4 mb-4">
            <div
                className="flex items-center justify-between cursor-pointer"
                onClick={() => setExpanded(!expanded)}
            >
                <div className="flex items-center gap-3">
                    <span className="text-sm font-medium text-slate-200">⚙️ GPU Scheduler</span>
                    <span className="px-2 py-0.5 rounded text-xs font-medium bg-green-500/20 text-green-400">
                        {gpus.length} GPUs
                    </span>
                    <span className="px-2 py-0.5 rounded text-xs font-medium bg-cyan-500/20 text-cyan-400">
                        Cap: {config.global.capacity_weight ?? 3.0}
                    </span>
                </div>
                <span className="text-slate-500">{expanded ? '▲' : '▼'}</span>
            </div>

            {expanded && (
                <div className="mt-4 space-y-4">
                    {/* VRAM Preset Buttons - Set all GPUs at once */}
                    <div className="flex items-center justify-between">
                        <span className="text-sm text-slate-400">VRAM Presets</span>
                        <div className="flex items-center gap-2">
                            {[25, 50, 75, 95].map(pct => (
                                <button
                                    key={pct}
                                    onClick={() => applyVramPreset(pct)}
                                    disabled={loading}
                                    className="px-3 py-1.5 rounded text-xs font-medium bg-slate-700/50 text-slate-300 hover:bg-cyan-500/30 hover:text-cyan-300 transition-colors disabled:opacity-50"
                                >
                                    {pct}%
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* GPU Priority Weights Section */}
                    <div className="border-t border-slate-700 pt-4">
                        <div className="text-xs text-slate-400 mb-3">GPU Priority Weights</div>

                        {/* Capacity Weight Slider */}
                        <div className="mb-4">
                            <div className="flex justify-between text-xs text-slate-400 mb-1">
                                <span>Capacity Weight <span className="text-slate-600">(prefer bigger GPUs)</span></span>
                                <span className="text-emerald-400 font-medium">{localCapacityWeight.toFixed(1)}</span>
                            </div>
                            <input
                                type="range"
                                min="0"
                                max="10"
                                step="0.5"
                                value={localCapacityWeight}
                                onChange={(e) => setLocalCapacityWeight(parseFloat(e.target.value))}
                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-emerald-500"
                            />
                            <div className="flex justify-between text-xs text-slate-500 mt-1">
                                <span>0 (ignore size)</span>
                                <span>10 (strongly prefer big)</span>
                            </div>
                        </div>

                        {/* Emptiness Weight Slider */}
                        <div>
                            <div className="flex justify-between text-xs text-slate-400 mb-1">
                                <span>Emptiness Weight <span className="text-slate-600">(prefer idle GPUs)</span></span>
                                <span className="text-amber-400 font-medium">{localEmptinessWeight.toFixed(1)}</span>
                            </div>
                            <input
                                type="range"
                                min="0"
                                max="10"
                                step="0.5"
                                value={localEmptinessWeight}
                                onChange={(e) => setLocalEmptinessWeight(parseFloat(e.target.value))}
                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-amber-500"
                            />
                            <div className="flex justify-between text-xs text-slate-500 mt-1">
                                <span>0 (pack tight)</span>
                                <span>10 (spread out)</span>
                            </div>
                        </div>

                        <p className="text-xs text-slate-500 mt-3">
                            <span className="text-emerald-400">↑ Capacity</span> = fill 5090 first, then 3090s, then 5060 Ti<br />
                            <span className="text-amber-400">↑ Emptiness</span> = prefer idle GPUs over partially-full ones
                        </p>
                    </div>

                    {/* Apply Button */}
                    {isDirty && (
                        <button
                            onClick={updateGlobal}
                            disabled={loading}
                            className="w-full py-2 bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
                        >
                            {loading ? 'Saving...' : 'Apply Changes'}
                        </button>
                    )}

                    {/* Per-GPU Controls (Debug) - Toggleable */}
                    <div className="border-t border-slate-700 pt-4">
                        <button
                            onClick={() => setDebugExpanded(!debugExpanded)}
                            className="flex items-center justify-between w-full text-left"
                        >
                            <div className="flex items-center gap-2">
                                <span className="text-xs text-slate-400">Per-GPU Controls</span>
                                <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400">Debug</span>
                            </div>
                            <span className="text-slate-500 text-xs">{debugExpanded ? '▲ Hide' : '▼ Show'}</span>
                        </button>

                        {debugExpanded && (
                            <div className="mt-4 space-y-4">
                                {gpus.map(gpu => {
                                    const gpuId = String(gpu.index);
                                    const override = config.overrides[gpuId] || {};
                                    const isForced = override.force_available ?? false;
                                    const isQuickEnabled = override.quick_enable ?? false;
                                    const isDisabled = override.disabled ?? false;
                                    const memoryUsed = ((gpu.memory_used_mb / gpu.memory_total_mb) * 100).toFixed(0);

                                    // GPU Name mapping
                                    const gpuNames: Record<number, string> = {
                                        0: 'RTX 5090',
                                        1: 'RTX 5060 Ti',
                                        2: 'RTX 3090 #1',
                                        3: 'RTX 3090 #2',
                                    };
                                    const gpuName = gpuNames[gpu.index] || `GPU ${gpu.index}`;

                                    // Get local state for this GPU
                                    const localOverride = getLocalGpuOverride(gpuId);

                                    // GPU specs for this GPU
                                    const maxVram = GPU_SPECS[gpu.index]?.maxVramMb ?? 24576;
                                    const minVram = 1024; // 1GB minimum

                                    // Check if this GPU has unsaved changes
                                    const serverOverride = config.overrides[gpuId] || {};
                                    const serverThreshold = serverOverride.threshold ?? (config.global?.target_vram_fill ?? 0.75);
                                    const serverSafetyMb = serverOverride.vram_safety_margin_mb ?? 0;
                                    const serverVramLimitMb = Math.round(maxVram * serverThreshold - serverSafetyMb);
                                    const serverPriorityTier = serverOverride.priority_tier ?? null;

                                    const hasUnsavedChanges =
                                        localOverride.vramLimitMb !== serverVramLimitMb ||
                                        localOverride.priorityTier !== serverPriorityTier;

                                    return (
                                        <div key={gpu.index} className={`bg-slate-800/50 rounded-lg px-4 py-4 ${isDisabled ? 'opacity-50' : ''}`}>
                                            {/* Header Row */}
                                            <div className="flex items-center justify-between mb-3">
                                                <div className="flex items-center gap-2">
                                                    <span className="text-sm font-medium text-slate-200">{gpuName}</span>
                                                    <span className={`text-xs px-1.5 py-0.5 rounded ${Number(memoryUsed) > 50 ? 'bg-yellow-500/20 text-yellow-400' : 'bg-green-500/20 text-green-400'}`}>
                                                        {memoryUsed}%
                                                    </span>
                                                    {isDisabled && (
                                                        <span className="text-xs px-1.5 py-0.5 rounded bg-red-500/20 text-red-400">Disabled</span>
                                                    )}
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    <button
                                                        onClick={() => toggleQuickEnable(gpuId)}
                                                        className={`px-2 py-1 rounded text-xs font-medium transition-colors ${isQuickEnabled
                                                            ? 'bg-cyan-500/40 text-cyan-200'
                                                            : 'bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30'
                                                            }`}
                                                    >
                                                        {isQuickEnabled ? '✓ Queued' : '+ Enable'}
                                                    </button>
                                                    <label className="flex items-center gap-1 text-xs text-slate-500 cursor-pointer">
                                                        <input
                                                            type="checkbox"
                                                            checked={isForced}
                                                            onChange={() => toggleForceAvailable(gpuId)}
                                                            className="w-3 h-3 accent-red-500"
                                                        />
                                                        <span className={isForced ? 'text-red-400' : ''}>Force</span>
                                                    </label>
                                                </div>
                                            </div>

                                            {/* Sliders Row - 2 columns now */}
                                            <div className="grid grid-cols-2 gap-4 text-xs">
                                                {/* Priority Tier Slider */}
                                                <div>
                                                    <div className="flex justify-between text-slate-500 mb-1">
                                                        <span>Priority</span>
                                                        <span className="text-emerald-400">
                                                            {localOverride.priorityTier !== null ? localOverride.priorityTier : 'Auto'}
                                                        </span>
                                                    </div>
                                                    <input
                                                        type="range"
                                                        min="0"
                                                        max="10"
                                                        value={localOverride.priorityTier ?? 5}
                                                        onChange={(e) => {
                                                            const val = parseInt(e.target.value);
                                                            updateLocalGpuOverride(gpuId, 'priorityTier', val);
                                                        }}
                                                        className="w-full h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-emerald-500"
                                                    />
                                                    <div className="flex justify-between text-slate-600 mt-0.5">
                                                        <span>Low</span>
                                                        <button
                                                            onClick={() => updateLocalGpuOverride(gpuId, 'priorityTier', null)}
                                                            className="text-slate-500 hover:text-emerald-400"
                                                        >
                                                            Reset
                                                        </button>
                                                        <span>High</span>
                                                    </div>
                                                </div>

                                                {/* VRAM Limit Slider (merged) */}
                                                <div>
                                                    <div className="flex justify-between text-slate-500 mb-1">
                                                        <span>VRAM Limit</span>
                                                        <span className="text-cyan-400">{(localOverride.vramLimitMb / 1024).toFixed(1)}GB</span>
                                                    </div>
                                                    <input
                                                        type="range"
                                                        min={minVram}
                                                        max={maxVram}
                                                        step="512"
                                                        value={localOverride.vramLimitMb}
                                                        onChange={(e) => updateLocalGpuOverride(gpuId, 'vramLimitMb', parseInt(e.target.value))}
                                                        className="w-full h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                                    />
                                                    <div className="flex justify-between text-slate-600 mt-0.5">
                                                        <span>1GB</span>
                                                        <span>{(maxVram / 1024).toFixed(0)}GB</span>
                                                    </div>
                                                </div>
                                            </div>

                                            {/* Save Button for this GPU */}
                                            {hasUnsavedChanges && (
                                                <button
                                                    onClick={() => saveGpuOverride(gpuId)}
                                                    className="mt-3 w-full py-1.5 bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 rounded text-xs font-medium transition-colors"
                                                >
                                                    Save {gpuName} Settings
                                                </button>
                                            )}
                                        </div>
                                    );
                                })}

                                <p className="text-xs text-slate-500">
                                    <span className="text-emerald-400">Priority</span>: Higher = preferred for jobs (Auto uses GPU capacity).<br />
                                    <span className="text-cyan-400">VRAM Limit</span>: Maximum VRAM the scheduler will use on this GPU.
                                </p>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}

// --- Main SystemResources Component ---

export function SystemResources() {
    const queryClient = useQueryClient();

    const { data: systemData } = useQuery({
        queryKey: ['system'],
        queryFn: fetchSystemStatus,
        refetchInterval: 2000,
    });

    const { data: powerControlData } = useQuery({
        queryKey: ['powerControl'],
        queryFn: fetchPowerControl,
        refetchInterval: 5000,
    });

    const manualMutation = useMutation({
        mutationFn: ({ gpuIndex, limitWatts }: { gpuIndex: number; limitWatts: number }) =>
            setPowerControlManual(gpuIndex, limitWatts),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['powerControl'] });
            queryClient.invalidateQueries({ queryKey: ['system'] });
        },
    });

    // Scheduler config for GPU disable status
    const { data: schedulerConfigData } = useQuery({
        queryKey: ['schedulerConfig'],
        queryFn: fetchSchedulerConfig,
        refetchInterval: 5000,
    });

    const toggleDisableMutation = useMutation({
        mutationFn: (gpuId: number) => toggleGpuDisabled(gpuId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['schedulerConfig'] });
        },
    });

    const gpuOverrides = schedulerConfigData?.data?.overrides ?? {};
    const currentLimits = powerControlData?.data.limits ?? {};
    const gpus = systemData?.data.gpus ?? [];
    const cpu = systemData?.data.cpu;
    const ram = systemData?.data.ram;
    const cpuHistory = systemData?.data.cpu_history ?? [];
    const ramHistory = systemData?.data.ram_history ?? [];

    if (!cpu && !ram && gpus.length === 0) {
        return <div className="animate-pulse h-32 bg-slate-800 rounded-xl mb-8 opactiy-50" />;
    }

    return (
        <>
            {/* System Overview - CPU & RAM */}
            {(cpu || ram) && (
                <section className="mb-8">
                    <h2 className="text-xl font-semibold text-slate-200 mb-4">System Overview</h2>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {/* CPU Card */}
                        {cpu && <CPUCard cpu={cpu} history={cpuHistory} />}
                        {/* RAM Card */}
                        {ram && <RAMCard ram={ram} history={ramHistory} />}
                    </div>
                </section>
            )}

            {/* GPU Status Cards */}
            <section className="mb-8">
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-xl font-semibold text-slate-200">GPU Status</h2>
                    {gpus.length > 0 && (
                        <span className="text-sm text-slate-400">
                            Total: {gpus.reduce((sum, gpu) => sum + gpu.power_draw_w, 0).toFixed(1)}W / {gpus.reduce((sum, gpu) => sum + (currentLimits[gpu.index] ?? gpu.power_limit_w), 0)}W
                        </span>
                    )}
                </div>

                {/* GPU Scheduler Settings Panel */}
                <GPUSchedulerSettings gpus={gpus} />

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    {gpus.map((gpu) => (
                        <GPUCard
                            key={gpu.index}
                            gpu={gpu}
                            currentLimit={currentLimits[gpu.index] ?? gpu.power_limit_w}
                            onSetLimit={(watts) => manualMutation.mutate({ gpuIndex: gpu.index, limitWatts: watts })}
                            isPending={manualMutation.isPending}
                            disabled={gpuOverrides[String(gpu.index)]?.disabled ?? false}
                            onToggleDisable={() => toggleDisableMutation.mutate(gpu.index)}
                        />
                    ))}
                    {gpus.length === 0 && (
                        <div className="col-span-full text-slate-500 text-center py-8">
                            No GPU data available
                        </div>
                    )}
                </div>
            </section>
        </>
    );
}
