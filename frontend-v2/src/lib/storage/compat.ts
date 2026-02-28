import type {
  HistoryRecord,
  SubtitleJobOptions,
  SubtitleJobOptionsPayload,
  SubtitleJobResult,
  SubtitleTaskMeta
} from '../../types/backend';
import {
  DEFAULT_LLM_BASE_URL,
  DEFAULT_LLM_MODEL,
  DEFAULT_SOURCE_LANGUAGE,
  DEFAULT_TARGET_LANGUAGE,
  DEFAULT_WHISPER_BASE_URL,
  DEFAULT_WHISPER_LANGUAGE,
  DEFAULT_WHISPER_MODEL,
  isValidLlmBaseUrl,
  isValidWhisperBaseUrl,
  normalizeLlmBaseUrl,
  normalizeWhisperBaseUrl,
} from '../api/provider-presets';
import { clamp, toNumber } from '../utils';

const DB_NAME = 'ListeningPracticeDB';
const DB_VERSION = 6;
const FILES_STORE = 'files';
const TRANSLATIONS_STORE = 'translations';
const CURRENT_SUBTITLES_CACHE_ID = 'current_subtitles';
const REMOVED_VOCAB_ITEMS_STORE = 'vocab_items';
const REMOVED_VOCAB_CACHE_STORE = 'vocab_cache';
const REMOVED_MODULE_PURGE_MARK_KEY = '__v2_removed_modules_purged_2026_02_23';

const REMOVED_STORAGE_PREFIXES = ['reading', 'vocab', 'supabase', 'anki'];
const REMOVED_STORAGE_EXACT_KEYS = [
  'totalReadingStudySeconds',
  'totalReadingWords',
  'totalVocabStudySeconds',
  'totalVocabWords',
  'supabaseUrl',
  'supabaseKey',
  'supabaseUserId',
  'ankiConnectUrl',
  'ankiDeck',
  'ankiNoteType'
] as const;

type JsonObject = Record<string, unknown>;

type LegacyFileRecord = {
  id: string;
  blob: Blob;
  metadata?: {
    name?: string;
    fileName?: string;
    type?: string;
    fileType?: string;
    size?: number;
  };
  timestamp?: number;
};

export type PracticeStatus = 'IDLE' | 'PLAYING' | 'WAITING_INPUT' | 'NEAR_MATCH' | 'AUTO_NEXT';
export type ListeningLetterFeedbackMode = 'sentence' | 'word';
export type ListeningShortcutAction = 'progress' | 'replay' | 'reveal' | 'immersive';

export interface ListeningShortcutBinding {
  code: string;
  key: string;
  shiftKey: boolean;
  ctrlKey: boolean;
  altKey: boolean;
  metaKey: boolean;
}

export type ListeningShortcutBindings = Record<ListeningShortcutAction, ListeningShortcutBinding>;

const DEFAULT_LISTENING_SHORTCUT_BINDINGS: ListeningShortcutBindings = {
  progress: {
    code: 'Space',
    key: ' ',
    shiftKey: false,
    ctrlKey: false,
    altKey: false,
    metaKey: false
  },
  replay: {
    code: 'KeyR',
    key: 'r',
    shiftKey: true,
    ctrlKey: false,
    altKey: false,
    metaKey: false
  },
  reveal: {
    code: 'KeyF',
    key: 'f',
    shiftKey: true,
    ctrlKey: false,
    altKey: false,
    metaKey: false
  },
  immersive: {
    code: 'KeyI',
    key: 'i',
    shiftKey: true,
    ctrlKey: false,
    altKey: false,
    metaKey: false
  }
};

export function getDefaultListeningShortcutBindings(): ListeningShortcutBindings {
  return {
    progress: { ...DEFAULT_LISTENING_SHORTCUT_BINDINGS.progress },
    replay: { ...DEFAULT_LISTENING_SHORTCUT_BINDINGS.replay },
    reveal: { ...DEFAULT_LISTENING_SHORTCUT_BINDINGS.reveal },
    immersive: { ...DEFAULT_LISTENING_SHORTCUT_BINDINGS.immersive }
  };
}

export interface LegacyCurrentSession {
  sessionId: string;
  currentIndex: number;
  totalSentences: number;
  timestamp: number;
}

export interface LegacyPracticeProgress {
  currentIndex: number;
  totalAttempts: number;
  correctAttempts: number;
  videoFileName: string;
  srtFileName: string;
  timestamp: number;
  practiceStatus?: PracticeStatus;
}

export interface ParsedSubtitleItem {
  id: number;
  start: number;
  end: number;
  text: string;
  translation: string;
  index: number;
  wordLineMap?: number[];
}

export const legacyStorageKeys = {
  activeModule: 'activeModule',
  subtitleSourceMode: 'subtitleSourceMode',
  learningHistory: 'learningHistory',
  learningHistoryRevision: 'learningHistoryRevision',
  dailyStats: 'dailyStats',
  moduleDailyStats: 'moduleDailyStats',
  totalStudySeconds: 'totalStudySeconds',
  totalCorrectWords: 'totalCorrectWords',
  totalSessions: 'totalSessions',
  totalListeningStudySeconds: 'totalListeningStudySeconds',
  totalListeningWords: 'totalListeningWords',
  totalReadingStudySeconds: 'totalReadingStudySeconds',
  totalReadingWords: 'totalReadingWords',
  totalVocabStudySeconds: 'totalVocabStudySeconds',
  totalVocabWords: 'totalVocabWords',
  dailyGoalMinutes: 'dailyGoalMinutes',
  dailyGoalSentences: 'dailyGoalSentences',
  longGoalMinutes: 'longGoalMinutes',
  longGoalSentences: 'longGoalSentences',
  dashboardRangeDays: 'dashboardRangeDays',
  currentSession: 'currentSession',
  listeningPracticeProgress: 'listeningPracticeProgress',
  listeningIndependentThreshold: 'listeningIndependentThreshold',
  listeningRevealGapThreshold: 'listeningRevealGapThreshold',
  listeningLetterFeedbackMode: 'listeningLetterFeedbackMode',
  listeningShortcutBindings: 'listeningShortcutBindings',
  autoLlmBaseUrl: 'autoLlmBaseUrl',
  autoLlmBaseUrlCustomList: 'autoLlmBaseUrlCustomList',
  geminiApiKey: 'geminiApiKey',
  geminiModel: 'geminiModel',
  autoLlmSupportJson: 'autoLlmSupportJson',
  autoEnableDemucs: 'autoEnableDemucs',
  autoEnableDiarization: 'autoEnableDiarization',
  autoAsrFallbackEnabled: 'autoAsrFallbackEnabled',
  autoSourceLanguage: 'autoSourceLanguage',
  autoTargetLanguage: 'autoTargetLanguage',
  autoWhisperRuntime: 'autoWhisperRuntime',
  autoWhisperQuality: 'autoWhisperQuality',
  autoWhisperModel: 'autoWhisperModel',
  autoWhisperLanguage: 'autoWhisperLanguage',
  autoWhisperApiKey: 'autoWhisperApiKey',
  autoWhisperBaseUrl: 'autoWhisperBaseUrl',
  autoWhisperBaseUrlCustomList: 'autoWhisperBaseUrlCustomList',
  autoWhisperMemoryByUrl: 'autoWhisperMemoryByUrl',
  autoSubtitleAdvancedOpen: 'autoSubtitleAdvancedOpen'
} as const;

