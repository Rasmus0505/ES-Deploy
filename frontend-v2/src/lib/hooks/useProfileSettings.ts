import { useCallback, useEffect, useRef, useState } from 'react';
import {
  DEFAULT_LLM_BASE_URL,
  DEFAULT_LLM_MODEL
} from '../api/provider-presets';
import { fetchProfileSettings, updateProfileKeys, updateProfileSettings } from '../api/profile';
import type {
  LlmOptions,
  ProfileKeysUpdateRequest,
  ProfileSettings,
  ProfileSettingsUpdateRequest
} from '../../types/backend';

const ENGLISH_LEVELS = new Set(['junior', 'senior', 'cet4', 'cet6', 'kaoyan', 'toefl', 'sat']);
const LISTENING_TRANSLATION_MODE_STORAGE_KEY = 'profileListeningTranslationModeV1';
const LISTENING_TRANSLATION_MODEL_STORAGE_KEY = 'profileListeningTranslationModelV1';
const DEFAULT_TRANSLATION_MODEL_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1';
const DEFAULT_TRANSLATION_MODEL = 'qwen-mt-flash';

export type ListeningTranslationMode = 'llm_model' | 'translation_model';
export type ExtendedProfileSettings = {
  english_level: ProfileSettings['english_level'];
  english_level_numeric: number;
  english_level_cefr: string;
  llm_mode: ProfileSettings['llm_mode'];
  llm_unified: LlmOptions;
  llm_listening: LlmOptions;
  llm_reading: LlmOptions;
  llm_unified_has_api_key: boolean;
  llm_listening_has_api_key: boolean;
  llm_reading_has_api_key: boolean;
  llm_unified_api_key_masked: string;
  llm_listening_api_key_masked: string;
  llm_reading_api_key_masked: string;
  updated_at: number;
  listening_translation_mode: ListeningTranslationMode;
  listening_translation_model: LlmOptions;
};
type ProfileLike = {
  english_level?: ProfileSettings['english_level'] | string;
  english_level_numeric?: number;
  english_level_cefr?: string;
  llm_mode?: ProfileSettings['llm_mode'] | string;
  llm_unified?: Record<string, unknown> | Partial<LlmOptions> | null;
  llm_listening?: Record<string, unknown> | Partial<LlmOptions> | null;
  llm_reading?: Record<string, unknown> | Partial<LlmOptions> | null;
  updated_at?: number;
  listening_translation_mode?: ListeningTranslationMode | string;
  listening_translation_model?: Partial<LlmOptions> | null;
};

const DEFAULT_TRANSLATION_MODEL_OPTIONS: LlmOptions = {
  base_url: DEFAULT_TRANSLATION_MODEL_BASE_URL,
  api_key: '',
  model: DEFAULT_TRANSLATION_MODEL,
  llm_support_json: false
};

export const DEFAULT_PROFILE_SETTINGS: ExtendedProfileSettings = {
  english_level: 'cet4',
  english_level_numeric: 7.5,
  english_level_cefr: 'B1',
  llm_mode: 'unified',
  llm_unified: {
    base_url: DEFAULT_LLM_BASE_URL,
    api_key: '',
    model: DEFAULT_LLM_MODEL,
    llm_support_json: false
  },
  llm_listening: {
    base_url: DEFAULT_LLM_BASE_URL,
    api_key: '',
    model: DEFAULT_LLM_MODEL,
    llm_support_json: false
  },
  llm_reading: {
    base_url: DEFAULT_LLM_BASE_URL,
    api_key: '',
    model: DEFAULT_LLM_MODEL,
    llm_support_json: false
  },
  llm_unified_has_api_key: false,
  llm_listening_has_api_key: false,
  llm_reading_has_api_key: false,
  llm_unified_api_key_masked: '',
  llm_listening_api_key_masked: '',
  llm_reading_api_key_masked: '',
  listening_translation_mode: 'llm_model',
  listening_translation_model: DEFAULT_TRANSLATION_MODEL_OPTIONS,
  updated_at: 0
};

function readStorageString(key: string, fallback = '') {
  try {
    if (typeof window === 'undefined') return fallback;
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return String(raw);
  } catch {
    return fallback;
  }
}

function writeStorageString(key: string, value: string) {
  try {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(key, value);
  } catch {
    // ignore localStorage failures
  }
}

function normalizeLlmOptions(
  value: Partial<LlmOptions> | null | undefined,
  fallbackBaseUrl = DEFAULT_LLM_BASE_URL,
  fallbackModel = DEFAULT_LLM_MODEL
): LlmOptions {
  const safe = value || {};
  return {
    base_url: String(safe.base_url || '').trim() || fallbackBaseUrl,
    api_key: String(safe.api_key || '').trim(),
    model: String(safe.model || '').trim() || fallbackModel,
    llm_support_json: Boolean(safe.llm_support_json)
  };
}

