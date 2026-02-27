import { getApiBase } from '../lib/api/base';

type UnknownRecord = Record<string, unknown>;

type HistoryRecordLike = {
  videoName: string;
  srtName: string;
  currentIndex: number;
  totalSentences: number;
  thumbnail: string;
  timestamp: number;
  completed: boolean;
  historyId: string;
  displayName: string;
  folderId: string;
};

type OriginStatePayload = {
  version: 'origin-state-bridge.v1';
  sourceHost: string;
  sourceOrigin: string;
  createdAt: number;
  limited: boolean;
  storage: Record<string, string>;
};

const PAYLOAD_PREFIX = '__origin_state_bridge_v1__';
const CANONICAL_HOST = 'localhost';
const SOURCE_HOST = '127.0.0.1';
const MAX_WINDOW_NAME_BYTES = 1_500_000;
const MAX_HISTORY_ROWS = 400;

const HISTORY_KEY = 'learningHistory';
const HISTORY_REV_KEY = 'learningHistoryRevision';
const DAILY_STATS_KEY = 'dailyStats';
const MODULE_DAILY_STATS_KEY = 'moduleDailyStats';
const DAILY_AGG_KEY = 'dailyAgg';

const MAX_NUMERIC_KEYS = new Set([
  'userXP',
  'userLevel',
  'totalStudySeconds',
  'totalCorrectWords',
  'totalSessions',
  'totalListeningStudySeconds',
  'totalListeningWords',
  'totalReadingStudySeconds',
  'totalReadingWords',
  'totalVocabStudySeconds',
  'totalVocabWords',
  'longGoalMinutes',
  'longGoalSentences'
]);

const MIN_NUMERIC_KEYS = new Set([
  'dailyGoalMinutes',
  'dailyGoalSentences'
]);

const PRIORITY_KEYS = new Set([
  HISTORY_KEY,
  HISTORY_REV_KEY,
  DAILY_STATS_KEY,
  MODULE_DAILY_STATS_KEY,
  DAILY_AGG_KEY,
  'currentSession',
  'listeningPracticeProgress',
  'activeModule',
  'userXP',
  'userLevel',
  'totalStudySeconds',
  'totalCorrectWords',
  'totalSessions',
  'totalListeningStudySeconds',
  'totalListeningWords',
  'dailyGoalMinutes',
  'dailyGoalSentences',
  'longGoalMinutes',
  'longGoalSentences',
  'subtitleSourceMode',
  'dashboardRangeDays'
]);

const API_BASE = getApiBase();

let bridgeBootstrapped = false;