export type SubtitleOptionForm = {
  enableDemucs: boolean;
  enableDiarization: boolean;
  asrFallbackEnabled: boolean;
  sourceLanguage: string;
  targetLanguage: string;
  whisperRuntime: 'cloud' | 'local';
  asrProfile: 'fast' | 'balanced' | 'accurate';
  whisperModel: string;
  whisperLanguage: string;
  whisperBaseUrl: string;
  whisperApiKey: string;
  advancedOpen: boolean;
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function readStorageString(key: string, fallback = '') {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null || raw === undefined || raw === '') return fallback;
    return String(raw);
  } catch {
    return fallback;
  }
}

export function readStorageNumber(key: string, fallback = 0) {
  return toNumber(readStorageString(key, String(fallback)), fallback);
}

export function readStorageBool(key: string, fallback = false) {
  const raw = readStorageString(key, fallback ? 'true' : 'false').toLowerCase().trim();
  if (raw === 'true') return true;
  if (raw === 'false') return false;
  return fallback;
}

export function readStorageJson<T>(key: string, fallback: T): T {
  const raw = readStorageString(key, '');
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function writeStorage(key: string, value: string) {
  localStorage.setItem(key, value);
}

export function writeStorageJson(key: string, value: unknown) {
  localStorage.setItem(key, JSON.stringify(value));
}

function normalizeCustomLlmBaseUrl(value: unknown) {
  return normalizeLlmBaseUrl(String(value || ''));
}

function isValidCustomLlmBaseUrl(value: string) {
  return isValidLlmBaseUrl(value);
}

function normalizeCustomLlmBaseUrlList(values: unknown[]) {
  const seen = new Set<string>();
  const normalized: string[] = [];
  values.forEach((item) => {
    const url = normalizeCustomLlmBaseUrl(item);
    if (!url || !isValidCustomLlmBaseUrl(url)) return;
    const key = url.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    normalized.push(url);
  });
  return normalized;
}

export function getCustomLlmBaseUrls() {
  const raw = readStorageJson<unknown[]>(legacyStorageKeys.autoLlmBaseUrlCustomList, []);
  if (!Array.isArray(raw)) return [];
  return normalizeCustomLlmBaseUrlList(raw);
}

export function setCustomLlmBaseUrls(values: string[]) {
  const normalized = normalizeCustomLlmBaseUrlList(Array.isArray(values) ? values : []);
  writeStorageJson(legacyStorageKeys.autoLlmBaseUrlCustomList, normalized);
  return normalized;
}

function normalizeCustomWhisperBaseUrl(value: unknown) {
  return normalizeWhisperBaseUrl(String(value || ''));
}

function isValidCustomWhisperBaseUrl(value: string) {
  return isValidWhisperBaseUrl(value);
}

function normalizeCustomWhisperBaseUrlList(values: unknown[]) {
  const seen = new Set<string>();
  const normalized: string[] = [];
  values.forEach((item) => {
    const url = normalizeCustomWhisperBaseUrl(item);
    if (!url || !isValidCustomWhisperBaseUrl(url)) return;
    const key = url.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    normalized.push(url);
  });
  return normalized;
}

export function getCustomWhisperBaseUrls() {
  const raw = readStorageJson<unknown[]>(legacyStorageKeys.autoWhisperBaseUrlCustomList, []);
  if (!Array.isArray(raw)) return [];
  return normalizeCustomWhisperBaseUrlList(raw);
}

export function setCustomWhisperBaseUrls(values: string[]) {
  const normalized = normalizeCustomWhisperBaseUrlList(Array.isArray(values) ? values : []);
  writeStorageJson(legacyStorageKeys.autoWhisperBaseUrlCustomList, normalized);
  return normalized;
}

export type WhisperMemoryBucket = {
  lastModel: string;
  lastApiKey: string;
  modelApiKeys: Record<string, string>;
  updatedAt: number;
};

type WhisperMemoryStore = Record<string, WhisperMemoryBucket>;

const normalizeWhisperMemoryRuntime = (value: string) => (
  String(value || '').trim().toLowerCase() === 'local' ? 'local' : 'cloud'
);

const normalizeWhisperMemoryUrl = (value: string) => normalizeWhisperBaseUrl(String(value || ''));

const normalizeWhisperMemoryModel = (value: string) => String(value || '').trim();

const normalizeWhisperMemoryApiKey = (value: string) => String(value || '').trim();

const getWhisperMemoryModelKey = (model: string) => normalizeWhisperMemoryModel(model).toLowerCase();

const buildWhisperMemoryBucketKey = (runtime: string, baseUrl: string) => {
  const safeUrl = normalizeWhisperMemoryUrl(baseUrl);
  if (!safeUrl) return '';
  const safeRuntime = normalizeWhisperMemoryRuntime(runtime);
  return `${safeRuntime}\u0000${safeUrl.toLowerCase()}`;
};

const normalizeWhisperMemoryBucket = (raw: unknown): WhisperMemoryBucket | null => {
  if (!isPlainObject(raw)) return null;

  const modelApiKeysRaw = isPlainObject(raw.modelApiKeys) ? raw.modelApiKeys : {};
  const modelApiKeys: Record<string, string> = {};
  Object.entries(modelApiKeysRaw).forEach(([key, value]) => {
    const modelKey = String(key || '').trim().toLowerCase();
    const apiKey = normalizeWhisperMemoryApiKey(String(value || ''));
    if (!modelKey || !apiKey) return;
    modelApiKeys[modelKey] = apiKey;
  });

  const lastModel = normalizeWhisperMemoryModel(String(raw.lastModel || ''));
  let lastApiKey = normalizeWhisperMemoryApiKey(String(raw.lastApiKey || ''));
  if (!lastApiKey && lastModel) {
    lastApiKey = normalizeWhisperMemoryApiKey(modelApiKeys[getWhisperMemoryModelKey(lastModel)] || '');
  }
  const updatedAt = Math.max(0, toNumber(raw.updatedAt, Date.now()));
  if (!lastModel && !lastApiKey && Object.keys(modelApiKeys).length === 0) {
    return null;
  }

  return {
    lastModel,
    lastApiKey,
    modelApiKeys,
    updatedAt
  };
};

const normalizeWhisperMemoryStore = (raw: unknown): WhisperMemoryStore => {
  if (!isPlainObject(raw)) return {};
  const normalized: WhisperMemoryStore = {};
  Object.entries(raw).forEach(([bucketKey, bucketRaw]) => {
    const safeBucketKey = String(bucketKey || '').trim();
    if (!safeBucketKey) return;
    const bucket = normalizeWhisperMemoryBucket(bucketRaw);
    if (!bucket) return;
    normalized[safeBucketKey] = bucket;
  });
  return normalized;
};

function readWhisperMemoryStore(): WhisperMemoryStore {
  const raw = readStorageJson<unknown>(legacyStorageKeys.autoWhisperMemoryByUrl, {});
  return normalizeWhisperMemoryStore(raw);
}

function writeWhisperMemoryStore(store: WhisperMemoryStore) {
  writeStorageJson(legacyStorageKeys.autoWhisperMemoryByUrl, store);
}

function cloneWhisperMemoryBucket(bucket: WhisperMemoryBucket): WhisperMemoryBucket {
  return {
    lastModel: bucket.lastModel,
    lastApiKey: bucket.lastApiKey,
    modelApiKeys: { ...(bucket.modelApiKeys || {}) },
    updatedAt: bucket.updatedAt
  };
}

export function getWhisperMemoryByUrl(runtime: string, baseUrl: string): WhisperMemoryBucket | null {
  const bucketKey = buildWhisperMemoryBucketKey(runtime, baseUrl);
  if (!bucketKey) return null;
  const store = readWhisperMemoryStore();
  const bucket = store[bucketKey];
  if (!bucket) return null;
  return cloneWhisperMemoryBucket(bucket);
}

export function getWhisperMemoryApiKey(runtime: string, baseUrl: string, model: string): string {
  const bucket = getWhisperMemoryByUrl(runtime, baseUrl);
  if (!bucket) return '';
  return normalizeWhisperMemoryApiKey(bucket.modelApiKeys[getWhisperMemoryModelKey(model)] || '');
}

export function setWhisperMemoryLastModel(runtime: string, baseUrl: string, model: string): WhisperMemoryBucket | null {
  const bucketKey = buildWhisperMemoryBucketKey(runtime, baseUrl);
  if (!bucketKey) return null;
  const safeModel = normalizeWhisperMemoryModel(model);
  if (!safeModel) return getWhisperMemoryByUrl(runtime, baseUrl);

  const store = readWhisperMemoryStore();
  const current = store[bucketKey] ? cloneWhisperMemoryBucket(store[bucketKey]) : {
    lastModel: '',
    lastApiKey: '',
    modelApiKeys: {},
    updatedAt: Date.now()
  };

  current.lastModel = safeModel;
  current.lastApiKey = normalizeWhisperMemoryApiKey(current.modelApiKeys[getWhisperMemoryModelKey(safeModel)] || '');
  current.updatedAt = Date.now();
  store[bucketKey] = current;
  writeWhisperMemoryStore(store);
  return cloneWhisperMemoryBucket(current);
}

export function setWhisperMemoryApiKey(runtime: string, baseUrl: string, model: string, apiKey: string): WhisperMemoryBucket | null {
  const bucketKey = buildWhisperMemoryBucketKey(runtime, baseUrl);
  if (!bucketKey) return null;
  const safeModel = normalizeWhisperMemoryModel(model);
  if (!safeModel) return getWhisperMemoryByUrl(runtime, baseUrl);

  const safeApiKey = normalizeWhisperMemoryApiKey(apiKey);
  const modelKey = getWhisperMemoryModelKey(safeModel);
  const store = readWhisperMemoryStore();
  const current = store[bucketKey] ? cloneWhisperMemoryBucket(store[bucketKey]) : {
    lastModel: '',
    lastApiKey: '',
    modelApiKeys: {},
    updatedAt: Date.now()
  };

  if (safeApiKey) {
    current.modelApiKeys[modelKey] = safeApiKey;
  } else {
    delete current.modelApiKeys[modelKey];
  }

  current.lastModel = safeModel;
  current.lastApiKey = normalizeWhisperMemoryApiKey(current.modelApiKeys[modelKey] || '');
  current.updatedAt = Date.now();

  if (!current.lastApiKey && Object.keys(current.modelApiKeys).length === 0) {
    delete store[bucketKey];
    writeWhisperMemoryStore(store);
    return null;
  }

  store[bucketKey] = current;
  writeWhisperMemoryStore(store);
  return cloneWhisperMemoryBucket(current);
}

export function secondsToDurationLabel(seconds: number) {
  const safe = Math.max(0, Math.floor(toNumber(seconds, 0)));
  const hour = Math.floor(safe / 3600);
  const minute = Math.floor((safe % 3600) / 60);
  const second = safe % 60;
  if (hour > 0) return `${hour}h ${minute}m`;
  if (minute > 0) return `${minute}m ${second}s`;
  return `${second}s`;
}

export function getLearningHistory() {
  return readStorageJson<HistoryRecord[]>(legacyStorageKeys.learningHistory, []);
}

const MAX_LEARNING_HISTORY_ROWS = 200;

const sanitizeHistoryName = (value: unknown) => String(value || '').trim();

const resolveDisplayName = (videoName: string) => {
  const safe = sanitizeHistoryName(videoName);
  if (!safe) return '';
  const withoutExt = safe.replace(/\.[^.]+$/, '').trim();
  return withoutExt || safe;
};

const buildHistoryKey = (videoName: string, srtName: string) => `${videoName}\u0000${srtName}`;

function normalizeHistorySubtitleTaskMeta(raw: unknown): SubtitleTaskMeta | null {
  if (!isPlainObject(raw)) return null;
  const pendingStateRaw = String(raw.pending_state || 'none').trim().toLowerCase();
  const pendingState = pendingStateRaw === 'failed' || pendingStateRaw === 'cancelled' ? pendingStateRaw : 'none';
  const lastJobId = String(raw.last_job_id || '').trim();
  if (!lastJobId) return null;
  const lastJobStatusRaw = String(raw.last_job_status || 'queued').trim().toLowerCase();
  const lastJobStatus = (
    lastJobStatusRaw === 'queued'
    || lastJobStatusRaw === 'running'
    || lastJobStatusRaw === 'completed'
    || lastJobStatusRaw === 'failed'
    || lastJobStatusRaw === 'cancelled'
  ) ? lastJobStatusRaw : 'queued';
  const sourceModeRaw = String(raw.source_mode || 'file').trim().toLowerCase();
  const sourceMode = sourceModeRaw === 'url' || sourceModeRaw === 'resume' ? sourceModeRaw : 'file';
  const updatedAt = Math.max(0, toNumber(raw.updated_at, Date.now()));
  return {
    pending_state: pendingState as SubtitleTaskMeta['pending_state'],
    last_job_id: lastJobId,
    last_job_status: lastJobStatus as SubtitleTaskMeta['last_job_status'],
    last_stage: String(raw.last_stage || '').trim(),
    last_message: String(raw.last_message || '').trim(),
    has_partial_result: Boolean(raw.has_partial_result),
    source_mode: sourceMode as SubtitleTaskMeta['source_mode'],
    updated_at: updatedAt,
  };
}

function normalizeHistoryRecordItem(raw: Partial<HistoryRecord>): HistoryRecord | null {
  const videoName = sanitizeHistoryName(raw.videoName);
  const srtName = sanitizeHistoryName(raw.srtName);
  if (!videoName || !srtName) return null;

  const totalSentences = Math.max(1, toNumber(raw.totalSentences, 1));
  const currentIndex = clamp(
    Math.max(0, toNumber(raw.currentIndex, 0)),
    0,
    Math.max(0, totalSentences - 1)
  );
  const timestamp = Math.max(0, toNumber(raw.timestamp, Date.now()));
  const thumbnail = sanitizeHistoryName(raw.thumbnail);
  const completed = Boolean(raw.completed);
  const historyId = sanitizeHistoryName(raw.historyId) || `${videoName}_${srtName}`;
  const displayName = sanitizeHistoryName(raw.displayName) || resolveDisplayName(videoName);
  const folderId = sanitizeHistoryName(raw.folderId);
  const subtitleTaskMeta = normalizeHistorySubtitleTaskMeta(raw.subtitleTaskMeta);

  return {
    videoName,
    srtName,
    currentIndex,
    totalSentences,
    thumbnail,
    timestamp,
    completed,
    historyId,
    displayName,
    folderId,
    subtitleTaskMeta
  };
}

function normalizeLearningHistoryRecords(records: HistoryRecord[]) {
  const deduped = new Map<string, HistoryRecord>();

  (records || []).forEach((item) => {
    const normalized = normalizeHistoryRecordItem(item);
    if (!normalized) return;

    const key = buildHistoryKey(normalized.videoName, normalized.srtName);
    const existing = deduped.get(key);
    if (!existing) {
      deduped.set(key, normalized);
      return;
    }

    const newer = normalized.timestamp >= existing.timestamp ? normalized : existing;
    const older = newer === normalized ? existing : normalized;
    deduped.set(key, {
      ...newer,
      thumbnail: sanitizeHistoryName(newer.thumbnail) || sanitizeHistoryName(older.thumbnail),
      displayName: sanitizeHistoryName(newer.displayName) || sanitizeHistoryName(older.displayName) || resolveDisplayName(newer.videoName),
      historyId: sanitizeHistoryName(newer.historyId) || sanitizeHistoryName(older.historyId) || `${newer.videoName}_${newer.srtName}`,
      subtitleTaskMeta: normalizeHistorySubtitleTaskMeta(newer.subtitleTaskMeta)
        || normalizeHistorySubtitleTaskMeta(older.subtitleTaskMeta)
    });
  });

  return Array.from(deduped.values())
    .sort((left, right) => toNumber(right?.timestamp, 0) - toNumber(left?.timestamp, 0))
    .slice(0, MAX_LEARNING_HISTORY_ROWS);
}

export function saveLearningHistory(records: HistoryRecord[]) {
  const normalized = normalizeLearningHistoryRecords(records);
  writeStorageJson(legacyStorageKeys.learningHistory, normalized);
  const prevRevision = Math.max(0, readStorageNumber(legacyStorageKeys.learningHistoryRevision, 0));
  const nowRevision = Date.now();
  const nextRevision = nowRevision > prevRevision ? nowRevision : prevRevision + 1;
  writeStorage(legacyStorageKeys.learningHistoryRevision, String(nextRevision));
  return normalized;
}

export function renameLearningHistoryRecord(videoName: string, srtName: string, nextDisplayName: string) {
  const safeVideo = sanitizeHistoryName(videoName);
  const safeSrt = sanitizeHistoryName(srtName);
  if (!safeVideo || !safeSrt) return getLearningHistory();

  const displayName = sanitizeHistoryName(nextDisplayName) || resolveDisplayName(safeVideo);
  const next = getLearningHistory().map((item) => {
    if (sanitizeHistoryName(item?.videoName) !== safeVideo || sanitizeHistoryName(item?.srtName) !== safeSrt) {
      return item;
    }
    return {
      ...item,
      displayName,
      timestamp: Date.now()
    };
  });
  return saveLearningHistory(next);
}

export function removeLearningHistoryRecord(videoName: string, srtName: string) {
  const safeVideo = sanitizeHistoryName(videoName);
  const safeSrt = sanitizeHistoryName(srtName);
  if (!safeVideo || !safeSrt) return getLearningHistory();

  const next = getLearningHistory().filter((item) => {
    const itemVideo = sanitizeHistoryName(item?.videoName);
    const itemSrt = sanitizeHistoryName(item?.srtName);
    return itemVideo !== safeVideo || itemSrt !== safeSrt;
  });
  return saveLearningHistory(next);
}

function upsertLearningHistoryRecord(payload: {
  videoName: string;
  srtName: string;
  currentIndex: number;
  totalSentences: number;
  thumbnail?: string;
  timestamp?: number;
  completed?: boolean;
  historyId?: string;
  displayName?: string;
  folderId?: string;
  practiceStatus?: PracticeStatus;
}) {
  const videoName = sanitizeHistoryName(payload.videoName);
  const srtName = sanitizeHistoryName(payload.srtName);
  if (!videoName || !srtName) return;

  const totalSentences = Math.max(1, toNumber(payload.totalSentences, 1));
  const currentIndex = clamp(
    Math.max(0, toNumber(payload.currentIndex, 0)),
    0,
    Math.max(0, totalSentences - 1)
  );
  const history = getLearningHistory();
  const existing = history.find((item) => (
    sanitizeHistoryName(item?.videoName) === videoName
    && sanitizeHistoryName(item?.srtName) === srtName
  )) || null;
  const timestamp = Math.max(0, toNumber(payload.timestamp, Date.now()));
  const practiceStatus = payload.practiceStatus || 'IDLE';
  const completed = Boolean(
    payload.completed
    ?? (practiceStatus === 'IDLE' && currentIndex >= totalSentences - 1)
  );
  const historyId = sanitizeHistoryName(payload.historyId) || sanitizeHistoryName(existing?.historyId) || `${videoName}_${srtName}`;
  const displayName = sanitizeHistoryName(payload.displayName) || sanitizeHistoryName(existing?.displayName) || resolveDisplayName(videoName);
  const folderId = sanitizeHistoryName(payload.folderId) || sanitizeHistoryName(existing?.folderId);
  const thumbnail = sanitizeHistoryName(payload.thumbnail) || sanitizeHistoryName(existing?.thumbnail);
  const subtitleTaskMeta = normalizeHistorySubtitleTaskMeta(existing?.subtitleTaskMeta);

  const nextRecord: HistoryRecord = {
    videoName,
    srtName,
    currentIndex,
    totalSentences,
    thumbnail,
    timestamp,
    completed,
    historyId,
    displayName,
    folderId,
    subtitleTaskMeta
  };

  const deduped = history.filter((item) => {
    const itemVideo = sanitizeHistoryName(item?.videoName || '');
    const itemSrt = sanitizeHistoryName(item?.srtName || '');
    return itemVideo !== videoName || itemSrt !== srtName;
  });
  saveLearningHistory([nextRecord, ...deduped]);
}

export function getDailyStats() {
  return readStorageJson<Record<string, JsonObject>>(legacyStorageKeys.dailyStats, {});
}

export function getModuleDailyStats() {
  return readStorageJson<Record<string, Record<string, JsonObject>>>(legacyStorageKeys.moduleDailyStats, {});
}

export function setActiveModule(moduleName: string) {
  writeStorage(legacyStorageKeys.activeModule, String(moduleName || 'listening'));
}

export function getDashboardRangeDays() {
  const raw = readStorageNumber(legacyStorageKeys.dashboardRangeDays, 7);
  if (raw === 30 || raw === 90) return raw;
  return 7;
}

export function setDashboardRangeDays(days: number) {
  const safe = days === 30 || days === 90 ? days : 7;
  writeStorage(legacyStorageKeys.dashboardRangeDays, String(safe));
}

export function getListeningIndependentThreshold() {
  const raw = readStorageNumber(legacyStorageKeys.listeningIndependentThreshold, 70);
  return clamp(Math.round(raw), 0, 100);
}

export function setListeningIndependentThreshold(value: number) {
  const safe = clamp(Math.round(toNumber(value, 70)), 0, 100);
  writeStorage(legacyStorageKeys.listeningIndependentThreshold, String(safe));
}

export function getListeningRevealGapThreshold() {
  const raw = readStorageNumber(legacyStorageKeys.listeningRevealGapThreshold, 20);
  return clamp(Math.round(raw), 0, 100);
}

export function setListeningRevealGapThreshold(value: number) {
  const safe = clamp(Math.round(toNumber(value, 20)), 0, 100);
  writeStorage(legacyStorageKeys.listeningRevealGapThreshold, String(safe));
}

export function getListeningLetterFeedbackMode(): ListeningLetterFeedbackMode {
  const raw = readStorageString(legacyStorageKeys.listeningLetterFeedbackMode, 'sentence').trim().toLowerCase();
  return raw === 'word' ? 'word' : 'sentence';
}

export function setListeningLetterFeedbackMode(mode: ListeningLetterFeedbackMode) {
  writeStorage(legacyStorageKeys.listeningLetterFeedbackMode, mode === 'word' ? 'word' : 'sentence');
}

function normalizeShortcutBinding(
  value: unknown,
  fallback: ListeningShortcutBinding
): ListeningShortcutBinding {
  if (!isPlainObject(value)) return { ...fallback };
  const code = typeof value.code === 'string' && value.code.trim() ? value.code.trim() : fallback.code;
  const key = typeof value.key === 'string' && value.key.trim() ? value.key.trim() : fallback.key;
  return {
    code,
    key,
    shiftKey: Boolean(value.shiftKey),
    ctrlKey: Boolean(value.ctrlKey),
    altKey: Boolean(value.altKey),
    metaKey: Boolean(value.metaKey)
  };
}

export function getListeningShortcutBindings(): ListeningShortcutBindings {
  const raw = readStorageJson<Record<string, unknown>>(legacyStorageKeys.listeningShortcutBindings, {});
  const defaults = getDefaultListeningShortcutBindings();
  return {
    progress: normalizeShortcutBinding(raw.progress, defaults.progress),
    replay: normalizeShortcutBinding(raw.replay, defaults.replay),
    reveal: normalizeShortcutBinding(raw.reveal, defaults.reveal),
    immersive: normalizeShortcutBinding(raw.immersive, defaults.immersive)
  };
}

export function setListeningShortcutBindings(value: ListeningShortcutBindings) {
  const defaults = getDefaultListeningShortcutBindings();
  writeStorageJson(legacyStorageKeys.listeningShortcutBindings, {
    progress: normalizeShortcutBinding(value.progress, defaults.progress),
    replay: normalizeShortcutBinding(value.replay, defaults.replay),
    reveal: normalizeShortcutBinding(value.reveal, defaults.reveal),
    immersive: normalizeShortcutBinding(value.immersive, defaults.immersive)
  });
}

export function getLegacyPracticeProgress() {
  return readStorageJson<LegacyPracticeProgress>(legacyStorageKeys.listeningPracticeProgress, {
    currentIndex: 0,
    totalAttempts: 0,
    correctAttempts: 0,
    videoFileName: '',
    srtFileName: '',
    timestamp: 0,
    practiceStatus: 'IDLE'
  });
}

export function persistLegacyPracticeProgress(
  progress: Partial<LegacyPracticeProgress> & {
    currentIndex: number;
    totalAttempts: number;
    correctAttempts: number;
    totalSentences: number;
  }
) {
  const prev = getLegacyPracticeProgress();
  const merged: LegacyPracticeProgress = {
    ...prev,
    ...progress,
    currentIndex: Math.max(0, toNumber(progress.currentIndex, prev.currentIndex)),
    totalAttempts: Math.max(0, toNumber(progress.totalAttempts, prev.totalAttempts)),
    correctAttempts: Math.max(0, toNumber(progress.correctAttempts, prev.correctAttempts)),
    timestamp: Date.now()
  };

  writeStorageJson(legacyStorageKeys.listeningPracticeProgress, merged);

  const session = readStorageJson<LegacyCurrentSession>(legacyStorageKeys.currentSession, {
    sessionId: `${merged.videoFileName || 'video'}_${merged.srtFileName || 'subtitle'}`,
    currentIndex: 0,
    totalSentences: Math.max(1, toNumber(progress.totalSentences, 1)),
    timestamp: 0
  });

  writeStorageJson(legacyStorageKeys.currentSession, {
    ...session,
    currentIndex: merged.currentIndex,
    totalSentences: Math.max(1, toNumber(progress.totalSentences, session.totalSentences || 1)),
    timestamp: Date.now()
  });

  const totalSentences = Math.max(1, toNumber(progress.totalSentences, session.totalSentences || 1));
  upsertLearningHistoryRecord({
    videoName: merged.videoFileName,
    srtName: merged.srtFileName,
    currentIndex: merged.currentIndex,
    totalSentences,
    timestamp: merged.timestamp,
    practiceStatus: merged.practiceStatus
  });
}

export function loadSubtitleOptionForm(): SubtitleOptionForm {
  return {
    enableDemucs: readStorageBool(legacyStorageKeys.autoEnableDemucs, false),
    enableDiarization: readStorageBool(legacyStorageKeys.autoEnableDiarization, false),
    asrFallbackEnabled: readStorageBool(legacyStorageKeys.autoAsrFallbackEnabled, true),
    sourceLanguage: readStorageString(legacyStorageKeys.autoSourceLanguage, DEFAULT_SOURCE_LANGUAGE),
    targetLanguage: readStorageString(legacyStorageKeys.autoTargetLanguage, DEFAULT_TARGET_LANGUAGE),
    whisperRuntime: readStorageString(legacyStorageKeys.autoWhisperRuntime, 'cloud') === 'local' ? 'local' : 'cloud',
    asrProfile: normalizeAsrProfile(readStorageString(legacyStorageKeys.autoWhisperQuality, 'balanced')),
    whisperModel: readStorageString(legacyStorageKeys.autoWhisperModel, DEFAULT_WHISPER_MODEL),
    whisperLanguage: readStorageString(legacyStorageKeys.autoWhisperLanguage, DEFAULT_WHISPER_LANGUAGE),
    whisperBaseUrl: readStorageString(legacyStorageKeys.autoWhisperBaseUrl, DEFAULT_WHISPER_BASE_URL),
    whisperApiKey: readStorageString(legacyStorageKeys.autoWhisperApiKey, ''),
    advancedOpen: readStorageBool(legacyStorageKeys.autoSubtitleAdvancedOpen, false)
  };
}

export function persistSubtitleOptionForm(form: SubtitleOptionForm) {
  writeStorage(legacyStorageKeys.autoEnableDemucs, String(Boolean(form.enableDemucs)));
  writeStorage(legacyStorageKeys.autoEnableDiarization, String(Boolean(form.enableDiarization)));
  writeStorage(legacyStorageKeys.autoAsrFallbackEnabled, String(Boolean(form.asrFallbackEnabled)));
  writeStorage(legacyStorageKeys.autoSourceLanguage, form.sourceLanguage.trim() || DEFAULT_SOURCE_LANGUAGE);
  writeStorage(legacyStorageKeys.autoTargetLanguage, form.targetLanguage.trim() || DEFAULT_TARGET_LANGUAGE);
  writeStorage(legacyStorageKeys.autoWhisperRuntime, form.whisperRuntime);
  writeStorage(legacyStorageKeys.autoWhisperQuality, normalizeAsrProfile(form.asrProfile));
  writeStorage(legacyStorageKeys.autoWhisperModel, form.whisperModel.trim() || DEFAULT_WHISPER_MODEL);
  writeStorage(legacyStorageKeys.autoWhisperLanguage, form.whisperLanguage.trim() || DEFAULT_WHISPER_LANGUAGE);
  writeStorage(legacyStorageKeys.autoWhisperBaseUrl, form.whisperBaseUrl.trim() || DEFAULT_WHISPER_BASE_URL);
  writeStorage(legacyStorageKeys.autoWhisperApiKey, form.whisperApiKey.trim());
  writeStorage(legacyStorageKeys.autoSubtitleAdvancedOpen, String(Boolean(form.advancedOpen)));
}

export function mapFormToSubtitleJobOptions(
  form: SubtitleOptionForm,
  llmOverride?: Partial<SubtitleJobOptions['llm']> | null
): SubtitleJobOptionsPayload {
  const profile = normalizeAsrProfile(form.asrProfile);
  const whisperRuntime = form.whisperRuntime === 'local' ? 'local' : 'cloud';
  const whisperBaseUrl = whisperRuntime === 'cloud'
    ? ''
    : (form.whisperBaseUrl.trim() || DEFAULT_WHISPER_BASE_URL);
  const whisperApiKey = whisperRuntime === 'cloud' ? '' : form.whisperApiKey.trim();
  const llmPayload = {
    base_url: String(llmOverride?.base_url || '').trim() || DEFAULT_LLM_BASE_URL,
    api_key: String(llmOverride?.api_key || '').trim(),
    model: String(llmOverride?.model || '').trim() || DEFAULT_LLM_MODEL,
    llm_support_json: Boolean(llmOverride?.llm_support_json)
  };
  return {
    asr_profile: profile,
    llm: llmPayload,
    whisper: {
      runtime: whisperRuntime,
      model: form.whisperModel.trim() || DEFAULT_WHISPER_MODEL,
      language: form.whisperLanguage.trim() || DEFAULT_WHISPER_LANGUAGE,
      base_url: whisperBaseUrl,
      api_key: whisperApiKey
    }
  };
}

function normalizeAsrProfile(value: string): 'fast' | 'balanced' | 'accurate' {
  const profile = String(value || '').trim().toLowerCase();
  if (profile === 'fast' || profile === 'accurate') return profile;
  return 'balanced';
}

function ensureLegacyStores(db: IDBDatabase) {
  if (db.objectStoreNames.contains(REMOVED_VOCAB_CACHE_STORE)) {
    db.deleteObjectStore(REMOVED_VOCAB_CACHE_STORE);
  }
  if (db.objectStoreNames.contains(REMOVED_VOCAB_ITEMS_STORE)) {
    db.deleteObjectStore(REMOVED_VOCAB_ITEMS_STORE);
  }
  if (!db.objectStoreNames.contains(FILES_STORE)) {
    db.createObjectStore(FILES_STORE, { keyPath: 'id' });
  }
  if (!db.objectStoreNames.contains(TRANSLATIONS_STORE)) {
    db.createObjectStore(TRANSLATIONS_STORE, { keyPath: 'key' });
  }
}

function hasStore(db: IDBDatabase, storeName: string) {
  return db.objectStoreNames.contains(storeName);
}

function openLegacyDb() {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onerror = () => reject(request.error || new Error('Failed to open indexedDB'));
    request.onupgradeneeded = () => {
      const db = request.result;
      ensureLegacyStores(db);
    };
    request.onsuccess = () => resolve(request.result);
  });
}

