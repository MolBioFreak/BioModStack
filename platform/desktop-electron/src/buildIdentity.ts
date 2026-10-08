export type ElectronBuildIdentity = {
  layer: 'electron';
  revision: string;
  buildId: string;
  buildTime: string;
  appVersion: string;
};

const FULL_GIT_SHA = /^[0-9a-f]{40}$/;

function clean(value: string | undefined, fallback: string): string {
  return value?.trim() || fallback;
}

export function resolveElectronBuildIdentity(
  environment: NodeJS.ProcessEnv | Record<string, string | undefined>,
  appVersion: string,
): ElectronBuildIdentity {
  const rawRevision = clean(environment.BMS_BUILD_SHA, 'unknown');
  return Object.freeze({
    layer: 'electron',
    revision: FULL_GIT_SHA.test(rawRevision) ? rawRevision : 'unknown',
    buildId: clean(environment.BMS_BUILD_ID, 'development'),
    buildTime: clean(environment.BMS_BUILD_TIME, 'unknown'),
    appVersion: clean(appVersion, 'unknown'),
  });
}
