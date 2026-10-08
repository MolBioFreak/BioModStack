import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './api';

export type AxisName = 'x' | 'y' | 'z' | 'g' | 'door';
export type ThermalBankName = 'nest' | 'lid' | 'pedestal';
export type ChillerBankName = 'rc' | 'oc';

type BioXpPayload = Record<string, any>;

export interface BioXpStatus {
    status: string;
    transport: string;
    runtime_available?: boolean;
    hardware_connected: boolean;
    linkage_configured?: boolean;
    linkage_url?: string | null;
    recommended_url?: string | null;
    startup_error: string | null;
    status_error?: string | null;
    proxy_error?: {
        status_code?: number;
        detail?: unknown;
    } | null;
    board_status?: Record<string, any> | null;
    deck_io_snapshot?: Record<string, number | null> | null;
}

export interface LinkageStatus {
    url: string | null;
    configured?: boolean;
    recommended_url?: string | null;
}

export interface AxisStatus {
    axis: string;
    preset: Record<string, any>;
    status: {
        position?: { position: number | null; ok: boolean };
        speed?: { speed: number | null; ok: boolean };
        max_current?: { value?: number | null; ok?: boolean };
        switches?: {
            left_state?: number | null;
            right_state?: number | null;
        };
    };
    switch_activity: {
        left_state?: number | null;
        right_state?: number | null;
        left_active?: boolean | null;
        right_active?: boolean | null;
    };
}

export interface AxisStatusBatchResponse {
    axes: AxisName[];
    rows: Partial<Record<AxisName, AxisStatus>>;
}

export interface MotionPowerStatus {
    hardware_connected?: boolean;
    board_status?: Record<string, any> | null;
    deck_io_snapshot?: Record<string, number | null> | null;
    rail_24v?: {
        raw?: number | null;
        no24v?: boolean | null;
        ack?: Record<string, any> | null;
    } | null;
    motion_arm?: Record<string, any> | null;
    latch_override?: Record<string, any> | null;
}

export interface CameraSnapshotResponse {
    ok: boolean;
    device: string | null;
    path: string | null;
    size?: number;
    rc?: number;
    output?: string;
    error?: string | null;
    image_b64?: string | null;
    image_error?: string | null;
}

export interface CameraDevicesResponse {
    ok: boolean;
    rows: Array<Record<string, any>>;
    preferred_device?: string | null;
}

export interface CameraControlRow {
    cid: number;
    type: number;
    name: string;
    minimum: number;
    maximum: number;
    step: number;
    default: number;
    flags: number;
    get?: {
        ok?: boolean;
        value?: number | null;
        device?: string;
        error?: string;
    };
}

export interface CameraControlsResponse {
    ok: boolean;
    device: string;
    rows: CameraControlRow[];
    error?: string | null;
}

export interface CameraControlWriteResponse {
    ok: boolean;
    cid: number;
    set_value: number;
    readback?: number | null;
    device: string;
    error?: string | null;
    stream_active?: boolean;
    stream_state?: Record<string, any>;
}

export interface CameraStreamOptions {
    device: string;
    fps?: number;
    quality?: number;
    width?: number;
    height?: number;
    nonce?: number;
}

export interface DaemonStatus {
    running: boolean;
    host: string;
    port: number;
    detail: string | null;
}

const invalidateBioXp = (queryClient: ReturnType<typeof useQueryClient>) => {
    queryClient.invalidateQueries({ queryKey: ['bioxp'] });
};

const bioxpHardwareMutationKey = (...parts: string[]) => ['bioxp', 'hardware', ...parts] as const;

export const useGetLinkage = () =>
    useQuery<LinkageStatus, Error>({
        queryKey: ['bioxp', 'linkage'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/linkage');
            return res.data;
        }
    });