function getRequestResult<T>(request: IDBRequest<T>) {
  return new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('IndexedDB request failed'));
  });
}

function putFileToDb(db: IDBDatabase, id: string, file: File) {
  return new Promise<void>((resolve, reject) => {
    if (!hasStore(db, FILES_STORE)) {
      resolve();
      return;
    }

    const tx = db.transaction([FILES_STORE], 'readwrite');
    const store = tx.objectStore(FILES_STORE);
    store.put({
      id,
      blob: file,
      metadata: {
        name: file.name,
        fileName: file.name,
        type: file.type,
        fileType: file.type,
        size: file.size
      },
      timestamp: Date.now()
    });

    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error('Failed to write file cache'));
    tx.onabort = () => reject(tx.error || new Error('Failed to write file cache'));
  });
}

function deleteFileFromDb(db: IDBDatabase, id: string) {
  return new Promise<void>((resolve, reject) => {
    if (!hasStore(db, FILES_STORE)) {
      resolve();
      return;
    }

    const tx = db.transaction([FILES_STORE], 'readwrite');
    const store = tx.objectStore(FILES_STORE);
    store.delete(id);

    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error('Failed to delete file cache'));
    tx.onabort = () => reject(tx.error || new Error('Failed to delete file cache'));
  });
}

export async function clearCurrentPracticeCache() {
  const db = await openLegacyDb();
  try {
    await deleteFileFromDb(db, 'current_video');
    await deleteFileFromDb(db, 'current_srt');
    await deleteFileFromDb(db, CURRENT_SUBTITLES_CACHE_ID);
  } finally {
    db.close();
  }

  try {
    localStorage.removeItem(legacyStorageKeys.currentSession);
  } catch {
    // ignore
  }
  try {
    localStorage.removeItem(legacyStorageKeys.listeningPracticeProgress);
  } catch {
    // ignore
  }
}