function isPlainObject(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function toFiniteNumber(value: unknown): number | null {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return numeric;
}

function decodeJson<T>(raw: string): T | null {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function encodeBase64Utf8(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = '';
  bytes.forEach((item) => {
    binary += String.fromCharCode(item);
  });
  return btoa(binary);
}

function decodeBase64Utf8(value: string): string {
  const binary = atob(value);
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

function collectStorageSnapshot(priorityOnly: boolean): Record<string, string> {
  const snapshot: Record<string, string> = {};
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (!key) continue;
    if (priorityOnly && !PRIORITY_KEYS.has(key) && !key.startsWith('auto')) continue;
    const value = localStorage.getItem(key);
    if (value === null) continue;
    snapshot[key] = value;
  }
  return snapshot;
}

function buildPayload(storage: Record<string, string>, limited: boolean): OriginStatePayload {
  return {
    version: 'origin-state-bridge.v1',
    sourceHost: window.location.hostname,
    sourceOrigin: window.location.origin,
    createdAt: Date.now(),
    limited,
    storage
  };
}

function buildStorableWindowNamePayload(): string {
  const fullPayload = buildPayload(collectStorageSnapshot(false), false);
  const fullRaw = JSON.stringify(fullPayload);
  const fullBytes = new Blob([fullRaw]).size;
  if (fullBytes <= MAX_WINDOW_NAME_BYTES) {
    return `${PAYLOAD_PREFIX}${encodeBase64Utf8(fullRaw)}`;
  }

  const limitedPayload = buildPayload(collectStorageSnapshot(true), true);
  const limitedRaw = JSON.stringify(limitedPayload);
  const limitedBytes = new Blob([limitedRaw]).size;
  if (limitedBytes > MAX_WINDOW_NAME_BYTES) {
    throw new Error(`origin bridge payload too large: ${limitedBytes} bytes`);
  }
  return `${PAYLOAD_PREFIX}${encodeBase64Utf8(limitedRaw)}`;
}

function readPayloadFromWindowName(): OriginStatePayload | null {
  const rawName = String(window.name || '');
  if (!rawName.startsWith(PAYLOAD_PREFIX)) return null;
  const encoded = rawName.slice(PAYLOAD_PREFIX.length);
  if (!encoded) return null;
  try {
    const decoded = decodeBase64Utf8(encoded);
    const payload = decodeJson<OriginStatePayload>(decoded);
    if (!payload || payload.version !== 'origin-state-bridge.v1' || !isPlainObject(payload.storage)) {
      return null;
    }
    return payload;
  } catch {
    return null;
  }
}

function normalizeHistoryRow(value: unknown): HistoryRecordLike | null {
  if (!isPlainObject(value)) return null;
  const videoName = String(value.videoName || '').trim();
  const srtName = String(value.srtName || '').trim();
  if (!videoName || !srtName) return null;

  const totalSentences = Math.max(1, Math.round(toFiniteNumber(value.totalSentences) || 1));
  const currentIndex = Math.max(0, Math.min(Math.round(toFiniteNumber(value.currentIndex) || 0), totalSentences - 1));
  const timestamp = Math.max(0, Math.round(toFiniteNumber(value.timestamp) || Date.now()));
  const displayName = String(value.displayName || '').trim();
  const historyId = String(value.historyId || '').trim();

  return {
    videoName,
    srtName,
    currentIndex,
    totalSentences,
    thumbnail: String(value.thumbnail || ''),
    timestamp,
    completed: Boolean(value.completed),
    historyId: historyId || `${videoName}_${srtName}`,
    displayName: displayName || videoName.replace(/\.[^.]+$/, ''),
    folderId: String(value.folderId || '')
  };
}

function mergeLearningHistory(existingRaw: string, incomingRaw: string): string {
  const existing = decodeJson<unknown[]>(existingRaw) || [];
  const incoming = decodeJson<unknown[]>(incomingRaw) || [];
  const mergedMap = new Map<string, HistoryRecordLike>();

  [...existing, ...incoming].forEach((item) => {
    const row = normalizeHistoryRow(item);
    if (!row) return;

    const key = `${row.videoName}\u0000${row.srtName}`;
    const previous = mergedMap.get(key);
    if (!previous) {
      mergedMap.set(key, row);
      return;
    }

    const candidate = row.timestamp >= previous.timestamp ? row : previous;
    const fallback = candidate === row ? previous : row;
    mergedMap.set(key, {
      ...candidate,
      thumbnail: candidate.thumbnail || fallback.thumbnail,
      displayName: candidate.displayName || fallback.displayName,
      historyId: candidate.historyId || fallback.historyId
    });
  });

  const rows = Array.from(mergedMap.values())
    .sort((left, right) => right.timestamp - left.timestamp)
    .slice(0, MAX_HISTORY_ROWS);
  return JSON.stringify(rows);
}

function mergeMetricRow(existing: unknown, incoming: unknown): unknown {
  if (!isPlainObject(existing)) return incoming;
  if (!isPlainObject(incoming)) return existing;

  const merged: UnknownRecord = { ...existing };
  Object.keys(incoming).forEach((key) => {
    const left = merged[key];
    const right = incoming[key];

    if (isPlainObject(left) && isPlainObject(right)) {
      merged[key] = mergeMetricRow(left, right);
      return;
    }

    const leftNumber = toFiniteNumber(left);
    const rightNumber = toFiniteNumber(right);
    if (leftNumber !== null && rightNumber !== null) {
      merged[key] = Math.max(leftNumber, rightNumber);
      return;
    }

    if (left === undefined || left === null || left === '') {
      merged[key] = right;
    }
  });
  return merged;
}

function mergeDailyStats(existingRaw: string, incomingRaw: string): string {
  const existing = decodeJson<UnknownRecord>(existingRaw) || {};
  const incoming = decodeJson<UnknownRecord>(incomingRaw) || {};
  if (!isPlainObject(existing)) return incomingRaw;
  if (!isPlainObject(incoming)) return existingRaw;

  const merged: UnknownRecord = { ...existing };
  Object.keys(incoming).forEach((dateKey) => {
    const existingDay = merged[dateKey];
    const incomingDay = incoming[dateKey];
    merged[dateKey] = mergeMetricRow(existingDay, incomingDay);
  });
  return JSON.stringify(merged);
}

function mergeModuleDailyStats(existingRaw: string, incomingRaw: string): string {
  const existing = decodeJson<UnknownRecord>(existingRaw) || {};
  const incoming = decodeJson<UnknownRecord>(incomingRaw) || {};
  if (!isPlainObject(existing)) return incomingRaw;
  if (!isPlainObject(incoming)) return existingRaw;

  const merged: UnknownRecord = { ...existing };
  Object.keys(incoming).forEach((moduleKey) => {
    merged[moduleKey] = mergeMetricRow(merged[moduleKey], incoming[moduleKey]);
  });
  return JSON.stringify(merged);
}

function mergeNumericString(existingRaw: string, incomingRaw: string, mode: 'max' | 'min'): string {
  const left = toFiniteNumber(existingRaw);
  const right = toFiniteNumber(incomingRaw);
  if (left === null && right === null) return existingRaw || incomingRaw;
  if (left === null) return String(right);
  if (right === null) return String(left);
  return String(mode === 'min' ? Math.min(left, right) : Math.max(left, right));
}

function parseHistoryRevision(value: string | null | undefined): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  return Math.max(0, Math.floor(numeric));
}

function applyIncomingStorage(storage: Record<string, string>): number {
  let changed = 0;
  const incomingKeys = Object.keys(storage);

  incomingKeys.forEach((key) => {
    const incomingRaw = storage[key];
    if (typeof incomingRaw !== 'string') return;

    if (key === HISTORY_REV_KEY) {
      return;
    }

    if (key === HISTORY_KEY) {
      const existingRaw = localStorage.getItem(HISTORY_KEY) || '[]';
      const existingRev = parseHistoryRevision(localStorage.getItem(HISTORY_REV_KEY));
      const incomingRev = parseHistoryRevision(storage[HISTORY_REV_KEY]);

      if (incomingRev < existingRev) {
        // 忽略旧 host 的过期历史快照，避免删除后的条目被回灌。
        return;
      }

      if (incomingRev > existingRev) {
        const normalizedIncoming = mergeLearningHistory('[]', incomingRaw);
        if (normalizedIncoming !== existingRaw) {
          localStorage.setItem(HISTORY_KEY, normalizedIncoming);
          changed += 1;
        }
        localStorage.setItem(HISTORY_REV_KEY, String(incomingRev));
        changed += 1;
        return;
      }

      const merged = mergeLearningHistory(existingRaw, incomingRaw);
      if (merged !== existingRaw) {
        localStorage.setItem(HISTORY_KEY, merged);
        changed += 1;
      }
      if (incomingRev > 0 && incomingRev !== existingRev) {
        localStorage.setItem(HISTORY_REV_KEY, String(incomingRev));
        changed += 1;
      }
      return;
    }

    const existingRaw = localStorage.getItem(key);
    if (existingRaw === null) {
      localStorage.setItem(key, incomingRaw);
      changed += 1;
      return;
    }

    if (key === DAILY_STATS_KEY || key === DAILY_AGG_KEY) {
      const merged = mergeDailyStats(existingRaw, incomingRaw);
      if (merged !== existingRaw) {
        localStorage.setItem(key, merged);
        changed += 1;
      }
      return;
    }

    if (key === MODULE_DAILY_STATS_KEY) {
      const merged = mergeModuleDailyStats(existingRaw, incomingRaw);
      if (merged !== existingRaw) {
        localStorage.setItem(key, merged);
        changed += 1;
      }
      return;
    }

    if (MAX_NUMERIC_KEYS.has(key)) {
      const merged = mergeNumericString(existingRaw, incomingRaw, 'max');
      if (merged !== existingRaw) {
        localStorage.setItem(key, merged);
        changed += 1;
      }
      return;
    }

    if (MIN_NUMERIC_KEYS.has(key)) {
      const merged = mergeNumericString(existingRaw, incomingRaw, 'min');
      if (merged !== existingRaw) {
        localStorage.setItem(key, merged);
        changed += 1;
      }
      return;
    }
  });

  return changed;
}

function readLearningHistoryFromStorage(): HistoryRecordLike[] {
  const rows = decodeJson<unknown[]>(localStorage.getItem(HISTORY_KEY) || '') || [];
  return rows
    .map((item) => normalizeHistoryRow(item))
    .filter(Boolean) as HistoryRecordLike[];
}

function syncLearningHistoryToBackend(records: HistoryRecordLike[]) {
  if (!Array.isArray(records)) return;
  void fetch(`${API_BASE}/history-records`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ records })
  }).catch(() => {
    // ignore sync errors; local state remains source of truth during migration.
  });
}

