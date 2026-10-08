type CordovaReadyHook = () => void

export type CordovaShellReadyTarget = object & {
  __BMS_CORDOVA_CONFIRM_READY__?: unknown
}

const signaledTargets = new WeakSet<object>()

function getCordovaReadyHook(target: CordovaShellReadyTarget): CordovaReadyHook | undefined {
  const candidate = target.__BMS_CORDOVA_CONFIRM_READY__
  return typeof candidate === 'function' ? (candidate as CordovaReadyHook) : undefined
}

export function signalCordovaAppReady(target: CordovaShellReadyTarget = globalThis): void {
  const readyHook = getCordovaReadyHook(target)
  if (!readyHook || signaledTargets.has(target)) {
    return
  }

  signaledTargets.add(target)
  readyHook()
}