function normalizeTranslationModelOptions(value: Partial<LlmOptions> | null | undefined): LlmOptions {
  return normalizeLlmOptions(value, DEFAULT_TRANSLATION_MODEL_BASE_URL, DEFAULT_TRANSLATION_MODEL);
}

function normalizePublicLlmMetadata(value: unknown): { has_api_key: boolean; api_key_masked: string } {
  if (!value || typeof value !== 'object') {
    return { has_api_key: false, api_key_masked: '' };
  }
  const safe = value as Record<string, unknown>;
  return {
    has_api_key: Boolean(safe.has_api_key),
    api_key_masked: String(safe.api_key_masked || '').trim()
  };
}

function toLlmUpdatePayload(value: Partial<LlmOptions> | null | undefined) {
  const safe = normalizeLlmOptions(value || {});
  return {
    base_url: safe.base_url,
    model: safe.model,
    llm_support_json: Boolean(safe.llm_support_json)
  };
}

function sanitizeProfileUpdateRequest(updates: ProfileSettingsUpdateRequest): ProfileSettingsUpdateRequest {
  const payload: ProfileSettingsUpdateRequest = {};
  if (updates.english_level) payload.english_level = updates.english_level;
  if (updates.llm_mode) payload.llm_mode = updates.llm_mode;
  if (updates.llm_unified) payload.llm_unified = toLlmUpdatePayload(updates.llm_unified as Partial<LlmOptions>);
  if (updates.llm_listening) payload.llm_listening = toLlmUpdatePayload(updates.llm_listening as Partial<LlmOptions>);
  if (updates.llm_reading) payload.llm_reading = toLlmUpdatePayload(updates.llm_reading as Partial<LlmOptions>);
  return payload;
}

function readListeningTranslationModeFromStorage(): ListeningTranslationMode {
  const raw = readStorageString(LISTENING_TRANSLATION_MODE_STORAGE_KEY, 'llm_model').trim().toLowerCase();
  return raw === 'translation_model' ? 'translation_model' : 'llm_model';
}

function readListeningTranslationModelFromStorage(): LlmOptions {
  const raw = readStorageString(LISTENING_TRANSLATION_MODEL_STORAGE_KEY, '');
  if (!raw) return DEFAULT_TRANSLATION_MODEL_OPTIONS;
  try {
    const parsed = JSON.parse(raw) as Partial<LlmOptions>;
    return normalizeTranslationModelOptions(parsed);
  } catch {
    return DEFAULT_TRANSLATION_MODEL_OPTIONS;
  }
}

export function saveListeningTranslationLocalSettings(settings: {
  mode: ListeningTranslationMode;
  model: Partial<LlmOptions> | LlmOptions;
}) {
  const safeMode: ListeningTranslationMode = settings.mode === 'translation_model' ? 'translation_model' : 'llm_model';
  const safeModel = normalizeTranslationModelOptions(settings.model);
  const persistedModel = { ...safeModel, api_key: '' };
  writeStorageString(LISTENING_TRANSLATION_MODE_STORAGE_KEY, safeMode);
  writeStorageString(LISTENING_TRANSLATION_MODEL_STORAGE_KEY, JSON.stringify(persistedModel));
  return {
    listening_translation_mode: safeMode,
    listening_translation_model: safeModel
  };
}

