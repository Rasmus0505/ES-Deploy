import { Eye, EyeOff, Link as LinkIcon, Plus, Trash2, Upload } from 'lucide-react';
import { useEffect, useMemo, useState, type ChangeEvent } from 'react';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle
} from '../../components/ui/drawer';
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
  InputGroupText
} from '../../components/ui/input-group';
import { Label } from '../../components/ui/label';
import { Select } from '../../components/ui/select';
import { TypographyMuted, TypographySmall } from '../../components/ui/typography';
import {
  CLOUD_WHISPER_MODEL_OPTIONS,
  LANGUAGE_OPTIONS,
  LOCAL_WHISPER_MODEL_OPTIONS
} from '../../lib/api/provider-presets';
import type { SubtitleOptionForm } from '../../lib/storage/compat';

export type HistoryUpgradeSourceState = 'ready' | 'missing' | 'expired';

type HistoryUpgradeDrawerProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onClose: () => void;
  onSubmit: () => void;
  pending?: boolean;
  historyTitle: string;
  historyWhisperRuntime?: string;
  historyWhisperModel?: string;
  sourceState: HistoryUpgradeSourceState;
  sourceUrl: string;
  sourceFileName?: string;
  onSourceUrlChange: (value: string) => void;
  onSourceFileChange: (file: File | null) => void;
  whisperBaseUrlOptions: ReadonlyArray<{ value: string; label: string }>;
  newWhisperBaseUrlInput: string;
  onNewWhisperBaseUrlInputChange: (value: string) => void;
  onAddCustomWhisperBaseUrl: () => void;
  onRemoveCurrentCustomWhisperBaseUrl: () => void;
  canRemoveCurrentCustomWhisperBaseUrl: boolean;
  whisperBaseUrlManageError: string;
  options: SubtitleOptionForm;
  onOptionChange: <K extends keyof SubtitleOptionForm>(
    key: K,
    value: SubtitleOptionForm[K],
    options?: { silentWhisperRestore?: boolean }
  ) => void;
};

const WHISPER_RUNTIME_OPTIONS = [
  { value: 'local', label: '本地（速度由电脑性能决定）' },
  { value: 'cloud', label: '云端AI（快速，成本低）' }
] as const;

const withCurrentValueOption = (
  currentValue: string,
  options: ReadonlyArray<{ value: string; label: string }>
) => {
  const safe = String(currentValue || '').trim();
  if (!safe) return options;
  if (options.some((item) => item.value === safe)) return options;
  return [{ value: safe, label: `${safe}（当前值）` }, ...options];
};

function resolveSourceTone(sourceState: HistoryUpgradeSourceState) {
  if (sourceState === 'ready') return 'success' as const;
  if (sourceState === 'expired') return 'warning' as const;
  return 'danger' as const;
}

function resolveSourceLabel(sourceState: HistoryUpgradeSourceState) {
  if (sourceState === 'ready') return '可直接重生成';
  if (sourceState === 'expired') return '源已过期';
  return '缺少可复用源';
}

