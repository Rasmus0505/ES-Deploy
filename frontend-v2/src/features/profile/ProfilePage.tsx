import { Eye, EyeOff, FlaskConical, RefreshCw, Save } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardBody, CardHeader } from '../../components/ui/card';
import { Checkbox } from '../../components/ui/checkbox';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Select } from '../../components/ui/select';
import { TypographyMuted, TypographyP, TypographySmall } from '../../components/ui/typography';
import { testSubtitleConfig } from '../../lib/api/subtitle';
import {
  useProfileSettings,
  type ExtendedProfileSettings,
  type ListeningTranslationMode
} from '../../lib/hooks/useProfileSettings';
import type { SubtitleJobOptionsPayload } from '../../types/backend';
import type { LlmOptions, ProfileSettingsUpdateRequest } from '../../types/backend';

const LEVEL_OPTIONS: ReadonlyArray<{ value: ExtendedProfileSettings['english_level']; label: string }> = [
  { value: 'junior', label: 'junior (3-4)' },
  { value: 'senior', label: 'senior (5-6)' },
  { value: 'cet4', label: 'cet4 (7-8)' },
  { value: 'cet6', label: 'cet6 (9-10)' },
  { value: 'kaoyan', label: 'kaoyan (10-11)' },
  { value: 'toefl', label: 'toefl (11-12)' },
  { value: 'sat', label: 'sat (11-12)' }
];

const TRANSLATION_MODEL_DEFAULT = 'qwen-mt-flash';
const TRANSLATION_BASE_URL_DEFAULT = 'https://dashscope.aliyuncs.com/compatible-mode/v1';
const LLM_BASE_URL_PRESET_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'https://dashscope.aliyuncs.com/compatible-mode/v1', label: '千问（百炼兼容）' }
];
const LLM_MODEL_PRESET_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'qwen3.5-plus', label: 'qwen3.5-plus' }
];
type ProbeKey = 'website' | 'translation';

type ProbeState = {
  status: 'idle' | 'testing' | 'ok' | 'failed';
  message: string;
};

const PROBE_DEFAULT_STATE: ProbeState = { status: 'idle', message: '待检测' };

function llmEqual(left: LlmOptions, right: LlmOptions) {
  return left.base_url === right.base_url
    && left.api_key === right.api_key
    && left.model === right.model
    && Boolean(left.llm_support_json) === Boolean(right.llm_support_json);
}

function normalizeLlmInput(value: LlmOptions): LlmOptions {
  return {
    base_url: String(value.base_url || '').trim(),
    api_key: String(value.api_key || '').trim(),
    model: String(value.model || '').trim(),
    llm_support_json: Boolean(value.llm_support_json)
  };
}

function normalizeTranslationModelInput(value: LlmOptions): LlmOptions {
  return {
    base_url: String(value.base_url || '').trim() || TRANSLATION_BASE_URL_DEFAULT,
    api_key: String(value.api_key || '').trim(),
    model: String(value.model || '').trim() || TRANSLATION_MODEL_DEFAULT,
    llm_support_json: Boolean(value.llm_support_json)
  };
}

function cloneLlm(value: LlmOptions): LlmOptions {
  return {
    base_url: value.base_url,
    api_key: value.api_key,
    model: value.model,
    llm_support_json: Boolean(value.llm_support_json)
  };
}

function resolveWebsiteLlmSource(value: ExtendedProfileSettings): LlmOptions {
  const mode = String(value.llm_mode || '').trim().toLowerCase();
  if (mode === 'custom') {
    return normalizeLlmInput(value.llm_reading);
  }
  return normalizeLlmInput(value.llm_unified);
}

function normalizeSingleWebsiteDraft(value: ExtendedProfileSettings): ExtendedProfileSettings {
  const website = resolveWebsiteLlmSource(value);
  return {
    ...value,
    llm_mode: 'unified',
    llm_unified: cloneLlm(website),
    llm_listening: cloneLlm(website),
    llm_reading: cloneLlm(website)
  };
}

