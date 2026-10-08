import { execFile, type ExecFileOptionsWithStringEncoding } from 'node:child_process';
import path from 'node:path';

export type ServiceRuntimeMode = 'dev' | 'container';
export type ServiceRuntimeTarget = 'dev' | 'prod' | 'both';
export type ServiceManagerAction = 'status' | 'start' | 'start-target' | 'stop' | 'restart' | 'restart-api';
export type ServiceStatusPayload = Record<string, unknown>;

export type ServiceManagerInvocation = {
  command: string;
  args: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
};

export type ServiceManagerRunResult = {
  stdout: string;
  stderr: string;
  exitCode: number;
};

export type ServiceControl = {
  getStatus: (runtimeMode?: ServiceRuntimeMode) => Promise<ServiceStatusPayload>;
  startAll: (runtimeMode?: ServiceRuntimeMode) => Promise<void>;
  startRuntimeTarget: (target: ServiceRuntimeTarget) => Promise<void>;
  stopAll: (runtimeMode?: ServiceRuntimeMode) => Promise<void>;
  restartAll: (runtimeMode?: ServiceRuntimeMode) => Promise<void>;
  restartApi: (runtimeMode?: ServiceRuntimeMode) => Promise<void>;
};

type ServiceControlOptions = {
  projectRoot?: string;
  pythonCommand?: string;
  run?: (invocation: ServiceManagerInvocation) => Promise<ServiceManagerRunResult> | ServiceManagerRunResult;
};

type InvocationOptions = {
  projectRoot?: string;
  pythonCommand?: string;
  runtimeMode?: ServiceRuntimeMode;
  target?: ServiceRuntimeTarget;
  json?: boolean;
};

function resolveProjectRoot(projectRoot?: string): string {
  if (projectRoot?.trim()) {
    return path.resolve(projectRoot);
  }
  if (process.env.BMS_HOME?.trim()) {
    return path.resolve(process.env.BMS_HOME);
  }
  return path.resolve(__dirname, '..', '..', '..', '..');
}

export function buildManageDesktopServicesInvocation(
  action: ServiceManagerAction,
  options: InvocationOptions = {},
): ServiceManagerInvocation {
  const projectRoot = resolveProjectRoot(options.projectRoot);
  const scriptPath = path.join(projectRoot, 'scripts', 'manage_desktop_services.py');
  const args = [scriptPath, action];

  if (options.runtimeMode) {
    args.push('--runtime', options.runtimeMode);
  }
  if (options.target) {
    args.push('--target', options.target);
  }
  if (options.json) {
    args.push('--json');
  }

  return {
    command: options.pythonCommand ?? process.env.PYTHON ?? 'python3',
    args,
    cwd: projectRoot,
    env: {
      ...process.env,
      BMS_HOME: projectRoot,
    },
  };
}

async function defaultRun(invocation: ServiceManagerInvocation): Promise<ServiceManagerRunResult> {
  return await new Promise((resolve, reject) => {
    const options: ExecFileOptionsWithStringEncoding = {
      cwd: invocation.cwd,
      env: invocation.env,
      encoding: 'utf8',
    };

    execFile(invocation.command, invocation.args, options, (error, stdout, stderr) => {
      if (error) {
        if (typeof error.code === 'string') {
          reject(error);
          return;
        }
        resolve({
          stdout: stdout ?? '',
          stderr: stderr ?? '',
          exitCode: typeof error.code === 'number' ? error.code : 1,
        });
        return;
      }

      resolve({
        stdout: stdout ?? '',
        stderr: stderr ?? '',
        exitCode: 0,
      });
    });
  });
}

function getFailureMessage(action: ServiceManagerAction, result: ServiceManagerRunResult): string {
  const detail = result.stderr.trim() || result.stdout.trim();
  if (detail) {
    return detail;
  }
  return `manage_desktop_services.py ${action} failed with exit code ${result.exitCode}`;
}

function assertSuccessfulResult(action: ServiceManagerAction, result: ServiceManagerRunResult): void {
  if (result.exitCode !== 0) {
    throw new Error(getFailureMessage(action, result));
  }
}

function parseStatusPayload(result: ServiceManagerRunResult): ServiceStatusPayload {
  assertSuccessfulResult('status', result);
  try {
    const parsed = JSON.parse(result.stdout);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('status payload must be an object');
    }
    return parsed as ServiceStatusPayload;
  } catch (error) {
    throw new Error(
      `Invalid service-manager status payload: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

export function createServiceControl(options: ServiceControlOptions = {}): ServiceControl {
  const run = options.run ?? defaultRun;

  async function invoke(
    action: ServiceManagerAction,
    runtimeMode?: ServiceRuntimeMode,
    json?: boolean,
    target?: ServiceRuntimeTarget,
  ) {
    const invocation = buildManageDesktopServicesInvocation(action, {
      projectRoot: options.projectRoot,
      pythonCommand: options.pythonCommand,
      runtimeMode,
      json,
      target,
    });
    return await run(invocation);
  }

  return {
    getStatus: async (runtimeMode) => parseStatusPayload(await invoke('status', runtimeMode, true)),
    startAll: async (runtimeMode) => assertSuccessfulResult('start', await invoke('start', runtimeMode)),
    startRuntimeTarget: async (target) => assertSuccessfulResult('start-target', await invoke('start-target', undefined, false, target)),
    stopAll: async (runtimeMode) => assertSuccessfulResult('stop', await invoke('stop', runtimeMode)),
    restartAll: async (runtimeMode) => assertSuccessfulResult('restart', await invoke('restart', runtimeMode)),
    restartApi: async (runtimeMode) => assertSuccessfulResult('restart-api', await invoke('restart-api', runtimeMode)),
  };
}