export async function removeLearningHistoryRecordAndCache(videoName: string, srtName: string) {
  const safeVideo = sanitizeHistoryName(videoName);
  const safeSrt = sanitizeHistoryName(srtName);
  const nextRecords = removeLearningHistoryRecord(safeVideo, safeSrt);

  const progress = getLegacyPracticeProgress();
  const shouldClearCache = (
    sanitizeHistoryName(progress.videoFileName) === safeVideo
    && sanitizeHistoryName(progress.srtFileName) === safeSrt
  );
  if (shouldClearCache) {
    await clearCurrentPracticeCache();
  }

  return {
    records: nextRecords,
    cacheCleared: shouldClearCache
  };
}

export async function getLegacyFileRecord(id: string): Promise<LegacyFileRecord | null> {
  const db = await openLegacyDb();
  try {
    if (!hasStore(db, FILES_STORE)) return null;
    const tx = db.transaction([FILES_STORE], 'readonly');
    const store = tx.objectStore(FILES_STORE);
    const request = store.get(id) as IDBRequest<LegacyFileRecord | undefined>;
    const payload = await getRequestResult(request);
    return payload || null;
  } finally {
    db.close();
  }
}

export async function getLegacyFileAsFile(id: string, fallbackName = id) {
  const payload = await getLegacyFileRecord(id);
  if (!payload?.blob) return null;

  const name = payload.metadata?.name || payload.metadata?.fileName || fallbackName;
  const type = payload.metadata?.type || payload.metadata?.fileType || payload.blob.type || 'application/octet-stream';
  return new File([payload.blob], name, { type });
}

