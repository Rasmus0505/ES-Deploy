import {
  Check,
  ClipboardPaste,
  Copy,
  Eraser,
  Upload,
  XCircle
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle
} from '../../components/ui/alert-dialog';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardBody, CardHeader } from '../../components/ui/card';
import { HoverExplain } from '../../components/ui/hover-explain';
import { Input } from '../../components/ui/input';
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
  InputGroupText
} from '../../components/ui/input-group';
import { Label } from '../../components/ui/label';
import { Progress } from '../../components/ui/progress';
import { Select } from '../../components/ui/select';
import { Slider } from '../../components/ui/slider';
import { Spinner } from '../../components/ui/spinner';
import { TabsContent, TabsList, TabsRoot, TabsTrigger } from '../../components/ui/tabs';
import { TypographyH4, TypographyLarge, TypographyMuted, TypographyP, TypographySmall } from '../../components/ui/typography';
import { useCopyToClipboard } from '../../hooks/use-copy-to-clipboard';
import {
  cancelSubtitleJob,
  createSubtitleJobFromFile,
  createSubtitleJobFromUrl,
  fetchHistoryRecords,
  fetchSubtitleJobResult,
  fetchSubtitleJobStatus,
  fetchSubtitleJobVideoBlob,
  fetchWhisperLocalModels,
  syncHistoryRecords
} from '../../lib/api/subtitle';
import { ApiRequestError } from '../../lib/api/http';
import {
  CLOUD_WHISPER_MODEL_OPTIONS,
  LANGUAGE_OPTIONS,
  LOCAL_WHISPER_MODEL_OPTIONS,
  type ProviderOption
} from '../../lib/api/provider-presets';
import {
  getLegacyFileAsFile,
  getLegacyPracticeProgress,
  getListeningIndependentThreshold,
  getListeningRevealGapThreshold,
  getLearningHistory,
  legacyStorageKeys,
  loadSubtitleOptionForm,
  mapFormToSubtitleJobOptions,
  bridgeToLegacyPractice,
  persistSubtitleOptionForm,
  readStorageNumber,
  readStorageString,
  removeLearningHistoryRecordAndCache,
  renameLearningHistoryRecord,
  setListeningIndependentThreshold,
  setListeningRevealGapThreshold,
  secondsToDurationLabel,
  type SubtitleOptionForm,
  writeStorage
} from '../../lib/storage/compat';
import type {
  HistoryRecord,
  JobStageDetail,
  JobStatusResponse,
  SubtitleSyncDiagnostics,
  SubtitleTaskMeta,
  SubtitleJobResult
} from '../../types/backend';
import { selectLlmOptions, useProfileSettings } from '../../lib/hooks/useProfileSettings';
import { HistoryUpgradeDrawer, type HistoryUpgradeSourceState } from './HistoryUpgradeDrawer';

type SourceMode = 'file' | 'url';

const INVALID_LOCAL_MODELS = new Set([
  'paraformer-v2',
  'qwen3-asr-flash-filetrans',
  'distil-large-v2',
  'large-v3-turbo',
  'whisper-large-v3',
  'whisper-large-v3-turbo',
  'whisper-1',
  'whisperx'
]);

type DropdownOption = ProviderOption;

const withCurrentValueOption = (
  currentValue: string,
  options: ReadonlyArray<DropdownOption>
): ReadonlyArray<DropdownOption> => {
  const safe = String(currentValue || '').trim();
  if (!safe) return options;
  if (options.some((item) => item.value === safe)) return options;
  return [{ value: safe, label: `${safe}（当前值）` }, ...options];
};
const TERMINAL_JOB_STATUSES = new Set(['completed', 'failed', 'cancelled']);

type TaskActionKey = 'submit' | 'cancel' | null;

const STAGE_LABELS: Record<string, string> = {
  queued: '排队',
  running: '运行中',
  download_source: '下载素材',
  extract_audio: '提取音频',
  llm_precheck: 'LLM 预检查',
  llm_translate: 'LLM直译',
  demucs: '人声分离',
  asr: '语音识别',
  nlp_split: '基础分句',
  meaning_split: '语义分句',
  summary_terms: '术语提取',
  translate_chunks: '分块翻译',
  split_subtitles: '长句拆分',
  single_line: '单行处理',
  align_timestamps: '时间戳对齐',
  align_and_build: '构建字幕',
  pipeline: '流水线',
  cancelling: '取消中',
  cancelled: '已取消',
  completed: '已完成',
  failed: '失败'
};

const TASK_STAGE_FLOW: ReadonlyArray<string> = [
  'queued',
  'running',
  'extract_audio',
  'asr',
  'llm_translate',
  'align_and_build',
  'completed'
];

const clampNumber = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

const formatDetailUpdateLabel = (updatedAt?: string | null) => {
  if (!updatedAt) return '';
  const parsed = new Date(updatedAt).getTime();
  if (!Number.isFinite(parsed)) return '';
  const delta = Date.now() - parsed;
  if (delta < 1500) return '刚刚更新';
  if (delta < 60_000) return `${Math.max(1, Math.floor(delta / 1000))} 秒前更新`;
  if (delta < 3_600_000) return `${Math.max(1, Math.floor(delta / 60_000))} 分钟前更新`;
  return `${Math.max(1, Math.floor(delta / 3_600_000))} 小时前更新`;
};

const formatEtaLabel = (etaSeconds?: number | null) => {
  const safe = Math.max(0, Number(etaSeconds || 0));
  if (!safe) return '';
  return `预计剩余 ${secondsToDurationLabel(Math.round(safe))}`;
};

const formatDurationFromMs = (value: number) => {
  const safe = Math.max(0, Number(value) || 0);
  if (safe < 1000) return '<1s';
  return secondsToDurationLabel(Math.round(safe / 1000));
};

const getErrorMessage = (error: unknown, fallback = '请求失败') => {
  if (error instanceof ApiRequestError) {
    const payload = error.payload as Record<string, unknown> | null;
    const detail = payload && typeof payload.detail === 'object' ? payload.detail as Record<string, unknown> : null;
    const detailCode = detail && typeof detail.code === 'string' ? detail.code.trim() : '';

    if (detailCode === 'invalid_whisper_model') {
      const detailMessage = typeof detail?.message === 'string' ? detail.message : 'local 模式不支持当前字幕生成模型';
      const model = typeof detail?.model === 'string' ? detail.model : '';
      const runtime = typeof detail?.runtime === 'string' ? detail.runtime : '';
      return `${detailMessage}${runtime || model ? `（runtime=${runtime || '-'} model=${model || '-'}）` : ''}`;
    }

    if (detailCode === 'active_job_exists') {
      const detailMessage = typeof detail?.message === 'string' ? detail.message : '已有字幕任务在执行';
      const activeJobId = typeof detail?.active_job_id === 'string' ? detail.active_job_id : '';
      return activeJobId ? `${detailMessage}（job_id=${activeJobId}）` : detailMessage;
    }

    if (detailCode === 'alignment_retry_requires_partial' || detailCode === 'job_not_continuable_status') {
      const detailMessage = typeof detail?.message === 'string' ? detail.message : '';
      return detailMessage || error.message || fallback;
    }

    return error.message || fallback;
  }
  if (error instanceof Error) return error.message || fallback;
  return fallback;
};

const getSubmitValidationError = (sourceMode: SourceMode, videoFile: File | null, sourceUrl: string, options: SubtitleOptionForm) => {
  if (sourceMode === 'file' && !videoFile) return '请先选择本地视频文件';
  if (sourceMode === 'url' && !sourceUrl.trim()) return '请先输入素材 URL';

  const runtime = String(options.whisperRuntime || '').trim().toLowerCase();
  const model = String(options.whisperModel || '').trim().toLowerCase();
  if (runtime === 'local' && INVALID_LOCAL_MODELS.has(model)) {
    return '你当前选择了本机识别，但这个模型仅支持云端。请改为云端AI，或换成 tiny/small/medium/large-v3。';
  }

  return '';
};

const CONTINUABLE_PENDING_STATES = new Set(['failed', 'cancelled']);
const VALID_JOB_STATUSES = new Set(['queued', 'running', 'completed', 'failed', 'cancelled']);