function isSingleWebsiteLlmShape(value: ExtendedProfileSettings): boolean {
  const mode = String(value.llm_mode || '').trim().toLowerCase();
  if (mode !== 'unified') return false;
  const unified = normalizeLlmInput(value.llm_unified);
  const listening = normalizeLlmInput(value.llm_listening);
  const reading = normalizeLlmInput(value.llm_reading);
  return llmEqual(unified, listening) && llmEqual(unified, reading);
}

function buildPatch(base: ExtendedProfileSettings, next: ExtendedProfileSettings): ProfileSettingsUpdateRequest {
  const patch: ProfileSettingsUpdateRequest = {};
  if (base.english_level !== next.english_level) patch.english_level = next.english_level;
  const baseWebsite = resolveWebsiteLlmSource(base);
  const nextWebsite = normalizeLlmInput(next.llm_unified);
  const shouldSyncWebsite = !isSingleWebsiteLlmShape(base) || !llmEqual(baseWebsite, nextWebsite);
  if (shouldSyncWebsite) {
    patch.llm_mode = 'unified';
    patch.llm_unified = cloneLlm(nextWebsite);
    patch.llm_listening = cloneLlm(nextWebsite);
    patch.llm_reading = cloneLlm(nextWebsite);
  }
  return patch;
}

function buildLlmProbeOptions(llm: LlmOptions): SubtitleJobOptionsPayload {
  return {
    asr_profile: 'balanced',
    llm: normalizeLlmInput(llm),
    whisper: {
      runtime: 'cloud',
      model: 'paraformer-v2',
      language: 'en',
      base_url: 'https://dashscope.aliyuncs.com',
      api_key: 'probe'
    }
  };
}