export function HistoryUpgradeDrawer({
  open,
  onOpenChange,
  onClose,
  onSubmit,
  pending = false,
  historyTitle,
  historyWhisperRuntime = '',
  historyWhisperModel = '',
  sourceState,
  sourceUrl,
  sourceFileName = '',
  onSourceUrlChange,
  onSourceFileChange,
  whisperBaseUrlOptions,
  newWhisperBaseUrlInput,
  onNewWhisperBaseUrlInputChange,
  onAddCustomWhisperBaseUrl,
  onRemoveCurrentCustomWhisperBaseUrl,
  canRemoveCurrentCustomWhisperBaseUrl,
  whisperBaseUrlManageError,
  options,
  onOptionChange
}: HistoryUpgradeDrawerProps) {
  const [showHistoryUpgradeWhisperApiKey, setShowHistoryUpgradeWhisperApiKey] = useState(false);
  const updateOption = <K extends keyof SubtitleOptionForm>(
    key: K,
    value: SubtitleOptionForm[K],
    nextOptions?: { silentWhisperRestore?: boolean }
  ) => {
    onOptionChange(key, value, nextOptions);
  };
  const currentWhisperModelBaseOptions = useMemo(
    () => (options.whisperRuntime === 'local' ? LOCAL_WHISPER_MODEL_OPTIONS : CLOUD_WHISPER_MODEL_OPTIONS),
    [options.whisperRuntime]
  );
  const whisperModelOptions = useMemo(
    () => withCurrentValueOption(options.whisperModel, currentWhisperModelBaseOptions),
    [currentWhisperModelBaseOptions, options.whisperModel]
  );

  const requiresSourceRefill = sourceState !== 'ready';

  useEffect(() => {
    const currentModel = String(options.whisperModel || '').trim();
    if (currentWhisperModelBaseOptions.some((item) => item.value === currentModel)) return;
    const fallbackModel = currentWhisperModelBaseOptions[0]?.value || '';
    if (!fallbackModel) return;
    updateOption('whisperModel', fallbackModel, { silentWhisperRestore: true });
  }, [currentWhisperModelBaseOptions, options.whisperModel]);

  useEffect(() => {
    if (!open) {
      setShowHistoryUpgradeWhisperApiKey(false);
    }
  }, [open]);

  return (
    <Drawer open={open} onOpenChange={onOpenChange} direction="right">
      <DrawerContent className="max-h-screen">
        <DrawerHeader>
          <DrawerTitle>重新生成听力 · 重跑字幕</DrawerTitle>
          <DrawerDescription>历史：{historyTitle || '未命名历史'}。可复用历史参数并重跑字幕流程。</DrawerDescription>
        </DrawerHeader>

        <div className="grid gap-4 overflow-y-auto px-4 pb-2">
          <section className="grid gap-2 rounded-lg border p-3">
            <div className="flex items-center gap-2">
              <Badge tone="info">历史模型</Badge>
              <TypographySmall>{historyWhisperRuntime || '-'}</TypographySmall>
              <TypographySmall>{historyWhisperModel || '-'}</TypographySmall>
            </div>
            <div className="flex items-center gap-2">
              <Badge tone={resolveSourceTone(sourceState)}>源状态</Badge>
              <TypographySmall>{resolveSourceLabel(sourceState)}</TypographySmall>
            </div>
            {requiresSourceRefill ? (
              <TypographyMuted>
                当前历史缺少可复用源，请补 URL 或补本地文件后再提交重新生成。
              </TypographyMuted>
            ) : null}
          </section>

          {requiresSourceRefill ? (
            <section className="grid gap-3 rounded-lg border p-3">
              <Label htmlFor="historyUpgradeSourceUrl">补充 URL 源</Label>
              <InputGroup>
                <InputGroupAddon>
                  <InputGroupText>
                    <LinkIcon />
                    https://
                  </InputGroupText>
                </InputGroupAddon>
                <InputGroupInput
                  id="historyUpgradeSourceUrl"
                  value={sourceUrl}
                  onChange={(event) => onSourceUrlChange(event.target.value)}
                  placeholder="example.com/video.mp4"
                />
              </InputGroup>
              <Label htmlFor="historyUpgradeSourceFile">补充本地视频源</Label>
              <InputGroup>
                <InputGroupAddon>
                  <InputGroupText>
                    <Upload />
                    文件
                  </InputGroupText>
                </InputGroupAddon>
                <InputGroupInput
                  id="historyUpgradeSourceFile"
                  type="file"
                  accept="video/*"
                  onChange={(event: ChangeEvent<HTMLInputElement>) => onSourceFileChange(event.target.files?.[0] || null)}
                />
                {sourceFileName ? (
                  <InputGroupAddon align="inline-end">
                    <InputGroupButton
                      variant="outline"
                      size="xs"
                      onClick={() => onSourceFileChange(null)}
                    >
                      清除
                    </InputGroupButton>
                  </InputGroupAddon>
                ) : null}
              </InputGroup>
              {sourceFileName ? <TypographySmall>已选：{sourceFileName}</TypographySmall> : null}
            </section>
          ) : null}

          <section className="grid gap-3 rounded-lg border p-3">
            <TypographySmall>字幕生成参数</TypographySmall>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="historyUpgradeWhisperRuntime">字幕生成运行方式</Label>
                <Select
                  id="historyUpgradeWhisperRuntime"
                  value={options.whisperRuntime}
                  onChange={(event) => updateOption('whisperRuntime', event.target.value === 'local' ? 'local' : 'cloud')}
                >
                  {WHISPER_RUNTIME_OPTIONS.map((item) => (
                    <option key={item.value} value={item.value}>{item.label}</option>
                  ))}
                </Select>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="historyUpgradeWhisperLanguage">视频语言</Label>
                <Select
                  id="historyUpgradeWhisperLanguage"
                  value={options.whisperLanguage}
                  onChange={(event) => updateOption('whisperLanguage', event.target.value)}
                >
                  {LANGUAGE_OPTIONS.map((item) => (
                    <option key={item.value} value={item.value}>{item.label}</option>
                  ))}
                </Select>
              </div>
              {options.whisperRuntime === 'cloud' ? (
                <div className="grid gap-2 md:col-span-2">
                  <Label htmlFor="historyUpgradeWhisperBaseUrl">字幕生成 URL</Label>
                  <div className="grid gap-2">
                    <Select
                      id="historyUpgradeWhisperBaseUrl"
                      value={options.whisperBaseUrl}
                      onChange={(event) => updateOption('whisperBaseUrl', event.target.value)}
                    >
                      {whisperBaseUrlOptions.map((item) => (
                        <option key={item.value} value={item.value}>{item.label}</option>
                      ))}
                    </Select>
                    <InputGroup className="upload-url-input-group">
                      <InputGroupInput
                        id="historyUpgradeWhisperBaseUrlCustomInput"
                        placeholder="新增自定义 URL（https://...）"
                        value={newWhisperBaseUrlInput}
                        onChange={(event) => onNewWhisperBaseUrlInputChange(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') {
                            event.preventDefault();
                            onAddCustomWhisperBaseUrl();
                          }
                        }}
                      />
                      <InputGroupAddon align="inline-end">
                        <InputGroupButton
                          aria-label="添加字幕生成 URL"
                          title="添加字幕生成 URL"
                          size="icon-xs"
                          onClick={onAddCustomWhisperBaseUrl}
                        >
                          <Plus />
                        </InputGroupButton>
                        <InputGroupButton
                          aria-label="删除当前自定义字幕生成 URL"
                          title="删除当前自定义字幕生成 URL"
                          size="icon-xs"
                          disabled={!canRemoveCurrentCustomWhisperBaseUrl}
                          onClick={onRemoveCurrentCustomWhisperBaseUrl}
                        >
                          <Trash2 />
                        </InputGroupButton>
                      </InputGroupAddon>
                    </InputGroup>
                    {whisperBaseUrlManageError ? (
                      <TypographySmall className="error-text">{whisperBaseUrlManageError}</TypographySmall>
                    ) : (
                      <TypographySmall>可新增并保存自定义 URL，仅自定义项支持删除。</TypographySmall>
                    )}
                  </div>
                </div>
              ) : null}
              <div className="grid gap-2">
                <Label htmlFor="historyUpgradeWhisperModel">字幕生成模型</Label>
                <Select
                  id="historyUpgradeWhisperModel"
                  value={options.whisperModel}
                  onChange={(event) => updateOption('whisperModel', event.target.value)}
                >
                  {whisperModelOptions.map((item) => (
                    <option key={item.value} value={item.value}>{item.label}</option>
                  ))}
                </Select>
              </div>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="historyUpgradeWhisperApiKey">字幕生成 API Key</Label>
              <InputGroup>
                <InputGroupInput
                  id="historyUpgradeWhisperApiKey"
                  type={showHistoryUpgradeWhisperApiKey ? 'text' : 'password'}
                  value={options.whisperApiKey}
                  onChange={(event) => updateOption('whisperApiKey', event.target.value)}
                />
                <InputGroupAddon align="inline-end">
                  <InputGroupButton
                    size="icon-xs"
                    aria-label={`${showHistoryUpgradeWhisperApiKey ? '隐藏' : '显示'} 字幕生成 API Key（升级抽屉）`}
                    title={`${showHistoryUpgradeWhisperApiKey ? '隐藏' : '显示'} 字幕生成 API Key（升级抽屉）`}
                    onClick={() => setShowHistoryUpgradeWhisperApiKey((prev) => !prev)}
                  >
                    {showHistoryUpgradeWhisperApiKey ? <EyeOff /> : <Eye />}
                  </InputGroupButton>
                </InputGroupAddon>
              </InputGroup>
            </div>
          </section>

        </div>

        <DrawerFooter className="border-t">
          <div className="flex items-center justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>取消</Button>
            <Button
              type="button"
              disabled={pending}
              onClick={onSubmit}
            >
              {pending ? '提交中...' : '开始重新生成听力'}
            </Button>
          </div>
        </DrawerFooter>
      </DrawerContent>
    </Drawer>
  );
}