const normalizeSubtitleTaskMeta = (value: unknown): SubtitleTaskMeta | null => {
  if (!value || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  const pendingStateRaw = String(raw.pending_state || 'none').trim().toLowerCase();
  const pendingState = CONTINUABLE_PENDING_STATES.has(pendingStateRaw) ? pendingStateRaw : 'none';
  const lastJobId = String(raw.last_job_id || '').trim();
  if (!lastJobId) return null;
  const lastJobStatusRaw = String(raw.last_job_status || 'queued').trim().toLowerCase();
  const lastJobStatus = VALID_JOB_STATUSES.has(lastJobStatusRaw) ? lastJobStatusRaw : 'queued';
  const sourceModeRaw = String(raw.source_mode || 'file').trim().toLowerCase();
  const sourceMode = sourceModeRaw === 'url' || sourceModeRaw === 'resume' ? sourceModeRaw : 'file';
  const updatedAt = Math.max(0, Number(raw.updated_at || 0));
  return {
    pending_state: pendingState as SubtitleTaskMeta['pending_state'],
    last_job_id: lastJobId,
    last_job_status: lastJobStatus as SubtitleTaskMeta['last_job_status'],
    last_stage: String(raw.last_stage || '').trim(),
    last_message: String(raw.last_message || '').trim(),
    has_partial_result: Boolean(raw.has_partial_result),
    source_mode: sourceMode as SubtitleTaskMeta['source_mode'],
    updated_at: Number.isFinite(updatedAt) ? updatedAt : Date.now()
  };
};

const buildSrtNameFromVideoName = (videoName: string) => {
  const safe = String(videoName || '').trim();
  if (!safe) return 'subtitle.srt';
  if (/\.srt$/i.test(safe)) return safe;
  const base = safe.replace(/\.[^.]+$/, '').trim() || safe;
  return `${base}.srt`;
};

const buildDisplayNameFromVideoName = (videoName: string) => {
  const safe = String(videoName || '').trim();
  if (!safe) return '未命名素材';
  return safe.replace(/\.[^.]+$/, '').trim() || safe;
};

const getUrlHistorySeed = (sourceUrl: string, jobId: string) => {
  const safeUrl = String(sourceUrl || '').trim();
  try {
    const parsed = new URL(safeUrl);
    const tail = decodeURIComponent(parsed.pathname.split('/').filter(Boolean).pop() || '').trim();
    const candidate = tail || `${parsed.hostname || 'url-source'}-${jobId.slice(0, 8)}.mp4`;
    const videoName = candidate.includes('.') ? candidate : `${candidate}.mp4`;
    return {
      videoName,
      srtName: buildSrtNameFromVideoName(videoName),
      displayName: buildDisplayNameFromVideoName(videoName)
    };
  } catch {
    const fallbackVideoName = `url-source-${jobId.slice(0, 8)}.mp4`;
    return {
      videoName: fallbackVideoName,
      srtName: buildSrtNameFromVideoName(fallbackVideoName),
      displayName: buildDisplayNameFromVideoName(fallbackVideoName)
    };
  }
};

const mergeHistoryMetaByKey = (records: HistoryRecord[], previous: HistoryRecord[]) => {
  const metaByKey = new Map<string, SubtitleTaskMeta | null>();
  previous.forEach((item) => {
    const key = buildHistoryKey(item);
    metaByKey.set(key, normalizeSubtitleTaskMeta(item.subtitleTaskMeta));
  });
  return records.map((item) => {
    const key = buildHistoryKey(item);
    const meta = normalizeSubtitleTaskMeta(item.subtitleTaskMeta) || metaByKey.get(key) || null;
    return { ...item, subtitleTaskMeta: meta };
  });
};

const dedupeHistoryRecords = (records: HistoryRecord[]) => {
  const sorted = sortHistoryRecords(records);
  const seen = new Set<string>();
  const result: HistoryRecord[] = [];
  sorted.forEach((item) => {
    const key = buildHistoryKey(item);
    if (seen.has(key)) return;
    seen.add(key);
    result.push(item);
  });
  return result;
};

const normalizeHistoryRecord = (item: Record<string, unknown>): HistoryRecord | null => {
  const videoName = String(item.videoName || '').trim();
  const srtName = String(item.srtName || '').trim();
  if (!videoName || !srtName) return null;
  const subtitleTaskMeta = normalizeSubtitleTaskMeta(item.subtitleTaskMeta);
  return {
    videoName,
    srtName,
    currentIndex: Number(item.currentIndex || 0),
    totalSentences: Number(item.totalSentences || 0),
    thumbnail: String(item.thumbnail || ''),
    timestamp: Number(item.timestamp || Date.now()),
    completed: Boolean(item.completed),
    historyId: String(item.historyId || ''),
    displayName: String(item.displayName || ''),
    folderId: String(item.folderId || ''),
    subtitleTaskMeta
  };
};

const sortHistoryRecords = (records: HistoryRecord[]) => (
  [...records].sort((left, right) => Number(right.timestamp || 0) - Number(left.timestamp || 0))
);

const buildHistoryKey = (record: Pick<HistoryRecord, 'videoName' | 'srtName'>) => `${record.videoName}::${record.srtName}`;

const getNormalizedLocalHistoryRecords = () => (
  sortHistoryRecords(
    getLearningHistory()
    .map((item) => normalizeHistoryRecord(item as unknown as Record<string, unknown>))
    .filter(Boolean) as HistoryRecord[]
  )
);

export function ListeningUploadPage() {
  const navigate = useNavigate();
  const initialHistoryRecordsRef = useRef<HistoryRecord[]>(getNormalizedLocalHistoryRecords());
  const [sourceMode, setSourceMode] = useState<SourceMode>(
    readStorageString(legacyStorageKeys.subtitleSourceMode, 'file') === 'url' ? 'url' : 'file'
  );
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [sourceUrl, setSourceUrl] = useState('');
  const [options, setOptions] = useState<SubtitleOptionForm>(() => loadSubtitleOptionForm());
  const [independentThreshold, setIndependentThreshold] = useState<number>(() => getListeningIndependentThreshold());
  const [revealGapThreshold, setRevealGapThreshold] = useState<number>(() => getListeningRevealGapThreshold());
  const [status, setStatus] = useState<JobStatusResponse | null>(null);
  const [result, setResult] = useState<SubtitleJobResult | null>(null);
  const [localModels, setLocalModels] = useState<string[]>([]);
  const [errorText, setErrorText] = useState('');
  const [historyRecords, setHistoryRecords] = useState<HistoryRecord[]>(() => initialHistoryRecordsRef.current);
  const [editingHistoryKey, setEditingHistoryKey] = useState('');
  const [editingHistoryName, setEditingHistoryName] = useState('');
  const [pendingDeleteHistory, setPendingDeleteHistory] = useState<HistoryRecord | null>(null);
  const [selectedPendingHistoryKey, setSelectedPendingHistoryKey] = useState('');
  const [startingHistoryKey, setStartingHistoryKey] = useState('');
  const [startingCurrentTaskLearning, setStartingCurrentTaskLearning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState<TaskActionKey>(null);
  const [upgradeDrawerOpen, setUpgradeDrawerOpen] = useState(false);
  const [upgradeRecord, setUpgradeRecord] = useState<HistoryRecord | null>(null);
  const [upgradeOptions, setUpgradeOptions] = useState<SubtitleOptionForm>(() => loadSubtitleOptionForm());
  const [upgradeSourceUrl, setUpgradeSourceUrl] = useState('');
  const [upgradeSourceFile, setUpgradeSourceFile] = useState<File | null>(null);
  const { profile, error: profileError } = useProfileSettings();
  const { copyToClipboard, isCopied } = useCopyToClipboard();

  const pollTimerRef = useRef<number | null>(null);
  const aliveRef = useRef(true);
  const historyRecordsRef = useRef<HistoryRecord[]>(initialHistoryRecordsRef.current);
  const optionsRef = useRef<SubtitleOptionForm>(options);
  const upgradeOptionsRef = useRef<SubtitleOptionForm>(upgradeOptions);
  const jobHistoryKeyMapRef = useRef<Record<string, string>>({});
  const jobSourceModeMapRef = useRef<Record<string, SubtitleTaskMeta['source_mode']>>({});
  const historyVideoFileMapRef = useRef<Record<string, File>>({});

  const profileListeningLlm = useMemo(() => selectLlmOptions(profile, 'listening'), [profile]);
  const subtitleOptions = useMemo(
    () => mapFormToSubtitleJobOptions(options, profileListeningLlm),
    [options, profileListeningLlm]
  );
  const actionBusy = busy || pendingAction !== null;
  const canSubmit = !actionBusy && (sourceMode === 'url' ? sourceUrl.trim().length > 0 : Boolean(videoFile));
  const canCancel = !actionBusy && Boolean(status?.job_id) && ['queued', 'running'].includes(String(status?.status || ''));
  const pendingDeleteHistoryName = String(pendingDeleteHistory?.displayName || pendingDeleteHistory?.videoName || '该历史').trim() || '该历史';
  const upgradeSourceState = useMemo<HistoryUpgradeSourceState>(() => {
    if (!upgradeRecord) return 'missing';
    if (upgradeSourceFile || upgradeSourceUrl.trim()) return 'ready';
    const progress = getLegacyPracticeProgress();
    const matchedCurrentCache = String(progress.videoFileName || '').trim() === upgradeRecord.videoName
      && String(progress.srtFileName || '').trim() === upgradeRecord.srtName;
    return matchedCurrentCache ? 'ready' : 'missing';
  }, [upgradeRecord, upgradeSourceFile, upgradeSourceUrl]);
  const currentWhisperModelBaseOptions = useMemo(
    () => (options.whisperRuntime === 'local' ? LOCAL_WHISPER_MODEL_OPTIONS : CLOUD_WHISPER_MODEL_OPTIONS),
    [options.whisperRuntime]
  );
  const whisperModelOptions = useMemo(
    () => withCurrentValueOption(options.whisperModel, currentWhisperModelBaseOptions),
    [currentWhisperModelBaseOptions, options.whisperModel]
  );
  const whisperLanguageOptions = useMemo(
    () => withCurrentValueOption(options.whisperLanguage, LANGUAGE_OPTIONS),
    [options.whisperLanguage]
  );
  const isTerminalStatus = useMemo(
    () => TERMINAL_JOB_STATUSES.has(String(status?.status || '').trim()),
    [status?.status]
  );
  const stageDurationRows = useMemo(() => {
    if (!status) return [];
    const durations: Record<string, number> = status.stage_durations_ms && typeof status.stage_durations_ms === 'object'
      ? status.stage_durations_ms
      : {};
    const order = Array.isArray(status.stage_order) ? status.stage_order : [];
    const rows: Array<{ stage: string; label: string; durationMs: number }> = [];
    const seen = new Set<string>();

    order.forEach((item) => {
      const stage = String(item || '').trim();
      if (!stage || seen.has(stage)) return;
      seen.add(stage);
      const durationMs = Math.max(0, Number(durations[stage] || 0));
      rows.push({
        stage,
        label: STAGE_LABELS[stage] || stage,
        durationMs
      });
    });

    Object.entries(durations).forEach(([rawStage, value]) => {
      const stage = String(rawStage || '').trim();
      if (!stage || seen.has(stage)) return;
      seen.add(stage);
      rows.push({
        stage,
        label: STAGE_LABELS[stage] || stage,
        durationMs: Math.max(0, Number(value || 0))
      });
    });

    return rows;
  }, [status]);
  const totalDurationLabel = useMemo(
    () => formatDurationFromMs(Math.max(0, Number(status?.total_duration_ms || 0))),
    [status?.total_duration_ms]
  );
  const shouldShowStageDurations = isTerminalStatus && stageDurationRows.length > 0;
  const stageDetail = useMemo<JobStageDetail | null>(() => {
    if (!status || !status.stage_detail || typeof status.stage_detail !== 'object') return null;
    const raw = status.stage_detail as JobStageDetail;
    const stage = String(raw.stage || status.current_stage || '').trim();
    if (!stage) return null;
    return {
      stage,
      step_key: String(raw.step_key || '').trim(),
      step_label: String(raw.step_label || '').trim(),
      done: Math.max(0, Number(raw.done || 0)),
      total: Math.max(0, Number(raw.total || 0)),
      unit: String(raw.unit || '').trim(),
      percent_in_stage: clampNumber(Number(raw.percent_in_stage || 0), 0, 100),
      eta_seconds: raw.eta_seconds ?? null,
      updated_at: raw.updated_at ?? null
    };
  }, [status]);
  const stageRailRows = useMemo(() => {
    if (!status) return [];
    const dynamicOrder = [
      ...(Array.isArray(status.stage_order) ? status.stage_order : []),
      String(status.current_stage || '').trim()
    ];
    const sequence = [...TASK_STAGE_FLOW];
    const hasDownloadSourceInProgress = dynamicOrder.some((item) => String(item || '').trim() === 'download_source');
    if (hasDownloadSourceInProgress && !sequence.includes('download_source')) {
      const runningIndex = sequence.indexOf('running');
      const insertIndex = runningIndex >= 0 ? runningIndex + 1 : 0;
      sequence.splice(insertIndex, 0, 'download_source');
    }
    dynamicOrder.forEach((item) => {
      const stage = String(item || '').trim();
      if (!stage || sequence.includes(stage)) return;
      sequence.push(stage);
    });

    const currentStage = String(status.current_stage || '').trim();
    const currentIndex = sequence.findIndex((stage) => stage === currentStage);
    const isFailed = status.status === 'failed' || status.status === 'cancelled';
    const isCompleted = status.status === 'completed';

    return sequence.map((stage, index) => {
      let state: 'pending' | 'running' | 'done' | 'failed' = 'pending';
      if (currentIndex >= 0) {
        if (index < currentIndex) state = 'done';
        if (index === currentIndex) {
          if (isFailed) state = 'failed';
          else if (isCompleted) state = 'done';
          else state = 'running';
        }
        if (isCompleted && index <= currentIndex) state = 'done';
      }
      return {
        stage,
        label: STAGE_LABELS[stage] || stage,
        state,
        current: index === currentIndex
      };
    });
  }, [status]);
  const syncDiagnostics = useMemo<SubtitleSyncDiagnostics | null>(() => {
    const raw = status?.sync_diagnostics || result?.diagnostics;
    if (!raw || typeof raw !== 'object') return null;
    const source = raw as SubtitleSyncDiagnostics;
    const correctionMethod = String(source.correction_method || '').trim() || 'none';
    return {
      alignment_quality_score: Number(source.alignment_quality_score || 0),
      global_offset_ms: Number(source.global_offset_ms || 0),
      drift_scale: Number(source.drift_scale || 1),
      correction_applied: Boolean(source.correction_applied),
      correction_method: correctionMethod,
      triggered: Boolean(source.triggered),
      correction_score: Number(source.correction_score || 0)
    };
  }, [result?.diagnostics, status?.sync_diagnostics]);
  const stageDetailUpdateLabel = useMemo(
    () => formatDetailUpdateLabel(stageDetail?.updated_at),
    [stageDetail?.updated_at]
  );
  const stageDetailEtaLabel = useMemo(
    () => formatEtaLabel(stageDetail?.eta_seconds ?? null),
    [stageDetail?.eta_seconds]
  );
  const isSubmittingJob = pendingAction === 'submit';
  const isCancellingJob = pendingAction === 'cancel';

  const clearPollTimer = useCallback(() => {
    if (pollTimerRef.current) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const persistHistoryRecordsToLocal = useCallback((records: HistoryRecord[]) => {
    const safeRecords = dedupeHistoryRecords(records);
    writeStorage(legacyStorageKeys.learningHistory, JSON.stringify(safeRecords));
    const prevRevision = Math.max(0, readStorageNumber(legacyStorageKeys.learningHistoryRevision, 0));
    const nowRevision = Date.now();
    const nextRevision = nowRevision > prevRevision ? nowRevision : prevRevision + 1;
    writeStorage(legacyStorageKeys.learningHistoryRevision, String(nextRevision));
  }, []);

  const commitHistoryRecords = useCallback((records: HistoryRecord[]) => {
    const safeRecords = dedupeHistoryRecords(records);
    historyRecordsRef.current = safeRecords;
    setHistoryRecords(safeRecords);
    persistHistoryRecordsToLocal(safeRecords);
    return safeRecords;
  }, [persistHistoryRecordsToLocal]);

  const syncHistoryRecordsSafely = useCallback(async (records: HistoryRecord[], fallbackMessage: string) => {
    try {
      await syncHistoryRecords(records);
    } catch (error) {
      const message = getErrorMessage(error, fallbackMessage);
      setErrorText(message);
    }
  }, []);

  const upsertHistoryRecord = useCallback((record: HistoryRecord) => {
    const historyKey = buildHistoryKey(record);
    const nextRecords = historyRecordsRef.current.filter((item) => buildHistoryKey(item) !== historyKey);
    const existing = historyRecordsRef.current.find((item) => buildHistoryKey(item) === historyKey) || null;
    const merged: HistoryRecord = {
      ...(existing || {}),
      ...record,
      subtitleTaskMeta: normalizeSubtitleTaskMeta(record.subtitleTaskMeta) || normalizeSubtitleTaskMeta(existing?.subtitleTaskMeta)
    };
    return commitHistoryRecords([merged, ...nextRecords]);
  }, [commitHistoryRecords]);

  const setPendingMetaByJobStatus = useCallback((
    jobId: string,
    payload: JobStatusResponse,
    sourceModeOverride?: SubtitleTaskMeta['source_mode']
  ) => {
    const mappedHistoryKey = jobHistoryKeyMapRef.current[jobId];
    const targetRecord = historyRecordsRef.current.find((item) => {
      if (mappedHistoryKey && buildHistoryKey(item) === mappedHistoryKey) return true;
      return normalizeSubtitleTaskMeta(item.subtitleTaskMeta)?.last_job_id === jobId;
    });
    if (!targetRecord) return historyRecordsRef.current;
    const historyKey = buildHistoryKey(targetRecord);
    const sourceMode = sourceModeOverride
      || jobSourceModeMapRef.current[jobId]
      || normalizeSubtitleTaskMeta(targetRecord.subtitleTaskMeta)?.source_mode
      || 'file';
    const pendingState = payload.status === 'failed' || payload.status === 'cancelled' ? payload.status : 'none';
    const nextMeta: SubtitleTaskMeta = {
      pending_state: pendingState,
      last_job_id: jobId,
      last_job_status: payload.status,
      last_stage: String(payload.current_stage || '').trim(),
      last_message: String(payload.error || payload.message || '').trim(),
      has_partial_result: Boolean(payload.partial_result),
      source_mode: sourceMode,
      updated_at: Date.now()
    };
    const nextRecords = historyRecordsRef.current.map((item) => (
      buildHistoryKey(item) === historyKey
        ? {
            ...item,
            timestamp: Date.now(),
            completed: pendingState === 'none' ? true : false,
            subtitleTaskMeta: nextMeta
          }
        : item
    ));
    return commitHistoryRecords(nextRecords);
  }, [commitHistoryRecords]);

  const clearPendingMetaByHistoryKey = useCallback((historyKey: string) => {
    const nextRecords = historyRecordsRef.current.map((item) => (
      buildHistoryKey(item) === historyKey
        ? {
            ...item,
            timestamp: Date.now(),
            subtitleTaskMeta: null
          }
        : item
    ));
    return commitHistoryRecords(nextRecords);
  }, [commitHistoryRecords]);

  const fetchResult = useCallback(async (jobId: string) => {
    const payload = await fetchSubtitleJobResult(jobId);
    if (!aliveRef.current) return;
    setResult(payload);
  }, []);

  const pollJob = useCallback(async (jobId: string) => {
    try {
      const payload = await fetchSubtitleJobStatus(jobId);
      if (!aliveRef.current) return;
      setStatus(payload);
      if (payload.status === 'completed') {
        clearPollTimer();
        const updated = setPendingMetaByJobStatus(jobId, payload);
        void syncHistoryRecordsSafely(updated, '任务已完成，但历史同步后端失败');
        await fetchResult(jobId);
        return;
      }
      if (payload.status === 'failed' || payload.status === 'cancelled') {
        clearPollTimer();
        const updated = setPendingMetaByJobStatus(jobId, payload);
        const selectedKey = jobHistoryKeyMapRef.current[jobId];
        if (selectedKey) {
          setSelectedPendingHistoryKey(selectedKey);
        }
        void syncHistoryRecordsSafely(updated, '任务状态已更新，但历史同步后端失败');
        const errorCode = String(payload.error_code || '').trim();
        const baseMessage = String(payload.error || payload.message || '任务失败').trim() || '任务失败';
        setErrorText(errorCode ? `[${errorCode}] ${baseMessage}` : baseMessage);
        return;
      }
      clearPollTimer();
      const nextDelay = clampNumber(Number(payload.poll_interval_ms_hint || 800), 600, 1500);
      pollTimerRef.current = window.setTimeout(() => {
        void pollJob(jobId);
      }, nextDelay);
    } catch (error) {
      clearPollTimer();
      setErrorText(getErrorMessage(error, '轮询失败'));
    }
  }, [clearPollTimer, fetchResult, setPendingMetaByJobStatus, syncHistoryRecordsSafely]);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      clearPollTimer();
    };
  }, [clearPollTimer]);

  useEffect(() => {
    historyRecordsRef.current = historyRecords;
  }, [historyRecords]);

  useEffect(() => {
    optionsRef.current = options;
  }, [options]);

  useEffect(() => {
    upgradeOptionsRef.current = upgradeOptions;
  }, [upgradeOptions]);

  useEffect(() => {
    if (!selectedPendingHistoryKey) return;
    const stillExists = historyRecords.some((item) => buildHistoryKey(item) === selectedPendingHistoryKey);
    if (!stillExists) {
      setSelectedPendingHistoryKey('');
    }
  }, [historyRecords, selectedPendingHistoryKey]);

  useEffect(() => {
    if (!pendingDeleteHistory) return;
    const pendingDeleteHistoryKey = buildHistoryKey(pendingDeleteHistory);
    const stillExists = historyRecords.some((item) => buildHistoryKey(item) === pendingDeleteHistoryKey);
    if (!stillExists) {
      setPendingDeleteHistory(null);
    }
  }, [historyRecords, pendingDeleteHistory]);

  useEffect(() => {
    persistSubtitleOptionForm(options);
  }, [options]);

  useEffect(() => {
    setListeningIndependentThreshold(independentThreshold);
  }, [independentThreshold]);

  useEffect(() => {
    setListeningRevealGapThreshold(revealGapThreshold);
  }, [revealGapThreshold]);

  useEffect(() => {
    writeStorage(legacyStorageKeys.subtitleSourceMode, sourceMode);
  }, [sourceMode]);

  useEffect(() => {
    const allowed = options.whisperRuntime === 'local' ? LOCAL_WHISPER_MODEL_OPTIONS : CLOUD_WHISPER_MODEL_OPTIONS;
    const currentModel = String(options.whisperModel || '').trim();
    if (allowed.some((item) => item.value === currentModel)) return;
    const fallbackModel = allowed[0]?.value || '';
    if (!fallbackModel) return;
    setOptions((prev) => {
      if (prev.whisperModel === fallbackModel) return prev;
      const next = { ...prev, whisperModel: fallbackModel };
      optionsRef.current = next;
      return next;
    });
  }, [options.whisperModel, options.whisperRuntime]);

  useEffect(() => {
    if (options.whisperRuntime !== 'local') {
      setLocalModels([]);
      return;
    }
    void (async () => {
      try {
        const payload = await fetchWhisperLocalModels();
        if (!aliveRef.current) return;
        setLocalModels((payload.models || []).map((item) => `${item.model}:${item.installed ? 'installed' : 'download'}`));
      } catch (error) {
        setErrorText(getErrorMessage(error, '本地模型探测失败'));
      }
    })();
  }, [options.whisperRuntime]);

  const updateOption = <K extends keyof SubtitleOptionForm>(key: K, value: SubtitleOptionForm[K]) => {
    setOptions((prev) => {
      const next = { ...prev, [key]: value } as SubtitleOptionForm;
      optionsRef.current = next;
      return next;
    });
  };

  const updateUpgradeOption = <K extends keyof SubtitleOptionForm>(key: K, value: SubtitleOptionForm[K]) => {
    setUpgradeOptions((prev) => {
      const next = { ...prev, [key]: value } as SubtitleOptionForm;
      upgradeOptionsRef.current = next;
      return next;
    });
  };

  const handleSourceModeChange = (nextValue: string) => {
    setSourceMode(nextValue === 'url' ? 'url' : 'file');
  };

  const handlePasteSourceUrl = async () => {
    if (!navigator.clipboard?.readText) {
      setErrorText('当前浏览器不支持读取剪贴板，请手动粘贴 URL。');
      return;
    }

    try {
      const pasted = await navigator.clipboard.readText();
      if (!aliveRef.current) return;
      setSourceUrl(String(pasted || '').trim());
      setErrorText('');
    } catch (error) {
      setErrorText(getErrorMessage(error, '读取剪贴板失败，请手动粘贴 URL。'));
    }
  };

  const handleCopySourceUrl = () => {
    const value = String(sourceUrl || '').trim();
    if (!value) return;
    copyToClipboard(value);
  };

  const handleClearSourceUrl = () => {
    setSourceUrl('');
    setErrorText('');
  };

  const handleSubmit = async () => {
    if (!canSubmit || pendingAction) return;
    const validationError = getSubmitValidationError(sourceMode, videoFile, sourceUrl, options);
    if (validationError) {
      setErrorText(validationError);
      return;
    }

    setPendingAction('submit');
    setBusy(true);
    setErrorText('');
    setStatus(null);
    setResult(null);
    clearPollTimer();
    const loadingToastId = toast.loading('正在创建任务...');

    try {
      const created = sourceMode === 'url'
        ? await createSubtitleJobFromUrl({ url: sourceUrl.trim(), options: subtitleOptions })
        : await createSubtitleJobFromFile(videoFile as File, subtitleOptions);
      const sourceModeForMeta: SubtitleTaskMeta['source_mode'] = sourceMode === 'url' ? 'url' : 'file';
      const sourceSeed = sourceMode === 'url'
        ? getUrlHistorySeed(sourceUrl.trim(), created.job_id)
        : {
            videoName: String(videoFile?.name || `local-source-${created.job_id.slice(0, 8)}.mp4`).trim(),
            srtName: buildSrtNameFromVideoName(String(videoFile?.name || `local-source-${created.job_id.slice(0, 8)}.mp4`)),
            displayName: buildDisplayNameFromVideoName(String(videoFile?.name || '本地素材'))
          };
      const createdRecord: HistoryRecord = {
        videoName: sourceSeed.videoName,
        srtName: sourceSeed.srtName,
        currentIndex: 0,
        totalSentences: 1,
        thumbnail: '',
        timestamp: Date.now(),
        completed: false,
        historyId: `${sourceSeed.videoName}_${sourceSeed.srtName}`,
        displayName: sourceSeed.displayName,
        folderId: '',
        subtitleTaskMeta: {
          pending_state: 'none',
          last_job_id: created.job_id,
          last_job_status: 'queued',
          last_stage: 'queued',
          last_message: '任务已排队',
          has_partial_result: false,
          source_mode: sourceModeForMeta,
          updated_at: Date.now()
        }
      };
      const updatedRecords = upsertHistoryRecord(createdRecord);
      const historyKey = buildHistoryKey(createdRecord);
      jobHistoryKeyMapRef.current[created.job_id] = historyKey;
      jobSourceModeMapRef.current[created.job_id] = sourceModeForMeta;
      if (sourceModeForMeta === 'file' && videoFile) {
        historyVideoFileMapRef.current[historyKey] = videoFile;
      }
      void syncHistoryRecordsSafely(updatedRecords, '任务已创建，但历史同步后端失败');
      toast.success('任务已创建，开始执行', { id: loadingToastId });
      await pollJob(created.job_id);
    } catch (error) {
      const message = getErrorMessage(error, '创建任务失败');
      setErrorText(message);
      toast.error(message, { id: loadingToastId });
    } finally {
      setBusy(false);
      setPendingAction(null);
    }
  };

  const handleCancel = async () => {
    if (!status?.job_id || pendingAction) return;
    setPendingAction('cancel');
    setBusy(true);
    const loadingToastId = toast.loading('正在提交取消请求...');
    try {
      await cancelSubtitleJob(status.job_id);
      toast.success('已提交取消请求', { id: loadingToastId });
      await pollJob(status.job_id);
    } catch (error) {
      const message = getErrorMessage(error, '取消失败');
      setErrorText(message);
      toast.error(message, { id: loadingToastId });
    } finally {
      setBusy(false);
      setPendingAction(null);
    }
  };

  const handleLoadRemoteHistory = async () => {
    try {
      const payload = await fetchHistoryRecords();
      const remote = (payload.records || [])
        .map((item) => normalizeHistoryRecord(item as unknown as Record<string, unknown>))
        .filter(Boolean) as HistoryRecord[];
      if (remote.length > 0) {
        const merged = mergeHistoryMetaByKey(remote, historyRecordsRef.current);
        commitHistoryRecords(merged);
        return;
      }
      commitHistoryRecords(getNormalizedLocalHistoryRecords());
    } catch (error) {
      setErrorText(getErrorMessage(error, '拉取后端历史失败'));
    }
  };

  const handleSyncHistory = async () => {
    setBusy(true);
    try {
      const records = historyRecordsRef.current;
      if (records.length === 0) {
        setErrorText('本地暂无可同步历史，请先开始一次听力练习。');
        return;
      }
      const payload = await syncHistoryRecords(records);
      const synced = (payload.records || [])
        .map((item) => normalizeHistoryRecord(item as unknown as Record<string, unknown>))
        .filter(Boolean) as HistoryRecord[];
      const merged = mergeHistoryMetaByKey(synced, records);
      commitHistoryRecords(merged);
    } catch (error) {
      setErrorText(getErrorMessage(error, '同步本地历史失败'));
    } finally {
      setBusy(false);
    }
  };

  const handleStartRenameHistory = (record: HistoryRecord) => {
    setEditingHistoryKey(buildHistoryKey(record));
    setEditingHistoryName((record.displayName || record.videoName || '').trim());
  };

  const handleCancelRenameHistory = () => {
    setEditingHistoryKey('');
    setEditingHistoryName('');
  };

  const handleSaveRenameHistory = async (record: HistoryRecord) => {
    const nextName = editingHistoryName.trim();
    const renamed = renameLearningHistoryRecord(record.videoName, record.srtName, nextName);
    const normalizedRenamed = (renamed || [])
      .map((item) => normalizeHistoryRecord(item as unknown as Record<string, unknown>))
      .filter(Boolean) as HistoryRecord[];
    const merged = mergeHistoryMetaByKey(normalizedRenamed, historyRecordsRef.current);
    const updatedRecords = commitHistoryRecords(merged);
    setEditingHistoryKey('');
    setEditingHistoryName('');

    try {
      await syncHistoryRecords(updatedRecords);
    } catch (error) {
      setErrorText(getErrorMessage(error, '本地已改名，后端同步失败'));
    }
  };

  const handleOpenDeleteHistoryDialog = (record: HistoryRecord) => {
    setPendingDeleteHistory(record);
  };

  const handleDeleteHistory = async () => {
    const record = pendingDeleteHistory;
    if (!record) return;

    setBusy(true);
    try {
      const removed = await removeLearningHistoryRecordAndCache(record.videoName, record.srtName);
      const normalizedRemoved = (removed.records || [])
        .map((item) => normalizeHistoryRecord(item as unknown as Record<string, unknown>))
        .filter(Boolean) as HistoryRecord[];
      const merged = mergeHistoryMetaByKey(normalizedRemoved, historyRecordsRef.current);
      const updatedRecords = commitHistoryRecords(merged);
      if (editingHistoryKey === buildHistoryKey(record)) {
        setEditingHistoryKey('');
        setEditingHistoryName('');
      }
      if (selectedPendingHistoryKey === buildHistoryKey(record)) {
        setSelectedPendingHistoryKey('');
      }
      delete historyVideoFileMapRef.current[buildHistoryKey(record)];

      await syncHistoryRecords(updatedRecords);
      setPendingDeleteHistory(null);
    } catch (error) {
      setErrorText(getErrorMessage(error, '删除历史失败'));
    } finally {
      setBusy(false);
    }
  };

  const handleSelectHistoryForContinue = (record: HistoryRecord) => {
    const historyKey = buildHistoryKey(record);
    setSelectedPendingHistoryKey(historyKey);
    const taskMeta = normalizeSubtitleTaskMeta(record.subtitleTaskMeta);
    if (!taskMeta || !CONTINUABLE_PENDING_STATES.has(taskMeta.pending_state)) return;
    handleOpenUpgrade(record);
  };

  const handleOpenLearningHistory = useCallback(() => {
    navigate('/listening#history-records');
    if (typeof window === 'undefined' || typeof document === 'undefined') return;
    window.setTimeout(() => {
      const target = document.getElementById('history-records');
      if (!target) return;
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 0);
  }, [navigate]);

  const resolveVideoFileForLearning = useCallback(async (record: HistoryRecord, jobId: string) => {
    const historyKey = buildHistoryKey(record);
    const inMemoryVideo = historyVideoFileMapRef.current[historyKey];
    if (inMemoryVideo) {
      return inMemoryVideo;
    }

    if (status?.job_id === jobId && videoFile && String(videoFile.name || '').trim() === String(record.videoName || '').trim()) {
      return videoFile;
    }

    const progress = getLegacyPracticeProgress();
    const matchedCurrentCache = String(progress.videoFileName || '').trim() === String(record.videoName || '').trim()
      && String(progress.srtFileName || '').trim() === String(record.srtName || '').trim();
    if (matchedCurrentCache) {
      const cachedFile = await getLegacyFileAsFile('current_video', record.videoName || 'history-video.mp4');
      if (cachedFile) return cachedFile;
    }

    try {
      const payload = await fetchSubtitleJobVideoBlob(jobId);
      const fallbackName = `history-${jobId.slice(0, 8)}.mp4`;
      const nextName = String(payload.filename || record.videoName || fallbackName).trim() || fallbackName;
      return new File([payload.blob], nextName, { type: payload.blob.type || 'video/mp4' });
    } catch (error) {
      if (error instanceof ApiRequestError && (error.status === 404 || error.status === 409)) {
        return null;
      }
      throw error;
    }
  }, [status?.job_id, videoFile]);

  const handleStartLearning = useCallback(async (record: HistoryRecord) => {
    if (busy || pendingAction || Boolean(startingHistoryKey)) return;
    const historyKey = buildHistoryKey(record);
    setStartingHistoryKey(historyKey);
    setErrorText('');

    try {
      const taskMeta = normalizeSubtitleTaskMeta(record.subtitleTaskMeta);
      let jobId = String(taskMeta?.last_job_id || '').trim();
      if (!jobId) {
        try {
          const payload = await fetchHistoryRecords();
          const remote = (payload.records || [])
            .map((item) => normalizeHistoryRecord(item as unknown as Record<string, unknown>))
            .filter(Boolean) as HistoryRecord[];
          const matched = remote.find((item) => buildHistoryKey(item) === historyKey) || null;
          const remoteMeta = normalizeSubtitleTaskMeta(matched?.subtitleTaskMeta);
          if (remoteMeta?.last_job_id) {
            jobId = remoteMeta.last_job_id;
          }
        } catch {
          // ignore remote fallback failures and use local hint
        }
      }
      if (!jobId) {
        setErrorText('该历史缺少任务 ID，请先重建任务后再开始学习。');
        return;
      }

      const resultPayload = await fetchSubtitleJobResult(jobId);
      const videoFileForPractice = await resolveVideoFileForLearning(record, jobId);
      if (!videoFileForPractice) {
        setErrorText('未找到可复用视频，请重建任务或补充本地视频后再开始学习。');
        return;
      }

      await bridgeToLegacyPractice({
        videoFile: videoFileForPractice,
        result: resultPayload,
        currentIndex: Math.max(0, Number(record.currentIndex || 0)),
        totalAttempts: 0,
        correctAttempts: 0
      });
      navigate('/listening/practice');
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 409) {
        setErrorText('任务尚未完成，暂不可开始学习。');
        return;
      }
      if (error instanceof ApiRequestError && error.status === 404) {
        setErrorText('任务结果不存在，请重新创建任务后再开始学习。');
        return;
      }
      setErrorText(getErrorMessage(error, '开始学习失败'));
    } finally {
      setStartingHistoryKey((current) => (current === historyKey ? '' : current));
    }
  }, [busy, navigate, pendingAction, resolveVideoFileForLearning, startingHistoryKey]);

  const handleStartLearningFromCurrentTask = useCallback(async () => {
    if (busy || pendingAction || startingCurrentTaskLearning) return;
    if (!status?.job_id || status.status !== 'completed' || !result) return;

    setStartingCurrentTaskLearning(true);
    setErrorText('');
    try {
      const historyKey = String(jobHistoryKeyMapRef.current[status.job_id] || '').trim();
      const matchedRecord = historyKey
        ? (historyRecordsRef.current.find((item) => buildHistoryKey(item) === historyKey) || null)
        : null;

      if (matchedRecord) {
        await handleStartLearning(matchedRecord);
        return;
      }

      if (!videoFile) {
        setErrorText('未找到可复用视频，请从历史记录卡片“开始学习”重试或重新创建任务。');
        return;
      }

      await bridgeToLegacyPractice({
        videoFile,
        result,
        currentIndex: 0,
        totalAttempts: 0,
        correctAttempts: 0
      });
      navigate('/listening/practice');
    } catch (error) {
      setErrorText(getErrorMessage(error, '开始学习失败'));
    } finally {
      setStartingCurrentTaskLearning(false);
    }
  }, [busy, handleStartLearning, navigate, pendingAction, result, startingCurrentTaskLearning, status, videoFile]);

  const handleOpenUpgrade = (record: HistoryRecord) => {
    const nextUpgradeOptions = { ...options };
    setUpgradeRecord(record);
    upgradeOptionsRef.current = nextUpgradeOptions;
    setUpgradeOptions(nextUpgradeOptions);
    setUpgradeSourceUrl('');
    setUpgradeSourceFile(null);
    setUpgradeDrawerOpen(true);
  };

  const handleCloseUpgrade = () => {
    setUpgradeDrawerOpen(false);
  };

  const handleSubmitUpgrade = async () => {
    if (!upgradeRecord) return;
    setBusy(true);
    setErrorText('');
    setStatus(null);
    setResult(null);
    clearPollTimer();

    try {
      const mappedOptions = mapFormToSubtitleJobOptions(
        { ...upgradeOptions },
        profileListeningLlm
      );
      let sourceKind: SourceMode | '';
      let sourceFileForJob: File | null;
      let sourceUrlForJob = '';

      sourceKind = '';
      sourceFileForJob = null;
      if (upgradeSourceFile) {
        sourceKind = 'file';
        sourceFileForJob = upgradeSourceFile;
      } else if (upgradeSourceUrl.trim()) {
        sourceKind = 'url';
        sourceUrlForJob = upgradeSourceUrl.trim();
      } else {
        const progress = getLegacyPracticeProgress();
        const matchedCurrentCache = String(progress.videoFileName || '').trim() === upgradeRecord.videoName
          && String(progress.srtFileName || '').trim() === upgradeRecord.srtName;
        if (matchedCurrentCache) {
          const cachedFile = await getLegacyFileAsFile('current_video', upgradeRecord.videoName || 'history-video.mp4');
          if (cachedFile) {
            sourceKind = 'file';
            sourceFileForJob = cachedFile;
          }
        }
      }

      if (!sourceKind) {
        setErrorText('该历史暂无可复用素材，请在弹窗中补充 URL 或选择本地文件。');
        return;
      }

      const created = sourceKind === 'url'
        ? await createSubtitleJobFromUrl({ url: sourceUrlForJob, options: mappedOptions })
        : await createSubtitleJobFromFile(sourceFileForJob as File, mappedOptions);
      const historyKey = buildHistoryKey(upgradeRecord);
      jobHistoryKeyMapRef.current[created.job_id] = historyKey;
      jobSourceModeMapRef.current[created.job_id] = sourceKind === 'url' ? 'url' : 'file';
      if (sourceKind === 'file' && sourceFileForJob) {
        historyVideoFileMapRef.current[historyKey] = sourceFileForJob;
      }
      const upgradeMeta: SubtitleTaskMeta = {
        pending_state: 'none',
        last_job_id: created.job_id,
        last_job_status: 'queued',
        last_stage: 'queued',
        last_message: '任务已排队',
        has_partial_result: false,
        source_mode: sourceKind === 'url' ? 'url' : 'file',
        updated_at: Date.now()
      };
      const updatedRecords = historyRecordsRef.current.map((item) => (
        buildHistoryKey(item) === historyKey
          ? {
              ...item,
              timestamp: Date.now(),
              subtitleTaskMeta: upgradeMeta
            }
          : item
      ));
      const mergedRecords = commitHistoryRecords(updatedRecords);
      void syncHistoryRecordsSafely(mergedRecords, '重新生成听力任务已创建，但历史同步后端失败');

      if (sourceKind === 'url') {
        setSourceMode('url');
        setSourceUrl(sourceUrlForJob);
        setVideoFile(null);
      } else {
        setSourceMode('file');
        setVideoFile(sourceFileForJob);
        setSourceUrl('');
      }
      setUpgradeDrawerOpen(false);
      await pollJob(created.job_id);
    } catch (error) {
      setErrorText(getErrorMessage(error, '重新生成听力失败'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-listening-upload fade-in">
      <Card id="history-records">
        <CardHeader
          title="历史记录"
          subtitle="查看你的学习历史，可预览封面、改名和删除缓存。"
          action={<Badge tone="default">{historyRecords.length} 条</Badge>}
        />
        <CardBody className="inline-stack">
          <div className="inline-actions">
            <Button type="button" variant="outline" size="sm" onClick={() => void handleLoadRemoteHistory()}>拉取后端历史</Button>
            <Button type="button" size="sm" onClick={() => void handleSyncHistory()} disabled={busy}>同步本地历史</Button>
          </div>
          {historyRecords.length === 0 ? <TypographyMuted>暂无历史记录，先完成一次字幕任务并保留结果即可生成。</TypographyMuted> : null}
          <div className="history-grid">
            {historyRecords.slice(0, 12).map((item) => {
              const historyKey = buildHistoryKey(item);
              const isEditing = editingHistoryKey === historyKey;
              const taskMeta = normalizeSubtitleTaskMeta(item.subtitleTaskMeta);
              const isPending = Boolean(taskMeta && CONTINUABLE_PENDING_STATES.has(taskMeta.pending_state));
              const isCompleted = Boolean(item.completed) && !isPending;
              const isActionable = isPending || isCompleted;
              const isSelectedPending = isPending && selectedPendingHistoryKey === historyKey;
              const isStartingLearning = startingHistoryKey === historyKey;
              const pendingTone = taskMeta?.pending_state === 'failed' ? 'danger' : 'warning';
              const pendingLabel = taskMeta?.pending_state === 'failed' ? '待完成 · 失败' : '待完成 · 已取消';
              return (
                <article
                  key={`${item.videoName}-${item.srtName}-${item.timestamp}`}
                  className={`history-item${isPending ? ' is-pending' : ''}${isSelectedPending ? ' is-selected' : ''}`}
                  role={isActionable ? 'button' : undefined}
                  tabIndex={isActionable ? 0 : undefined}
                  data-history-key={historyKey}
                  onClick={(event) => {
                    if (!isActionable) return;
                    if ((event.target as HTMLElement).closest('button, input, select, textarea, a')) return;
                    if (isPending) {
                      void handleSelectHistoryForContinue(item);
                      return;
                    }
                    void handleStartLearning(item);
                  }}
                  onKeyDown={(event) => {
                    if (!isActionable) return;
                    if (event.key !== 'Enter' && event.key !== ' ') return;
                    event.preventDefault();
                    if (isPending) {
                      void handleSelectHistoryForContinue(item);
                      return;
                    }
                    void handleStartLearning(item);
                  }}
                >
                  <div className="history-item__cover-shell">
                    {item.thumbnail ? (
                      <img className="history-item__cover-image" src={item.thumbnail} alt={`${item.displayName || item.videoName} 封面`} loading="lazy" />
                    ) : (
                      <div className="history-item__cover-empty">暂无封面</div>
                    )}
                  </div>
                  <div className="history-item__content">
                    {isEditing ? (
                      <div className="history-item__rename">
                        <Input
                          value={editingHistoryName}
                          onChange={(event) => setEditingHistoryName(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') {
                              event.preventDefault();
                              void handleSaveRenameHistory(item);
                            }
                            if (event.key === 'Escape') {
                              event.preventDefault();
                              handleCancelRenameHistory();
                            }
                          }}
                          placeholder="输入历史名称"
                        />
                        <div className="inline-actions">
                          <Button type="button" size="sm" onClick={() => void handleSaveRenameHistory(item)}>保存</Button>
                          <Button type="button" variant="outline" size="sm" onClick={handleCancelRenameHistory}>取消</Button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="history-item__title-row">
                          <TypographyH4 asChild>
                            <span className="history-item__title">{item.displayName || item.videoName}</span>
                          </TypographyH4>
                          {isPending ? <Badge tone={pendingTone}>{pendingLabel}</Badge> : null}
                        </div>
                        <TypographyMuted asChild>
                          <span className="history-item__progress">
                            {item.currentIndex + 1}/{Math.max(1, item.totalSentences)} {item.completed ? '· 已完成' : '· 进行中'}
                          </span>
                        </TypographyMuted>
                        {isPending ? (
                          <TypographySmall asChild>
                            <span className="history-item__pending-tip">
                              点击卡片后可直接重新生成听力
                            </span>
                          </TypographySmall>
                        ) : null}
                        <TypographySmall asChild>
                          <span className="history-item__time">{new Date(item.timestamp || Date.now()).toLocaleString()}</span>
                        </TypographySmall>
                        <div className="history-item__actions">
                          {isCompleted ? (
                            <>
                              <Button
                                type="button"
                                size="sm"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  void handleStartLearning(item);
                                }}
                                disabled={actionBusy || isStartingLearning}
                              >
                                {isStartingLearning ? '进入中...' : '开始学习'}
                              </Button>
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  handleOpenLearningHistory();
                                }}
                              >
                                学习记录
                              </Button>
                            </>
                          ) : null}
                          <Button type="button" variant="secondary" size="sm" onClick={(event) => {
                            event.stopPropagation();
                            handleOpenUpgrade(item);
                          }}
                          >
                            重新生成听力
                          </Button>
                          <Button type="button" variant="outline" size="sm" onClick={(event) => {
                            event.stopPropagation();
                            handleStartRenameHistory(item);
                          }}
                          >
                            改名
                          </Button>
                          <Button
                            type="button"
                            variant="destructive"
                            size="sm"
                            onClick={(event) => {
                              event.stopPropagation();
                              handleOpenDeleteHistoryDialog(item);
                            }}
                            disabled={busy}
                          >
                            删除并清缓存
                          </Button>
                        </div>
                      </>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        </CardBody>
      </Card>

      <div className="listening-upload-grid">
        <Card>
          <CardHeader
            title="上传源"
            subtitle="从本地上传视频，或粘贴链接创建字幕任务。"
            action={<Badge tone={sourceMode === 'file' ? 'success' : 'default'}>{sourceMode === 'file' ? 'file' : 'url'}</Badge>}
          />
          <CardBody className="form-grid">
            <TabsRoot value={sourceMode} onValueChange={handleSourceModeChange} className="upload-source-tabs">
              <TabsList className="w-fit">
                <TabsTrigger value="file">本地文件</TabsTrigger>
                <TabsTrigger value="url">URL</TabsTrigger>
              </TabsList>

              <TabsContent value="file" className="mt-3 grid gap-2">
                <Label htmlFor="videoFile">视频文件</Label>
                <InputGroup>
                  <InputGroupAddon>
                    <InputGroupText>上传</InputGroupText>
                  </InputGroupAddon>
                  <InputGroupInput
                    id="videoFile"
                    type="file"
                    accept="video/*"
                    onChange={(event) => setVideoFile(event.target.files?.[0] || null)}
                  />
                </InputGroup>
                <TypographySmall asChild>
                  <span className="upload-file-hint">{videoFile ? videoFile.name : '尚未选择文件'}</span>
                </TypographySmall>
              </TabsContent>

              <TabsContent value="url" className="mt-3 grid gap-2">
                <Label htmlFor="sourceUrl">素材 URL</Label>
                <InputGroup className="upload-url-input-group">
                  <InputGroupAddon>
                    <InputGroupText>https://</InputGroupText>
                  </InputGroupAddon>
                  <InputGroupInput
                    id="sourceUrl"
                    placeholder="example.com/video.mp4"
                    value={sourceUrl}
                    onChange={(event) => setSourceUrl(event.target.value)}
                  />
                  <InputGroupAddon align="inline-end">
                    <InputGroupButton
                      aria-label="粘贴 URL"
                      title="粘贴 URL"
                      size="icon-xs"
                      disabled={busy}
                      onClick={() => void handlePasteSourceUrl()}
                    >
                      <ClipboardPaste />
                    </InputGroupButton>
                    <InputGroupButton
                      aria-label={isCopied ? '已复制' : '复制 URL'}
                      title={isCopied ? '已复制' : '复制 URL'}
                      size="icon-xs"
                      disabled={busy || !sourceUrl.trim()}
                      onClick={handleCopySourceUrl}
                    >
                      {isCopied ? <Check /> : <Copy />}
                    </InputGroupButton>
                    <InputGroupButton
                      aria-label="清空 URL"
                      title="清空 URL"
                      size="icon-xs"
                      disabled={busy || !sourceUrl}
                      onClick={handleClearSourceUrl}
                    >
                      <Eraser />
                    </InputGroupButton>
                  </InputGroupAddon>
                </InputGroup>
                <TypographySmall asChild>
                  <span className="upload-file-hint">支持直链地址，复制后可一键粘贴与清空。</span>
                </TypographySmall>
              </TabsContent>
            </TabsRoot>

            <section className="learning-config-panel">
              <HoverExplain asChild content="调整学习节奏和揭示方式，不确定时可先用默认值。">
                <TypographyLarge asChild>
                  <span className="learning-config-title">学习配置</span>
                </TypographyLarge>
              </HoverExplain>
              <div className="learning-config-grid">
                <div className="listening-threshold-control">
                <div className="listening-threshold-head">
                    <HoverExplain asChild content="达到这个比例后，本句重播结束会自动切到下一句。">
                      <Label id="independentThresholdLabel">独立完成阈值</Label>
                    </HoverExplain>
                    <strong>{independentThreshold}%</strong>
                  </div>
                  <Slider
                    aria-labelledby="independentThresholdLabel"
                    min={0}
                    max={100}
                    step={1}
                    value={[independentThreshold]}
                    onValueChange={(value) => {
                      const nextValue = Number(value?.[0] ?? 70);
                      setIndependentThreshold(Number.isFinite(nextValue) ? nextValue : 70);
                    }}
                  />
                </div>

                <div className="listening-threshold-control">
                  <div className="listening-threshold-head">
                    <HoverExplain asChild content="当你接近目标时，推进键会先帮你揭示当前词。">
                      <Label id="revealGapThresholdLabel">揭示差值阈值</Label>
                    </HoverExplain>
                    <strong>{revealGapThreshold}%</strong>
                  </div>
                  <Slider
                    aria-labelledby="revealGapThresholdLabel"
                    min={0}
                    max={100}
                    step={1}
                    value={[revealGapThreshold]}
                    onValueChange={(value) => {
                      const nextValue = Number(value?.[0] ?? 20);
                      setRevealGapThreshold(Number.isFinite(nextValue) ? nextValue : 20);
                    }}
                  />
                </div>
              </div>
            </section>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="自动字幕配置"
            subtitle="设置字幕生成参数。"
            action={<Badge tone={options.whisperRuntime === 'local' ? 'warning' : 'info'}>{options.whisperRuntime}</Badge>}
          />
          <CardBody className="form-grid">
            <div className="form-two-cols">
              <div>
                <HoverExplain asChild content="字幕生成方式决定转写速度与可用模型。">
                  <Label htmlFor="whisperRuntime">字幕生成运行方式</Label>
                </HoverExplain>
                <Select id="whisperRuntime" value={options.whisperRuntime} onChange={(event) => updateOption('whisperRuntime', event.target.value === 'local' ? 'local' : 'cloud')}>
                  <option value="local">本地（速度由电脑性能决定）</option>
                  <option value="cloud">云端AI（快速，成本低）</option>
                </Select>
              </div>
              <div>
                <HoverExplain asChild content="告诉系统视频主要语言，可提升识别稳定性。">
                  <Label htmlFor="whisperLanguage">视频语言</Label>
                </HoverExplain>
                <Select id="whisperLanguage" value={options.whisperLanguage} onChange={(event) => updateOption('whisperLanguage', event.target.value)}>
                  {whisperLanguageOptions.map((item) => (
                    <option key={item.value} value={item.value}>{item.label}</option>
                  ))}
                </Select>
              </div>
            </div>

            <div>
              <HoverExplain asChild content="选择语音识别模型，模型越大通常越准。">
                <Label htmlFor="whisperModel">字幕生成模型</Label>
              </HoverExplain>
              <Select id="whisperModel" value={options.whisperModel} onChange={(event) => updateOption('whisperModel', event.target.value)}>
                {whisperModelOptions.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </Select>
            </div>

            {options.whisperRuntime === 'cloud' ? (
              <TypographySmall className="upload-file-hint">
                云端识别会自动使用当前账号的 OneAPI 令牌与托管通道，无需填写 URL 或 API Key。
              </TypographySmall>
            ) : null}
            {options.whisperRuntime === 'local' ? (
              <div className="capabilities-grid">
                {localModels.map((model) => <Badge key={model} tone={model.endsWith('installed') ? 'success' : 'warning'}>{model}</Badge>)}
              </div>
            ) : null}
          </CardBody>
        </Card>
        <div className="upload-profile-link-row">
          <TypographyMuted>ASR、翻译模型、LLM 均由系统通过 OneAPI 令牌统一托管，本页无需填写 URL 或 API Key。</TypographyMuted>
          {profileError ? <TypographySmall className="error-text">个人中心读取失败，不影响听力生成和提交。</TypographySmall> : null}
          <Button type="button" variant="outline" size="sm" onClick={() => navigate('/profile')}>
            打开个人中心
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader
          title="任务操作"
          subtitle="创建任务与取消任务。"
          action={<Badge tone={busy ? 'warning' : 'default'}>{busy ? '处理中' : '空闲'}</Badge>}
        />
        <CardBody className="inline-stack">
          <div className="inline-actions">
            <Button
              type="button"
              icon={isSubmittingJob ? <Spinner size="sm" /> : <Upload size={16} strokeWidth={1.8} />}
              disabled={!canSubmit}
              onClick={() => void handleSubmit()}
            >
              {isSubmittingJob ? '创建中...' : '创建任务'}
            </Button>
            <Button
              type="button"
              variant="destructive"
              icon={isCancellingJob ? <Spinner size="sm" /> : <XCircle size={16} strokeWidth={1.8} />}
              disabled={!canCancel}
              onClick={() => void handleCancel()}
            >
              {isCancellingJob ? '取消中...' : '取消'}
            </Button>
          </div>
          {errorText ? <TypographyP className="error-text">{errorText}</TypographyP> : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="任务进度"
          subtitle={status ? `${status.status} / ${status.current_stage}` : '尚未创建任务'}
          subtitleBehavior="inline"
          action={<Badge tone={status?.status === 'completed' ? 'success' : 'info'}>{status?.status || 'idle'}</Badge>}
        />
        <CardBody className="inline-stack">
          <section className="task-progress-shell" aria-label="任务执行详情" aria-live="polite">
            <Progress value={Number(status?.progress_percent || 0)} />
            <div className="task-progress-top">
              <div className="task-progress-top__summary">
                <span className="task-progress-top__percent">{status?.progress_percent || 0}%</span>
                <span className="task-progress-top__message">{status?.message || '等待任务'}</span>
              </div>
              <div className="task-progress-top__badges">
                <Badge tone="info">{STAGE_LABELS[String(status?.current_stage || '').trim()] || status?.current_stage || '-'}</Badge>
                <Badge tone="default">{status?.asr_provider_effective || 'ASR -'}</Badge>
                {status?.error_code ? <Badge tone="danger">code: {status.error_code}</Badge> : null}
              </div>
            </div>

            <div className="task-live-detail">
              <div className="task-live-detail__main">
                {status && !isTerminalStatus ? <Spinner size="sm" label="任务执行中" /> : null}
                <span className="task-live-detail__label">
                  {stageDetail?.step_label || STAGE_LABELS[String(status?.current_stage || '').trim()] || '等待阶段细节'}
                </span>
              </div>
              <div className="task-live-detail__meta">
                {stageDetail ? (
                  <>
                    {stageDetail.total > 0 ? (
                      <span>{Math.max(0, Number(stageDetail.done || 0))}/{Math.max(0, Number(stageDetail.total || 0))}{stageDetail.unit || ''}</span>
                    ) : null}
                    <span>阶段进度 {Math.max(0, Math.min(100, Number(stageDetail.percent_in_stage || 0)))}%</span>
                    {stageDetailEtaLabel ? <span>{stageDetailEtaLabel}</span> : null}
                    {stageDetailUpdateLabel ? <span>{stageDetailUpdateLabel}</span> : null}
                  </>
                ) : (
                  <span>等待阶段细节...</span>
                )}
              </div>
            </div>

            {stageRailRows.length ? (
              <div className="task-stage-rail" aria-label="任务阶段轨道">
                {stageRailRows.map((item) => (
                  <span
                    key={item.stage}
                    className={`task-stage-pill is-${item.state} ${item.current ? 'is-current' : ''}`}
                  >
                    {item.label}
                  </span>
                ))}
              </div>
            ) : null}

          </section>
          {shouldShowStageDurations ? (
            <section className="task-stage-timing" aria-label="本次任务阶段耗时">
              <div className="task-stage-timing__head">
                <TypographySmall>本次任务阶段耗时</TypographySmall>
                <TypographySmall>总耗时：{totalDurationLabel}</TypographySmall>
              </div>
              <div className="task-stage-timing__list">
                {stageDurationRows.map((item) => (
                  <div key={item.stage} className="task-stage-timing__item">
                    <span>{item.label}</span>
                    <span>{formatDurationFromMs(item.durationMs)}</span>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
          {syncDiagnostics ? (
            <section className="task-stage-timing" aria-label="播放同步诊断">
              <div className="task-stage-timing__head">
                <TypographySmall>播放同步诊断</TypographySmall>
                <TypographySmall>
                  {syncDiagnostics.correction_applied
                    ? `已校正(${syncDiagnostics.correction_method})`
                    : `未校正(${syncDiagnostics.correction_method})`}
                </TypographySmall>
              </div>
              <div className="task-stage-timing__list">
                <div className="task-stage-timing__item">
                  <span>alignment_quality_score</span>
                  <span>{Number(syncDiagnostics.alignment_quality_score || 0).toFixed(4)}</span>
                </div>
                <div className="task-stage-timing__item">
                  <span>global_offset_ms</span>
                  <span>{Math.round(Number(syncDiagnostics.global_offset_ms || 0))}</span>
                </div>
                <div className="task-stage-timing__item">
                  <span>drift_scale</span>
                  <span>{Number(syncDiagnostics.drift_scale || 1).toFixed(6)}</span>
                </div>
                <div className="task-stage-timing__item">
                  <span>triggered</span>
                  <span>{syncDiagnostics.triggered ? 'yes' : 'no'}</span>
                </div>
              </div>
            </section>
          ) : null}
          {result ? (
            <>
              <div className="result-grid">
                <div>subtitle_count: {result.subtitles?.length || 0}</div>
                <div>word_segments: {result.word_segments?.length || 0}</div>
                <div>partial: {result.partial ? 'yes' : 'no'}</div>
              </div>
              <div className="inline-actions">
                <Button
                  type="button"
                  size="sm"
                  disabled={status?.status !== 'completed' || actionBusy || startingCurrentTaskLearning}
                  onClick={() => void handleStartLearningFromCurrentTask()}
                >
                  {startingCurrentTaskLearning ? '进入中...' : '开始学习'}
                </Button>
              </div>
            </>
          ) : null}
        </CardBody>
      </Card>

      <AlertDialog open={Boolean(pendingDeleteHistory)} onOpenChange={(open) => {
        if (!open && !busy) {
          setPendingDeleteHistory(null);
        }
      }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除历史并清缓存</AlertDialogTitle>
            <AlertDialogDescription>{`确认删除「${pendingDeleteHistoryName}」吗？将同时删除对应本地缓存。`}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy} onClick={() => setPendingDeleteHistory(null)}>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" disabled={busy} onClick={() => void handleDeleteHistory()}>
              {busy ? '删除中...' : '确认删除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <HistoryUpgradeDrawer
        open={upgradeDrawerOpen}
        onOpenChange={(nextOpen) => {
          setUpgradeDrawerOpen(nextOpen);
        }}
        onClose={handleCloseUpgrade}
        onSubmit={() => void handleSubmitUpgrade()}
        pending={busy}
        historyTitle={upgradeRecord?.displayName || upgradeRecord?.videoName || '未命名历史'}
        historyWhisperRuntime={String((upgradeRecord as unknown as Record<string, unknown>)?.lastWhisperRuntime || '')}
        historyWhisperModel={String((upgradeRecord as unknown as Record<string, unknown>)?.lastWhisperModel || '')}
        sourceState={upgradeSourceState}
        sourceUrl={upgradeSourceUrl}
        sourceFileName={upgradeSourceFile?.name || ''}
        onSourceUrlChange={setUpgradeSourceUrl}
        onSourceFileChange={setUpgradeSourceFile}
        options={upgradeOptions}
        onOptionChange={updateUpgradeOption}
      />

    </div>
  );
}