export const useSetLinkage = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (url: string) => {
            const res = await api.post('/api/bioxp/linkage', { url });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useDisconnectLinkage = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/linkage/disconnect');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useDaemonStatus = () =>
    useQuery<DaemonStatus, Error>({
        queryKey: ['bioxp', 'daemon'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/daemon/status');
            return res.data;
        },
        refetchInterval: 10000,
        retry: false,
    });

export const useDaemonStart = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/daemon/start');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useDaemonStop = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/daemon/stop');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useBioXpStatus = (enabled = true, refetchIntervalMs: number | false = 5000) =>
    useQuery<BioXpStatus, Error>({
        queryKey: ['bioxp', 'status'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/status');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const useReconnectRuntime = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('runtime', 'reconnect'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/reconnect');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useAxisStatus = (axis: AxisName | null, enabled = true, refetchIntervalMs: number | false = 3000) =>
    useQuery<AxisStatus, Error>({
        queryKey: ['bioxp', 'axis', axis],
        queryFn: async () => {
            const res = await api.get(`/api/bioxp/motion/axis/${axis}/status`);
            return res.data;
        },
        enabled: !!axis && enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const useAxisStatusBatch = (axes: AxisName[], enabled = true, refetchIntervalMs: number | false = 3000) =>
    useQuery<AxisStatusBatchResponse, Error>({
        queryKey: ['bioxp', 'axis-batch', axes.join(',')],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/motion/axes/status', {
                params: { axes: axes.join(',') },
            });
            return res.data;
        },
        enabled: enabled && axes.length > 0,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const usePrepareInterlock = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'interlock', 'prepare'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/motion/interlock/prepare');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useMotionPowerStatus = (enabled = true, refetchIntervalMs: number | false = 8000) =>
    useQuery<MotionPowerStatus, Error>({
        queryKey: ['bioxp', 'motion', 'power', 'status'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/motion/power/status');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? refetchIntervalMs : false,
        retry: false,
    });

export const useMotionPowerEnable = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'power', 'enable'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/motion/power/enable');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useMotionPowerDiag = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'power', 'diag'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/motion/power/diag');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useMotionHardReset = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'hard-reset'),
        mutationFn: async ({ rounds }: { rounds: number }) => {
            const res = await api.post('/api/bioxp/motion/hard_reset', { rounds });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useClearLock = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'clear-lock'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/motion/clear_lock');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useMoveRelative = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'relative'),
        mutationFn: async ({ axis, steps, wait_timeout_s = 15.0 }: { axis: AxisName; steps: number; wait_timeout_s?: number }) => {
            const res = await api.post('/api/bioxp/motion/axis/relative', { axis, steps, wait_timeout_s });
            return res.data;
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis', variables.axis] });
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis-batch'] });
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'status'] });
        }
    });
};

export const useMoveAbsolute = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'absolute'),
        mutationFn: async ({ axis, position_steps }: { axis: AxisName; position_steps: number }) => {
            const res = await api.post('/api/bioxp/motion/axis/absolute', { axis, position_steps, wait_timeout_s: 60.0 });
            return res.data;
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis', variables.axis] });
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis-batch'] });
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'status'] });
        }
    });
};

export const useHomeAxis = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('motion', 'home'),
        mutationFn: async ({ axis }: { axis: AxisName }) => {
            const res = await api.post('/api/bioxp/motion/axis/home', { axis, timeout_s: 20.0 });
            return res.data;
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis', variables.axis] });
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis-batch'] });
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'status'] });
        }
    });
};

