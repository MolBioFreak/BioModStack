import { useEffect } from 'react';

export function useMolBioBodyScrollLock(
  isViewerFullscreen: boolean,
  isMobileMolBio: boolean,
): void {
  const locked = isViewerFullscreen || isMobileMolBio;
  useEffect(() => {
    if (!locked) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [locked]);
}
