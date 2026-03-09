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

export const useBioXpStatus = () =>
    useQuery<BioXpStatus, Error>({
        queryKey: ['bioxp', 'status'],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/status');
            return res.data;
        },
        refetchInterval: 5000,
        retry: false,
    });

export const useReconnectRuntime = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/reconnect');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useAxisStatus = (axis: AxisName | null, enabled = true) =>
    useQuery<AxisStatus, Error>({
        queryKey: ['bioxp', 'axis', axis],
        queryFn: async () => {
            const res = await api.get(`/api/bioxp/motion/axis/${axis}/status`);
            return res.data;
        },
        enabled: !!axis && enabled,
        refetchInterval: enabled ? 3000 : false,
        retry: false,
    });

export const usePrepareInterlock = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async () => {
            const res = await api.post('/api/bioxp/motion/interlock/prepare');
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useClearLock = () => {
    const queryClient = useQueryClient();
    return useMutation({
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
        mutationFn: async ({ axis, steps }: { axis: AxisName; steps: number }) => {
            const res = await api.post('/api/bioxp/motion/axis/relative', { axis, steps, wait_timeout_s: 15.0 });
            return res.data;
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis', variables.axis] });
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'status'] });
        }
    });
};

export const useMoveAbsolute = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async ({ axis, position_steps }: { axis: AxisName; position_steps: number }) => {
            const res = await api.post('/api/bioxp/motion/axis/absolute', { axis, position_steps, wait_timeout_s: 60.0 });
            return res.data;
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis', variables.axis] });
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'status'] });
        }
    });
};

export const useHomeAxis = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async ({ axis }: { axis: AxisName }) => {
            const res = await api.post('/api/bioxp/motion/axis/home', { axis, timeout_s: 45.0 });
            return res.data;
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis', variables.axis] });
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
        mutationFn: async ({ bank, target_temp_c }: { bank: ThermalBankName; target_temp_c: number }) => {
            const res = await api.post('/api/bioxp/thermal/set_temp', { bank, target_temp_c });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useThermalBaseline = () => {
    const queryClient = useQueryClient();
    return useMutation({
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
        mutationFn: async ({ bank, target_temp_c }: { bank: ChillerBankName; target_temp_c: number }) => {
            const res = await api.post('/api/bioxp/chiller/set_temp', { bank, target_temp_c });
            return res.data;
        },
        onSuccess: () => invalidateBioXp(queryClient)
    });
};

export const useChillerBaseline = () => {
    const queryClient = useQueryClient();
    return useMutation({
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
    useQuery<BioXpPayload, Error>({
        queryKey: ['bioxp', 'camera', 'controls', device],
        queryFn: async () => {
            const res = await api.get('/api/bioxp/camera/controls', { params: { device } });
            return res.data;
        },
        enabled: enabled && !!device,
        refetchInterval: enabled ? 10000 : false,
        retry: false,
    });

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
