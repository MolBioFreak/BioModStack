export type GraphicsWorkaroundOptions = {
  platform?: NodeJS.Platform;
  env?: NodeJS.ProcessEnv;
};

export type HardwareAccelerationApp = {
  disableHardwareAcceleration: () => void;
};

function parseBooleanOverride(value: string | undefined): boolean | undefined {
  if (!value) {
    return undefined;
  }
  const normalized = value.trim().toLowerCase();
  if (!normalized) {
    return undefined;
  }
  if (['1', 'true', 'yes', 'on'].includes(normalized)) {
    return true;
  }
  if (['0', 'false', 'no', 'off'].includes(normalized)) {
    return false;
  }
  return undefined;
}

export function shouldDisableGpuAcceleration(options: GraphicsWorkaroundOptions = {}): boolean {
  const env = options.env ?? process.env;
  const override = parseBooleanOverride(env.BMS_ELECTRON_DISABLE_GPU);
  if (typeof override === 'boolean') {
    return override;
  }
  return false;
}

export function applyShellGraphicsWorkarounds(
  appLike: HardwareAccelerationApp,
  options: GraphicsWorkaroundOptions = {},
): boolean {
  if (!shouldDisableGpuAcceleration(options)) {
    return false;
  }
  appLike.disableHardwareAcceleration();
  return true;
}
