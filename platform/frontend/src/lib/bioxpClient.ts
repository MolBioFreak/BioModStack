import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from './api';

// Types derived from the BioXP API
export type AxisName = 'x' | 'y' | 'z' | 'g' | 'door';
export type ThermalBankName = 'nest' | 'lid' | 'pedestal';
export type ChillerBankName = 'rc' | 'oc';

export interface BioXpStatus {
    status: string;
    transport: string;
    hardware_connected: boolean;
    startup_error: string | null;
}

export interface AxisStatus {
    axis: string;
    preset: any;
    status: {
        raw: number;
        position_reached: boolean;
        switch_left: boolean;
        switch_right: boolean;
        standstill: boolean;
        sg_error: boolean;
    };
    switch_activity: {
        raw: number;
        sw1_active: boolean;
        sw2_active: boolean;
        sw3_active: boolean;
    };
}

// Hooks
export const useGetLinkage = () => {
    return useQuery<{ url: string | null }, Error>({
        queryKey: ['bioxp', 'linkage'],
        queryFn: async () => {
            const res = await api.get('/bioxp/linkage');
            return res.data;
        }
    });
};

export const useSetLinkage = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (url: string) => {
            const res = await api.post('/bioxp/linkage', { url });
            return res.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['bioxp'] });
        }
    });
};

export const useDisconnectLinkage = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async () => {
            const res = await api.post('/bioxp/linkage/disconnect');
            return res.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['bioxp'] });
        }
    });
};

export interface DaemonStatus {
    running: boolean;
    host: string;
    port: number;
    detail: string | null;
}

export const useDaemonStatus = () => {
    return useQuery<DaemonStatus, Error>({
        queryKey: ['bioxp', 'daemon'],
        queryFn: async () => {
            const res = await api.get('/bioxp/daemon/status');
            return res.data;
        },
        refetchInterval: 10000,
        retry: false,
    });
};

export const useDaemonStart = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async () => {
            const res = await api.post('/bioxp/daemon/start');
            return res.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['bioxp'] });
        }
    });
};

export const useDaemonStop = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async () => {
            const res = await api.post('/bioxp/daemon/stop');
            return res.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['bioxp'] });
        }
    });
};

export const useBioXpStatus = () => {
    return useQuery<BioXpStatus, Error>({
        queryKey: ['bioxp', 'status'],
        queryFn: async () => {
            const res = await api.get('/bioxp/status');
            return res.data;
        },
        refetchInterval: 5000,
        retry: false // Fail fast if hardware is completely offline, respecting the strict no-sim policy.
    });
};

export const useAxisStatus = (axis: AxisName | null) => {
    return useQuery<AxisStatus, Error>({
        queryKey: ['bioxp', 'axis', axis],
        queryFn: async () => {
            const res = await api.get(`/bioxp/motion/axis/${axis}/status`);
            return res.data;
        },
        enabled: !!axis,
        refetchInterval: 2000,
    });
};

export const useMoveRelative = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async ({ axis, steps }: { axis: AxisName, steps: number }) => {
            const res = await api.post('/bioxp/motion/axis/relative', { axis, steps, wait_timeout_s: 15.0 });
            return res.data;
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis', variables.axis] });
        }
    });
};

export const useMoveAbsolute = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async ({ axis, position_steps }: { axis: AxisName, position_steps: number }) => {
            const res = await api.post('/bioxp/motion/axis/absolute', { axis, position_steps, wait_timeout_s: 60.0 });
            return res.data;
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis', variables.axis] });
        }
    });
};

export const useHomeAxis = () => {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async ({ axis }: { axis: AxisName }) => {
            const res = await api.post('/bioxp/motion/axis/home', { axis, timeout_s: 45.0 });
            return res.data;
        },
        onSuccess: (_, variables) => {
            queryClient.invalidateQueries({ queryKey: ['bioxp', 'axis', variables.axis] });
        }
    });
};

export const useSetThermalTemp = () => {
    return useMutation({
        mutationFn: async ({ bank, target_temp_c }: { bank: ThermalBankName, target_temp_c: number }) => {
            const res = await api.post('/bioxp/thermal/set_temp', { bank, target_temp_c });
            return res.data;
        }
    });
};

export const useSetChillerTemp = () => {
    return useMutation({
        mutationFn: async ({ bank, target_temp_c }: { bank: ChillerBankName, target_temp_c: number }) => {
            const res = await api.post('/bioxp/chiller/set_temp', { bank, target_temp_c });
            return res.data;
        }
    });
};

export const useCameraSnapshot = () => {
    return useMutation({
        mutationFn: async () => {
            const res = await api.post('/bioxp/camera/snapshot', { device: '/dev/video0' });
            return res.data;
        }
    });
};
