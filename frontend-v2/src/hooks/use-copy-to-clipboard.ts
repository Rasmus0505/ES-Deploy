import { useEffect, useRef, useState } from 'react';

type UseCopyToClipboardOptions = {
  timeout?: number;
  onCopy?: () => void;
};

export function useCopyToClipboard(options: UseCopyToClipboardOptions = {}) {
  const { timeout = 2000, onCopy } = options;
  const [isCopied, setIsCopied] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) {
        window.clearTimeout(timerRef.current);
      }
    };
  }, []);

  const copyToClipboard = (value: string) => {
    if (typeof window === 'undefined' || !navigator.clipboard?.writeText) return;
    if (!value) return;

    void navigator.clipboard.writeText(value).then(() => {
      setIsCopied(true);
      onCopy?.();

      if (timeout === 0) return;
      if (timerRef.current) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => {
        setIsCopied(false);
      }, timeout);
    }).catch(() => {
      // Clipboard permission can be denied by browser policy.
    });
  };

  return { isCopied, copyToClipboard };
}