export function normalizeProfileSettings(value: ProfileLike | null | undefined): ExtendedProfileSettings {
  const safe = value || {};
  const englishLevel = String(safe.english_level || '').trim().toLowerCase();
  const llmMode = String(safe.llm_mode || '').trim().toLowerCase();
  const llmUnifiedRaw = safe.llm_unified && typeof safe.llm_unified === 'object'
    ? (safe.llm_unified as Record<string, unknown>)
    : {};
  const llmListeningRaw = safe.llm_listening && typeof safe.llm_listening === 'object'
    ? (safe.llm_listening as Record<string, unknown>)
    : {};
  const llmReadingRaw = safe.llm_reading && typeof safe.llm_reading === 'object'
    ? (safe.llm_reading as Record<string, unknown>)
    : {};
  const llmUnifiedMeta = normalizePublicLlmMetadata(safe.llm_unified);
  const llmListeningMeta = normalizePublicLlmMetadata(safe.llm_listening);
  const llmReadingMeta = normalizePublicLlmMetadata(safe.llm_reading);
  const localTranslationMode = readListeningTranslationModeFromStorage();
  const localTranslationModel = readListeningTranslationModelFromStorage();
  const rawTranslationMode = String(safe.listening_translation_mode || localTranslationMode || 'llm_model').trim().toLowerCase();
  const listeningTranslationMode: ListeningTranslationMode = rawTranslationMode === 'translation_model' ? 'translation_model' : 'llm_model';
  return {
    english_level: (ENGLISH_LEVELS.has(englishLevel) ? englishLevel : 'cet4') as ProfileSettings['english_level'],
    english_level_numeric: Number(safe.english_level_numeric || DEFAULT_PROFILE_SETTINGS.english_level_numeric),
    english_level_cefr: String(safe.english_level_cefr || '').trim() || DEFAULT_PROFILE_SETTINGS.english_level_cefr,
    llm_mode: (llmMode === 'custom' ? 'custom' : 'unified') as ProfileSettings['llm_mode'],
    llm_unified: normalizeLlmOptions({ ...llmUnifiedRaw, api_key: '' }),
    llm_listening: normalizeLlmOptions({ ...llmListeningRaw, api_key: '' }),
    llm_reading: normalizeLlmOptions({ ...llmReadingRaw, api_key: '' }),
    llm_unified_has_api_key: llmUnifiedMeta.has_api_key,
    llm_listening_has_api_key: llmListeningMeta.has_api_key,
    llm_reading_has_api_key: llmReadingMeta.has_api_key,
    llm_unified_api_key_masked: llmUnifiedMeta.api_key_masked,
    llm_listening_api_key_masked: llmListeningMeta.api_key_masked,
    llm_reading_api_key_masked: llmReadingMeta.api_key_masked,
    listening_translation_mode: listeningTranslationMode,
    listening_translation_model: normalizeTranslationModelOptions(
      safe.listening_translation_model || localTranslationModel || DEFAULT_TRANSLATION_MODEL_OPTIONS
    ),
    updated_at: Number(safe.updated_at || 0)
  };
}

export function selectLlmOptions(profile: Partial<ExtendedProfileSettings> | null | undefined, scene: 'listening' | 'reading'): LlmOptions {
  const safeProfile = normalizeProfileSettings(profile || DEFAULT_PROFILE_SETTINGS);
  if (scene === 'listening' && safeProfile.listening_translation_mode === 'translation_model') {
    return normalizeTranslationModelOptions(safeProfile.listening_translation_model);
  }
  if (safeProfile.llm_mode === 'custom') {
    return normalizeLlmOptions(scene === 'listening' ? safeProfile.llm_listening : safeProfile.llm_reading);
  }
  return normalizeLlmOptions(safeProfile.llm_unified);
}

export function useProfileSettings() {
  const [profile, setProfile] = useState<ExtendedProfileSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const initializedRef = useRef(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const payload = await fetchProfileSettings();
      setProfile(normalizeProfileSettings(payload));
    } catch (err) {
      const message = err instanceof Error ? err.message : '加载个人中心配置失败';
      setError(message || '加载个人中心配置失败');
      setProfile((prev) => prev || normalizeProfileSettings(DEFAULT_PROFILE_SETTINGS));
    } finally {
      setLoading(false);
    }
  }, []);

  const save = useCallback(
    async (updates: ProfileSettingsUpdateRequest) => {
      setError('');
      const payload = await updateProfileSettings(sanitizeProfileUpdateRequest(updates));
      const normalized = normalizeProfileSettings(payload);
      setProfile(normalized);
      return normalized;
    },
    []
  );

  const saveApiKeys = useCallback(
    async (updates: ProfileKeysUpdateRequest) => {
      setError('');
      const payload: ProfileKeysUpdateRequest = {};
      if (updates.llm_unified_api_key !== undefined) payload.llm_unified_api_key = String(updates.llm_unified_api_key || '');
      if (updates.llm_listening_api_key !== undefined) payload.llm_listening_api_key = String(updates.llm_listening_api_key || '');
      if (updates.llm_reading_api_key !== undefined) payload.llm_reading_api_key = String(updates.llm_reading_api_key || '');
      if (Object.keys(payload).length === 0) {
        return { status: 'ok' as const, updated_fields: [] as string[] };
      }
      const response = await updateProfileKeys(payload);
      await refresh();
      return response;
    },
    [refresh]
  );

  const saveListeningTranslationSettings = useCallback(
    (settings: { mode: ListeningTranslationMode; model: Partial<LlmOptions> | LlmOptions }) => {
      const saved = saveListeningTranslationLocalSettings(settings);
      setProfile((prev) => normalizeProfileSettings({ ...(prev || DEFAULT_PROFILE_SETTINGS), ...saved }));
      return normalizeProfileSettings({ ...(profile || DEFAULT_PROFILE_SETTINGS), ...saved });
    },
    [profile]
  );

  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;
    void refresh();
  }, [refresh]);

  return {
    profile: profile || normalizeProfileSettings(DEFAULT_PROFILE_SETTINGS),
    loading,
    error,
    refresh,
    save,
    saveApiKeys,
    saveListeningTranslationSettings
  };
}