function parseSrtTimestamp(value: string) {
  const match = String(value || '').trim().match(/(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})/);
  if (!match) return 0;
  const hour = toNumber(match[1], 0);
  const minute = toNumber(match[2], 0);
  const second = toNumber(match[3], 0);
  const ms = toNumber(match[4], 0);
  return hour * 3600 + minute * 60 + second + ms / 1000;
}

const SRT_WORD_PATTERN = /[A-Za-z0-9']+/g;

function normalizeSubtitleText(value: string) {
  return String(value || '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\[[^\]]*\]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function parseSrtText(source: string): ParsedSubtitleItem[] {
  const normalized = String(source || '').replace(/\r/g, '').trim();
  if (!normalized) return [];

  const blocks = normalized.split(/\n{2,}/);
  const rows: ParsedSubtitleItem[] = [];

  blocks.forEach((block, index) => {
    const lines = block.split('\n').map((line) => line.trim()).filter(Boolean);
    if (lines.length < 2) return;

    const timeLineIndex = lines[0].includes('-->') ? 0 : 1;
    const timeLine = lines[timeLineIndex] || '';
    const [startRaw, endRaw] = timeLine.split('-->').map((item) => item.trim());
    if (!startRaw || !endRaw) return;

    const rawContentLines = lines.slice(timeLineIndex + 1);
    const normalizedLines = rawContentLines
      .map((line) => normalizeSubtitleText(line))
      .filter(Boolean);
    const text = normalizeSubtitleText(normalizedLines.join(' '));
    if (!text) return;

    const lineWordMap: number[] = [];
    normalizedLines.forEach((line, lineIndex) => {
      const words = line.match(SRT_WORD_PATTERN);
      if (!Array.isArray(words) || words.length === 0) return;
      const mark = lineIndex === 0 ? 1 : 2;
      words.forEach(() => lineWordMap.push(mark));
    });

    const textWords = text.match(SRT_WORD_PATTERN) || [];
    const normalizedWordLineMap = textWords.length > 0
      ? textWords.map((_, index) => lineWordMap[index] || 1)
      : undefined;

    rows.push({
      id: rows.length + 1,
      start: parseSrtTimestamp(startRaw),
      end: parseSrtTimestamp(endRaw),
      text,
      translation: '',
      index,
      wordLineMap: normalizedWordLineMap
    });
  });

  return rows;
}

function estimateSubtitleCount(sourceSrt: string) {
  const lines = String(sourceSrt || '').split(/\r?\n/);
  return lines.reduce((count, line) => (line.includes('-->') ? count + 1 : count), 0);
}

export function buildSrtText(result: SubtitleJobResult) {
  const sourceSrt = String(result?.source_srt || '').trim();
  if (sourceSrt) return sourceSrt;
  const bilingual = String(result?.bilingual_srt || '').trim();
  if (bilingual) return bilingual;
  const subtitles = Array.isArray(result?.subtitles) ? result.subtitles : [];
  return subtitles
    .map((item, index) => {
      const start = formatSrtTimestamp(item.start);
      const end = formatSrtTimestamp(item.end);
      return `${index + 1}\n${start} --> ${end}\n${item.text}\n`;
    })
    .join('\n');
}

function normalizePracticeSubtitleRows(subtitles: SubtitleJobResult['subtitles']): ParsedSubtitleItem[] {
  if (!Array.isArray(subtitles)) return [];
  const rows: ParsedSubtitleItem[] = [];

  subtitles.forEach((item, index) => {
    const text = normalizeSubtitleText(item?.text ?? '');
    if (!text) return;

    const start = Math.max(0, toNumber(item?.start, 0));
    const endRaw = toNumber(item?.end, start);
    const end = endRaw >= start ? endRaw : start;
    const normalizedIndex = Math.max(0, Math.floor(toNumber(item?.index, index)));
    const normalizedId = Math.max(1, Math.floor(toNumber(item?.id, rows.length + 1)));
    const translation = normalizeSubtitleText(item?.translation ?? '');

    rows.push({
      id: normalizedId,
      start,
      end,
      text,
      translation,
      index: normalizedIndex
    });
  });

  return rows;
}

function formatSrtTimestamp(seconds: number) {
  const safe = clamp(toNumber(seconds, 0), 0, 86399);
  const totalMs = Math.round(safe * 1000);
  const hour = Math.floor(totalMs / 3600000);
  const minute = Math.floor((totalMs % 3600000) / 60000);
  const second = Math.floor((totalMs % 60000) / 1000);
  const ms = totalMs % 1000;
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:${String(second).padStart(2, '0')},${String(ms).padStart(3, '0')}`;
}

function waitForVideoEvent(video: HTMLVideoElement, eventName: 'loadeddata' | 'loadedmetadata' | 'seeked', timeoutMs: number) {
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      cleanup();
      reject(new Error(`waitForVideoEvent timeout: ${eventName}`));
    }, Math.max(200, timeoutMs));

    const onEvent = () => {
      cleanup();
      resolve();
    };

    const cleanup = () => {
      window.clearTimeout(timer);
      video.removeEventListener(eventName, onEvent);
    };

    video.addEventListener(eventName, onEvent, { once: true });
  });
}

async function captureVideoFirstFrameThumbnail(videoFile: File): Promise<string> {
  if (typeof document === 'undefined' || typeof window === 'undefined') return '';
  if (!videoFile || !(videoFile.type || '').toLowerCase().startsWith('video/')) return '';

  const objectUrl = URL.createObjectURL(videoFile);
  const video = document.createElement('video');
  video.preload = 'metadata';
  video.muted = true;
  video.playsInline = true;
  video.src = objectUrl;

  try {
    try {
      await waitForVideoEvent(video, 'loadeddata', 4500);
    } catch {
      await waitForVideoEvent(video, 'loadedmetadata', 4500);
    }

    const duration = Number.isFinite(video.duration) ? Number(video.duration) : 0;
    if (duration > 0.08) {
      try {
        video.currentTime = Math.min(duration / 3, 0.1);
        await waitForVideoEvent(video, 'seeked', 2400);
      } catch {
        // ignore seek failures and keep current frame
      }
    }

    const videoWidth = Math.max(1, Number(video.videoWidth || 0));
    const videoHeight = Math.max(1, Number(video.videoHeight || 0));
    if (videoWidth <= 1 || videoHeight <= 1) return '';

    const targetWidth = Math.min(360, videoWidth);
    const targetHeight = Math.max(1, Math.round((targetWidth * videoHeight) / videoWidth));
    const canvas = document.createElement('canvas');
    canvas.width = targetWidth;
    canvas.height = targetHeight;

    const context = canvas.getContext('2d');
    if (!context) return '';
    context.drawImage(video, 0, 0, targetWidth, targetHeight);
    return canvas.toDataURL('image/jpeg', 0.76);
  } catch {
    return '';
  } finally {
    try {
      video.pause();
      video.removeAttribute('src');
      video.load();
    } catch {
      // ignore
    }
    URL.revokeObjectURL(objectUrl);
  }
}

export async function bridgeToLegacyPractice(params: {
  videoFile: File;
  result: SubtitleJobResult;
  currentIndex?: number;
  totalAttempts?: number;
  correctAttempts?: number;
}) {
  const { videoFile, result } = params;
  const now = Date.now();
  const normalizedSubtitles = normalizePracticeSubtitleRows(result.subtitles);
  const srtText = buildSrtText(result);
  const subtitleCount = Math.max(estimateSubtitleCount(srtText), normalizedSubtitles.length, 1);
  const currentIndex = clamp(toNumber(params.currentIndex, 0), 0, Math.max(0, subtitleCount - 1));
  const baseName = videoFile.name.replace(/\.[^.]+$/, '') || 'subtitles';
  const srtName = `${baseName}.srt`;
  const srtFile = new File([srtText], srtName, { type: 'application/x-subrip' });
  const subtitleJsonFile = new File(
    [JSON.stringify(normalizedSubtitles)],
    `${baseName}.subtitles.json`,
    { type: 'application/json' }
  );
  const thumbnail = await captureVideoFirstFrameThumbnail(videoFile);

  const db = await openLegacyDb();
  try {
    await putFileToDb(db, 'current_video', videoFile);
    await putFileToDb(db, 'current_srt', srtFile);
    await putFileToDb(db, CURRENT_SUBTITLES_CACHE_ID, subtitleJsonFile);
  } finally {
    db.close();
  }

  const sessionId = `${videoFile.name}_${srtFile.name}`;
  writeStorageJson(legacyStorageKeys.currentSession, {
    sessionId,
    currentIndex,
    totalSentences: subtitleCount,
    timestamp: now
  });

  writeStorageJson(legacyStorageKeys.listeningPracticeProgress, {
    currentIndex,
    totalAttempts: toNumber(params.totalAttempts, 0),
    correctAttempts: toNumber(params.correctAttempts, 0),
    videoFileName: videoFile.name,
    srtFileName: srtFile.name,
    practiceStatus: 'IDLE',
    timestamp: now
  });

  upsertLearningHistoryRecord({
    videoName: videoFile.name,
    srtName: srtFile.name,
    currentIndex,
    totalSentences: subtitleCount,
    thumbnail,
    timestamp: now,
    practiceStatus: 'IDLE'
  });

  setActiveModule('listening');

  return {
    srtName: srtFile.name,
    subtitleCount
  };
}

function collectRemovedStorageKeys() {
  const keys: string[] = [];

  try {
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (!key) continue;
      const lowered = key.toLowerCase();
      if (REMOVED_STORAGE_PREFIXES.some((prefix) => lowered.startsWith(prefix))) {
        keys.push(key);
      }
    }
  } catch {
    return [];
  }

  return keys;
}

function purgeRemovedLocalStorageData() {
  const keysToRemove = new Set<string>([...REMOVED_STORAGE_EXACT_KEYS, ...collectRemovedStorageKeys()]);

  keysToRemove.forEach((key) => {
    try {
      localStorage.removeItem(key);
    } catch {
      // ignore
    }
  });

  const moduleDailyStats = readStorageJson<Record<string, unknown>>(legacyStorageKeys.moduleDailyStats, {});
  if (isPlainObject(moduleDailyStats)) {
    let changed = false;

    if ('reading' in moduleDailyStats) {
      delete moduleDailyStats.reading;
      changed = true;
    }
    if ('vocab' in moduleDailyStats) {
      delete moduleDailyStats.vocab;
      changed = true;
    }

    Object.values(moduleDailyStats).forEach((row) => {
      if (!isPlainObject(row)) return;
      if ('reading' in row) {
        delete row.reading;
        changed = true;
      }
      if ('vocab' in row) {
        delete row.vocab;
        changed = true;
      }
    });

    if (changed) {
      writeStorageJson(legacyStorageKeys.moduleDailyStats, moduleDailyStats);
    }
  }

  const historyRows = readStorageJson<unknown[]>(legacyStorageKeys.learningHistory, []);
  if (Array.isArray(historyRows) && historyRows.length > 0) {
    const filtered = historyRows.filter((row) => {
      if (!isPlainObject(row)) return true;
      const moduleName = String(row.module || row.moduleId || row.moduleName || '').trim().toLowerCase();
      return moduleName !== 'home' && moduleName !== 'reading' && moduleName !== 'vocab';
    });

    if (filtered.length !== historyRows.length) {
      writeStorageJson(legacyStorageKeys.learningHistory, filtered);
      const prevRevision = Math.max(0, readStorageNumber(legacyStorageKeys.learningHistoryRevision, 0));
      const nowRevision = Date.now();
      const nextRevision = nowRevision > prevRevision ? nowRevision : prevRevision + 1;
      writeStorage(legacyStorageKeys.learningHistoryRevision, String(nextRevision));
    }
  }

  writeStorage(legacyStorageKeys.activeModule, 'listening');
}

async function runLegacyDbMigration() {
  try {
    const db = await openLegacyDb();
    db.close();
  } catch {
    // ignore
  }
}

export async function purgeRemovedModuleData() {
  if (readStorageString(REMOVED_MODULE_PURGE_MARK_KEY, '')) {
    const activeModule = readStorageString(legacyStorageKeys.activeModule, '');
    if (!activeModule || activeModule === 'home' || activeModule === 'reading' || activeModule === 'vocab') {
      writeStorage(legacyStorageKeys.activeModule, 'listening');
    }
    return;
  }

  purgeRemovedLocalStorageData();
  await runLegacyDbMigration();
  writeStorage(REMOVED_MODULE_PURGE_MARK_KEY, new Date().toISOString());
}
