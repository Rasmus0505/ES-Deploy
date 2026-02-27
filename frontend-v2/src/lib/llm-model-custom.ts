const STORAGE_KEY = 'autoLlmModelCustomList';

const normalizeLlmModel = (value: string) => String(value || '').trim();

const getLlmModelKey = (value: string) => normalizeLlmModel(value).toLowerCase();

const dedupeModels = (values: readonly string[]) => {
  const next: string[] = [];
  const seen = new Set<string>();
  values.forEach((value) => {
    const normalized = normalizeLlmModel(value);
    if (!normalized) return;
    const key = getLlmModelKey(normalized);
    if (seen.has(key)) return;
    seen.add(key);
    next.push(normalized);
  });
  return next;
};

export function getCustomLlmModels() {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return dedupeModels(parsed.filter((item) => typeof item === 'string') as string[]);
  } catch {
    return [];
  }
}

export function setCustomLlmModels(values: readonly string[]) {
  const normalized = dedupeModels(values);
  if (typeof window === 'undefined') return normalized;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
  } catch {
    // ignore localStorage write failures
  }
  return normalized;
}

export function isValidLlmModel(value: string) {
  return normalizeLlmModel(value).length > 0;
}

export { STORAGE_KEY as customLlmModelStorageKey, getLlmModelKey, normalizeLlmModel };