function clearPayloadFromWindowName() {
  if (String(window.name || '').startsWith(PAYLOAD_PREFIX)) {
    window.name = '';
  }
}

function redirectToCanonicalHost(): boolean {
  const nextUrl = new URL(window.location.href);
  nextUrl.hostname = CANONICAL_HOST;
  if (nextUrl.toString() === window.location.href) return false;

  window.location.replace(nextUrl.toString());
  return true;
}

export function bootstrapOriginStateBridge(): boolean {
  if (typeof window === 'undefined' || typeof localStorage === 'undefined') return false;
  if (bridgeBootstrapped) return false;
  bridgeBootstrapped = true;

  try {
    if (window.location.hostname === SOURCE_HOST) {
      try {
        window.name = buildStorableWindowNamePayload();
      } catch {
      }

      const redirected = redirectToCanonicalHost();
      if (redirected) return true;
    }

    if (window.location.hostname === CANONICAL_HOST) {
      const payload = readPayloadFromWindowName();
      if (payload && payload.sourceHost === SOURCE_HOST) {
        applyIncomingStorage(payload.storage);
        const history = readLearningHistoryFromStorage();
        syncLearningHistoryToBackend(history);
        clearPayloadFromWindowName();
      }
    }
  } catch {
  }

  return false;
}