export const useLatchStatus = (enabled = true) =>
    useQuery<BioXpPayload, Error>({
        queryKey: ['bioxp', 'latch'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/latch/status');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? 5000 : false,
        retry: false,
    });

export const useLatchLock = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('latch', 'lock'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/latch/lock');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useLatchUnlock = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('latch', 'unlock'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/latch/unlock');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useLedRgb = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('led', 'rgb'),
        mutationFn: async (payload: { r: number; g: number; b: number }) => {
            const res = await api.post('/api/bioxp/led/rgb', payload);
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useLedPct = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('led', 'pct'),
        mutationFn: async (pct: number) => {
            const res = await api.post('/api/bioxp/led/pct', { pct });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useLedOff = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('led', 'off'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/led/off');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useThermalSnapshot = (enabled = true) =>
    useQuery<BioXpPayload, Error>({
        queryKey: ['bioxp', 'thermal', 'snapshot'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/thermal/snapshot');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? 5000 : false,
        retry: false,
    });

export const useSetThermalTemp = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('thermal', 'set-temp'),
        mutationFn: async ({ bank, target_temp_c }: { bank: ThermalBankName; target_temp_c: number }) => {
            const res = await api.post('/api/bioxp/thermal/set_temp', { bank, target_temp_c });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useSetThermalFan = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('thermal', 'fan'),
        mutationFn: async ({ speed }: { speed: number }) => {
            const res = await api.post('/api/bioxp/thermal/fan', { speed });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useSetThermalPwm = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('thermal', 'pwm'),
        mutationFn: async ({ bank, pwm }: { bank: ThermalBankName; pwm: number }) => {
            const res = await api.post('/api/bioxp/thermal/pwm', { bank, pwm });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useSetThermalRates = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('thermal', 'rates'),
        mutationFn: async ({ bank, cool_rate_c_s, heat_rate_c_s }: { bank: ThermalBankName; cool_rate_c_s: number; heat_rate_c_s: number }) => {
            const res = await api.post('/api/bioxp/thermal/rates', { bank, cool_rate_c_s, heat_rate_c_s });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useThermalBaseline = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('thermal', 'baseline'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/thermal/baseline');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useThermalFastProfile = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('thermal', 'fast-profile'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/thermal/fast_profile');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useThermalHardReset = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('thermal', 'hard-reset'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/thermal/hard_reset');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useChillerSnapshot = (enabled = true) =>
    useQuery<BioXpPayload, Error>({
        queryKey: ['bioxp', 'chiller', 'snapshot'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/chiller/snapshot');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? 5000 : false,
        retry: false,
    });

export const useSetChillerTemp = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('chiller', 'set-temp'),
        mutationFn: async ({ bank, target_temp_c }: { bank: ChillerBankName; target_temp_c: number }) => {
            const res = await api.post('/api/bioxp/chiller/set_temp', { bank, target_temp_c });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useSetChillerFan = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('chiller', 'fan'),
        mutationFn: async ({ bank, speed }: { bank: ChillerBankName; speed: number }) => {
            const res = await api.post('/api/bioxp/chiller/fan', { bank, speed });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useSetChillerPwm = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('chiller', 'pwm'),
        mutationFn: async ({ bank, pwm }: { bank: ChillerBankName; pwm: number }) => {
            const res = await api.post('/api/bioxp/chiller/pwm', { bank, pwm });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useSetChillerRates = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('chiller', 'rates'),
        mutationFn: async ({ bank, cool_rate_c_s, heat_rate_c_s }: { bank: ChillerBankName; cool_rate_c_s: number; heat_rate_c_s: number }) => {
            const res = await api.post('/api/bioxp/chiller/rates', { bank, cool_rate_c_s, heat_rate_c_s });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useChillerBaseline = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('chiller', 'baseline'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/chiller/baseline');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useChillerHardReset = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationKey: bioxpHardwareMutationKey('chiller', 'hard-reset'),
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/chiller/hard_reset');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useCameraDevices = (enabled = true) =>
    useQuery<CameraDevicesResponse, Error>({
        queryKey: ['bioxp', 'camera', 'devices'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/camera/devices');
            return res.data;
        },
        enabled,
        refetchInterval: enabled ? 10000 : false,
        retry: false,
    });

export const useCameraControls = (device: string, enabled = true) =>
    useQuery<CameraControlsResponse, Error>({
        queryKey: ['bioxp', 'camera', 'controls', device],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/camera/controls', { params: { device } });
            return res.data;
        },
        enabled: enabled && !!device,
        refetchInterval: enabled ? 10000 : false,
        retry: false,
    });

export const useSetCameraControl = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async ({ device, cid, value }: { device: string; cid: number; value: number }) => {
            const res = await api.post('/api/bioxp/camera/control', { device, cid, value });
            return res.data as CameraControlWriteResponse;
        },
        onSuccess: (data, variables) => {
            queryClient.setQueryData<CameraControlsResponse | undefined>(
                ['bioxp', 'camera', 'controls', variables.device],
                (current) => {
                    if (!current?.rows?.length) {
                        return current;
                    }
                    return {
                        ...current,
                        rows: current.rows.map((row) =>
                            row.cid === variables.cid
                                ? {
                                    ...row,
                                    get: {
                                        ...(row.get ?? {}),
                                        ok: data.ok,
                                        value: typeof data.readback === 'number' ? data.readback : variables.value,
                                        device: variables.device,
                                    },
                                }
                                : row,
                        ),
                    };
                },
            );
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'camera', 'controls', variables.device] });
        },
    });
};

export const useCameraSnapshot = () =>
    useMutation({
        mutationFn: async ({ device }: { device: string }) => {
            const res = await api.post('/api/bioxp/camera/snapshot', { device });
            return res.data as CameraSnapshotResponse;
        }
    });

export const useCameraStreamHealth = () =>
    useMutation({
        mutationFn: async ({ device, seconds }: { device: string; seconds: number }) => {
            const res = await api.post('/api/bioxp/camera/stream_health', { device, seconds });
            return res.data;
        }
    });

export const useCameraAutoRecover = () =>
    useMutation({
        mutationFn: async ({ device, max_resets }: { device: string; max_resets: number }) => {
            const res = await api.post('/api/bioxp/camera/auto_recover', { device, max_resets });
            return res.data;
        }
    });

export const useCameraReset = () =>
    useMutation({
        mutationFn: async ({ device }: { device: string }) => {
            const res = await api.post('/api/bioxp/camera/reset', { device });
            return res.data;
        }
    });

export const useCameraStop = () =>
    useMutation({
        mutationFn: async ({ device }: { device: string }) => {
            const res = await api.post('/api/bioxp/camera/stop', { device });
            return res.data;
        }
    });

export const getCameraStreamUrl = ({
    device,
    fps = 8,
    quality = 7,
    width = 640,
    height = 480,
    nonce,
}: CameraStreamOptions) => {
    const params = new URLSearchParams({
        device,
        fps: String(fps),
        quality: String(quality),
        width: String(width),
        height: String(height),
    });
    if (nonce != null) {
        params.set('nonce', String(nonce));
    }
    return `/api/bioxp/camera/mjpeg?${params.toString()}`;
};
