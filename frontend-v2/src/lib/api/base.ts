export const DEFAULT_API_BASE = 'http://127.0.0.1:8766/api/v1';

export function resolveApiBase(value: string | undefined | null) {
  return String(value || DEFAULT_API_BASE).replace(/\/+$/, '');
}

const API_BASE = resolveApiBase(import.meta.env.VITE_SUBTITLE_API_BASE);

export function getApiBase() {
  return API_BASE;
}