function LlmConfigForm({
  prefix,
  value,
  onChange,
  apiKeyVisible,
  onToggleApiKey,
  probeState,
  onTest,
  baseUrlPresetOptions,
  modelPresetOptions
}: {
  prefix: string;
  value: LlmOptions;
  onChange: (next: LlmOptions) => void;
  apiKeyVisible: boolean;
  onToggleApiKey: () => void;
  probeState: ProbeState;
  onTest: () => void;
  baseUrlPresetOptions: ReadonlyArray<{ value: string; label: string }>;
  modelPresetOptions: ReadonlyArray<{ value: string; label: string }>;
}) {
  return (
    <div className="profile-llm-section">
      <div className="profile-field-grid">
        <div className="profile-field">
          <Label htmlFor={`${prefix}BaseUrlPreset`}>Base URL 预设</Label>
          <Select
            id={`${prefix}BaseUrlPreset`}
            value={value.base_url}
            onChange={(event) => {
              const nextBaseUrl = event.target.value;
              onChange({ ...value, base_url: nextBaseUrl });
            }}
          >
            {baseUrlPresetOptions.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </Select>
          <TypographySmall>可手工输入覆盖</TypographySmall>
        </div>
        <div className="profile-field">
          <Label htmlFor={`${prefix}BaseUrl`}>Base URL</Label>
          <Input
            id={`${prefix}BaseUrl`}
            value={value.base_url}
            onChange={(event) => onChange({ ...value, base_url: event.target.value })}
            placeholder="https://api.example.com/v1"
          />
        </div>
        <div className="profile-field">
          <Label htmlFor={`${prefix}ModelPreset`}>Model 预设</Label>
          <Select
            id={`${prefix}ModelPreset`}
            value={value.model}
            onChange={(event) => {
              const nextModel = event.target.value;
              onChange({ ...value, model: nextModel });
            }}
          >
            {modelPresetOptions.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </Select>
          <TypographySmall>可手工输入覆盖</TypographySmall>
        </div>
        <div className="profile-field">
          <Label htmlFor={`${prefix}Model`}>Model</Label>
          <Input
            id={`${prefix}Model`}
            value={value.model}
            onChange={(event) => onChange({ ...value, model: event.target.value })}
            placeholder="gpt-5.2"
          />
        </div>
        <div className="profile-field">
          <Label htmlFor={`${prefix}ApiKey`}>API Key</Label>
          <div className="profile-api-key-row">
            <Input
              id={`${prefix}ApiKey`}
              type={apiKeyVisible ? 'text' : 'password'}
              value={value.api_key}
              onChange={(event) => onChange({ ...value, api_key: event.target.value })}
              placeholder="sk-***"
            />
            <Button type="button" variant="outline" size="sm" onClick={onToggleApiKey} aria-label={`${apiKeyVisible ? '隐藏' : '显示'} API Key`}>
              {apiKeyVisible ? <EyeOff size={14} strokeWidth={1.8} /> : <Eye size={14} strokeWidth={1.8} />}
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={onTest} icon={probeState.status === 'testing' ? <RefreshCw size={14} strokeWidth={1.8} /> : <FlaskConical size={14} strokeWidth={1.8} />}>
              测试
            </Button>
            <span className={`probe-dot probe-${probeState.status}`} aria-label={`${prefix}-probe-${probeState.status}`} />
          </div>
          <TypographySmall>{probeState.message}</TypographySmall>
        </div>
      </div>
      <label className="profile-toggle-row" htmlFor={`${prefix}SupportJson`}>
        <Checkbox
          id={`${prefix}SupportJson`}
          checked={Boolean(value.llm_support_json)}
          onCheckedChange={(checked) => onChange({ ...value, llm_support_json: checked === true })}
        />
        <span>支持 JSON 输出</span>
      </label>
    </div>
  );
}

export function ProfilePage() {
  const { profile, loading, error, refresh, save, saveListeningTranslationSettings } = useProfileSettings();
  const [draft, setDraft] = useState<ExtendedProfileSettings>(() => normalizeSingleWebsiteDraft(profile));
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [saveSuccess, setSaveSuccess] = useState('');
  const [apiVisibility, setApiVisibility] = useState<Record<string, boolean>>({});
  const [probeMap, setProbeMap] = useState<Record<ProbeKey, ProbeState>>({
    website: PROBE_DEFAULT_STATE,
    translation: PROBE_DEFAULT_STATE
  });
  const probeAbortRef = useRef<Record<ProbeKey, AbortController | null>>({
    website: null,
    translation: null
  });
  const probeSeqRef = useRef<Record<ProbeKey, number>>({
    website: 0,
    translation: 0
  });

  useEffect(() => {
    setDraft(normalizeSingleWebsiteDraft(profile));
  }, [profile]);

  const patch = useMemo(() => buildPatch(profile, draft), [profile, draft]);
  const hasRemotePatch = Object.keys(patch).length > 0;
  const localTranslationChanged = useMemo(
    () => (
      profile.listening_translation_mode !== draft.listening_translation_mode
      || !llmEqual(profile.listening_translation_model, draft.listening_translation_model)
    ),
    [draft.listening_translation_mode, draft.listening_translation_model, profile.listening_translation_mode, profile.listening_translation_model]
  );
  const canSave = (hasRemotePatch || localTranslationChanged) && !loading && !saving;

  const handleSave = async () => {
    if (!canSave) return;
    setSaving(true);
    setSaveError('');
    setSaveSuccess('');
    try {
      if (hasRemotePatch) {
        await save(patch);
      }
      if (localTranslationChanged) {
        saveListeningTranslationSettings({
          mode: draft.listening_translation_mode,
          model: normalizeTranslationModelInput(draft.listening_translation_model)
        });
      }
      setSaveSuccess('已保存');
    } catch (err) {
      const message = err instanceof Error ? err.message : '保存失败';
      setSaveError(message || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleListeningTranslationModeChange = (mode: ListeningTranslationMode) => {
    setDraft((prev) => ({ ...prev, listening_translation_mode: mode }));
  };

  const handleWebsiteLlmChange = useCallback((next: LlmOptions) => {
    const normalized = normalizeLlmInput(next);
    setDraft((prev) => ({
      ...prev,
      llm_mode: 'unified',
      llm_unified: cloneLlm(normalized),
      llm_listening: cloneLlm(normalized),
      llm_reading: cloneLlm(normalized)
    }));
  }, []);

  const setProbeState = useCallback((key: ProbeKey, next: ProbeState) => {
    setProbeMap((prev) => ({ ...prev, [key]: next }));
  }, []);

  const runProbe = useCallback(async (key: ProbeKey, llm: LlmOptions, trigger: 'manual' | 'auto') => {
    const normalized = key === 'translation' ? normalizeTranslationModelInput(llm) : normalizeLlmInput(llm);
    const ready = Boolean(normalized.base_url && normalized.model && normalized.api_key);
    if (!ready) {
      setProbeState(key, { status: 'idle', message: '待检测' });
      return;
    }
    const nextSeq = Number(probeSeqRef.current[key] || 0) + 1;
    probeSeqRef.current[key] = nextSeq;
    if (probeAbortRef.current[key]) {
      probeAbortRef.current[key]?.abort();
      probeAbortRef.current[key] = null;
    }
    const controller = new AbortController();
    probeAbortRef.current[key] = controller;
    setProbeState(key, { status: 'testing', message: '检测中...' });
    try {
      const payload = await testSubtitleConfig(buildLlmProbeOptions(normalized), 'llm', { signal: controller.signal });
      if (controller.signal.aborted || nextSeq !== probeSeqRef.current[key]) return;
      if (payload?.llm?.ok) {
        const message = payload.llm.message || (key === 'translation' ? '翻译模型可用' : 'LLM 可用');
        setProbeState(key, { status: 'ok', message });
      } else {
        const message = payload?.llm?.message || (key === 'translation' ? '翻译模型不可用' : 'LLM 不可用');
        setProbeState(key, { status: 'failed', message });
        if (trigger === 'manual') {
          toast.error(message);
        }
      }
    } catch (probeError) {
      if (controller.signal.aborted || nextSeq !== probeSeqRef.current[key]) return;
      const message = probeError instanceof Error ? probeError.message || 'LLM 检测失败' : 'LLM 检测失败';
      setProbeState(key, { status: 'failed', message });
      if (trigger === 'manual') {
        toast.error(message);
      }
    } finally {
      if (probeAbortRef.current[key] === controller) {
        probeAbortRef.current[key] = null;
      }
    }
  }, [setProbeState]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (draft.listening_translation_mode === 'translation_model') {
        void runProbe('translation', draft.listening_translation_model, 'auto');
      } else {
        setProbeState('translation', PROBE_DEFAULT_STATE);
      }
      void runProbe('website', draft.llm_unified, 'auto');
    }, 800);
    return () => {
      window.clearTimeout(timer);
    };
  }, [
    draft.listening_translation_mode,
    draft.listening_translation_model.api_key,
    draft.listening_translation_model.base_url,
    draft.listening_translation_model.model,
    draft.llm_unified.api_key,
    draft.llm_unified.base_url,
    draft.llm_unified.model,
    runProbe,
    setProbeState
  ]);

  useEffect(() => () => {
    (['website', 'translation'] as const).forEach((key) => {
      if (probeAbortRef.current[key]) {
        probeAbortRef.current[key]?.abort();
        probeAbortRef.current[key] = null;
      }
    });
  }, []);

  return (
    <div className="page-profile fade-in">
      <Card className="profile-card">
        <CardHeader
          title="个人中心"
          action={<Badge tone={loading ? 'warning' : 'default'}>{loading ? '加载中' : '已就绪'}</Badge>}
        />
        <CardBody className="profile-card__body">
          {error ? <TypographyP className="error-text">{error}</TypographyP> : null}
          <section className="profile-section">
            <TypographySmall asChild className="profile-level-title">
              <h3>英语等级</h3>
            </TypographySmall>
            <div className="profile-field-grid">
              <div className="profile-field">
                <Select
                  id="profileEnglishLevel"
                  aria-label="英语等级"
                  value={draft.english_level}
                  onChange={(event) => setDraft((prev) => ({ ...prev, english_level: event.target.value as ExtendedProfileSettings['english_level'] }))}
                  disabled={loading || saving}
                >
                  {LEVEL_OPTIONS.map((item) => (
                    <option key={item.value} value={item.value}>{item.label}</option>
                  ))}
                </Select>
              </div>
              <div className="profile-field">
                <Label>等级信息</Label>
                <TypographyP>{draft.english_level_cefr} / {draft.english_level_numeric}</TypographyP>
              </div>
            </div>
          </section>

          <section className="profile-section">
            <TypographySmall asChild className="profile-section-title-emphasis">
              <h3>听力翻译模式</h3>
            </TypographySmall>
            <div className="profile-mode-switch" role="group" aria-label="听力翻译模式切换">
              <Button
                type="button"
                variant={draft.listening_translation_mode === 'llm_model' ? 'default' : 'outline'}
                onClick={() => handleListeningTranslationModeChange('llm_model')}
                disabled={loading || saving}
              >
                LLM 模型翻译
              </Button>
              <Button
                type="button"
                variant={draft.listening_translation_mode === 'translation_model' ? 'default' : 'outline'}
                onClick={() => handleListeningTranslationModeChange('translation_model')}
                disabled={loading || saving}
              >
                翻译模型翻译（qwen-mt-flash）
              </Button>
            </div>

            {draft.listening_translation_mode === 'translation_model' ? (
              <div className="profile-llm-group profile-translation-group">
                <TypographySmall>翻译模型配置</TypographySmall>
                <LlmConfigForm
                  prefix="profileListeningTranslation"
                  value={draft.listening_translation_model}
                  onChange={(next) => setDraft((prev) => ({ ...prev, listening_translation_model: normalizeTranslationModelInput(next) }))}
                  apiKeyVisible={Boolean(apiVisibility.profileListeningTranslation)}
                  onToggleApiKey={() => setApiVisibility((prev) => ({ ...prev, profileListeningTranslation: !prev.profileListeningTranslation }))}
                  probeState={probeMap.translation}
                  onTest={() => void runProbe('translation', draft.listening_translation_model, 'manual')}
                  baseUrlPresetOptions={LLM_BASE_URL_PRESET_OPTIONS}
                  modelPresetOptions={LLM_MODEL_PRESET_OPTIONS}
                />
              </div>
            ) : (
              <TypographySmall className="profile-translation-tip">当前使用网站 LLM 配置执行翻译。</TypographySmall>
            )}
          </section>

          <section className="profile-section">
            <TypographySmall asChild className="profile-section-title-emphasis">
              <h3>网站 LLM 配置</h3>
            </TypographySmall>
            <div className="profile-llm-group">
              <LlmConfigForm
                prefix="profileWebsite"
                value={draft.llm_unified}
                onChange={handleWebsiteLlmChange}
                apiKeyVisible={Boolean(apiVisibility.profileWebsite)}
                onToggleApiKey={() => setApiVisibility((prev) => ({ ...prev, profileWebsite: !prev.profileWebsite }))}
                probeState={probeMap.website}
                onTest={() => void runProbe('website', draft.llm_unified, 'manual')}
                baseUrlPresetOptions={LLM_BASE_URL_PRESET_OPTIONS}
                modelPresetOptions={LLM_MODEL_PRESET_OPTIONS}
              />
            </div>
          </section>

          {saveError ? <TypographyP className="error-text">{saveError}</TypographyP> : null}
          {saveSuccess ? <TypographyP className="success-text">{saveSuccess}</TypographyP> : null}

          <div className="profile-actions">
            <Button type="button" variant="secondary" onClick={() => void refresh()} disabled={loading || saving} icon={<RefreshCw size={16} strokeWidth={1.8} />}>
              刷新
            </Button>
            <Button type="button" onClick={() => void handleSave()} disabled={!canSave} icon={<Save size={16} strokeWidth={1.8} />}>
              {saving ? '保存中...' : '保存设置'}
            </Button>
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
