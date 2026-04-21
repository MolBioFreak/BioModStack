export type PointerZoomGesture = {
  ctrlKey: boolean;
  metaKey: boolean;
  deltaY: number;
};

export function resolvePointerZoomStep(gesture: PointerZoomGesture): 1 | -1 | null {
  if ((!gesture.ctrlKey && !gesture.metaKey) || gesture.deltaY === 0) {
    return null;
  }
  return gesture.deltaY < 0 ? 1 : -1;
}
