export type ProviderOption = { value: string; label: string };

export const QWEN35_PLUS_PRESET = {
  base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  model: 'qwen3.5-plus'
} as const;

export const LOCAL_WHISPER_MODEL_OPTIONS: ReadonlyArray<ProviderOption> = [
  { value: 'tiny', label: 'tiny(测试)' },
  { value: 'small', label: 'small（速度快，质量低）' },
  { value: 'medium', label: 'medium（速度一般，质量一般）' },
  { value: 'large-v3', label: 'large-v3（速度慢，质量高）' }
];

export const CLOUD_WHISPER_MODEL_OPTIONS: ReadonlyArray<ProviderOption> = [
  { value: 'paraformer-v2', label: 'paraformer-v2（云端默认）' },
  { value: 'qwen3-asr-flash', label: 'qwen3-asr-flash（推荐）' },
  { value: 'qwen3-asr-flash-filetrans', label: 'qwen3-asr-flash-filetrans（兼容）' }
];

export const LANGUAGE_OPTIONS: ReadonlyArray<ProviderOption> = [
  { value: 'en', label: '英语（en）' },
  { value: 'zh', label: '中文（zh）' },
  { value: 'ja', label: '日语（ja）' },
  { value: 'ko', label: '韩语（ko）' }
];

export const WHISPER_BASE_URL_OPTIONS: ReadonlyArray<ProviderOption> = [
  { value: 'https://dashscope.aliyuncs.com', label: 'paraformer（预设）' },
  { value: 'https://dashscope.aliyuncs.com/api/v1', label: '千问3-ASR-Flash（预设）' }
];

export const LLM_BASE_URL_OPTIONS: ReadonlyArray<ProviderOption> = [
  { value: 'https://api.siliconflow.cn/v1', label: 'SiliconFlow（推荐）' },
  { value: 'https://api.openai.com/v1', label: 'OpenAI 官方' },
  { value: 'https://openrouter.ai/api/v1', label: 'OpenRouter' },
  { value: QWEN35_PLUS_PRESET.base_url, label: '阿里百炼兼容模式（qwen3.5-plus）' },
  { value: 'https://gmn.chuangzuoli.com/v1', label: 'GMN' }
];

export const DEFAULT_LLM_BASE_URL = LLM_BASE_URL_OPTIONS[0].value;
export const DEFAULT_LLM_MODEL = 'gpt-5.2';
export const DEFAULT_WHISPER_BASE_URL = WHISPER_BASE_URL_OPTIONS[0].value;
export const DEFAULT_WHISPER_MODEL = 'paraformer-v2';
export const DEFAULT_SOURCE_LANGUAGE = 'en';
export const DEFAULT_TARGET_LANGUAGE = 'zh';
export const DEFAULT_WHISPER_LANGUAGE = 'en';

const normalizeProviderBaseUrl = (value: string) => String(value || '').trim().replace(/\/+$/, '');

const isValidHttpUrl = (value: string) => {
  const safe = normalizeProviderBaseUrl(value);
  if (!safe) return false;
  try {
    const parsed = new URL(safe);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
};

export const normalizeLlmBaseUrl = normalizeProviderBaseUrl;
export const normalizeWhisperBaseUrl = normalizeProviderBaseUrl;
export const isValidLlmBaseUrl = isValidHttpUrl;
export const isValidWhisperBaseUrl = isValidHttpUrl;
export const getLlmBaseUrlKey = (value: string) => normalizeLlmBaseUrl(value).toLowerCase();
export const getWhisperBaseUrlKey = (value: string) => normalizeWhisperBaseUrl(value).toLowerCase();
