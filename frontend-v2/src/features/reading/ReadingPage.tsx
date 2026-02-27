
import { BookCheck, Copy, RefreshCw, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation } from 'react-router-dom';
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
import { Checkbox } from '../../components/ui/checkbox';
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle
} from '../../components/ui/drawer';
import { Label } from '../../components/ui/label';
import { Select } from '../../components/ui/select';
import { Textarea } from '../../components/ui/textarea';
import { TypographyMuted, TypographyP, TypographySmall } from '../../components/ui/typography';
import {
  deleteReadingShortAnswerGroup,
  deleteReadingVersion,
  fetchReadingHistory,
  fetchReadingShortAnswerHistory,
  fetchReadingSources,
  fetchReadingVersion,
  generateReadingMaterial,
  submitReadingShortAnswer
} from '../../lib/api/reading';
import { ApiRequestError } from '../../lib/api/http';
import { fetchHistoryRecords } from '../../lib/api/subtitle';
import { selectLlmOptions, useProfileSettings } from '../../lib/hooks/useProfileSettings';
import type {
  HistoryRecord,
  ReadingDifficultyTier,
  ReadingGenre,
  ReadingHistoryItem,
  ReadingMaterialKind,
  ReadingMaterialResponse,
  ReadingRatioPreset,
  ReadingScope,
  ReadingShortAnswerHistoryItem,
  ReadingSourceSummary
} from '../../types/backend';

const VERSION_CACHE_PREFIX = 'reading_version::';

const LEVEL_OPTIONS = [
  { value: 'junior', label: 'junior (3-4)' },
  { value: 'senior', label: 'senior (5-6)' },
  { value: 'cet4', label: 'cet4 (7-8)' },
  { value: 'cet6', label: 'cet6 (9-10)' },
  { value: 'kaoyan', label: 'kaoyan (10-11)' },
  { value: 'toefl', label: 'toefl (11-12)' },
  { value: 'sat', label: 'sat (11-12)' }
] as const;

const SCOPE_OPTIONS: ReadonlyArray<{ value: ReadingScope; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'intensive', label: '仅精读' },
  { value: 'extensive', label: '仅泛读' }
];
const RATIO_OPTIONS: ReadonlyArray<{ value: ReadingRatioPreset; label: string }> = [
  { value: 'high_energy', label: '高能 70/30' },
  { value: 'long_term', label: '均衡 50/50' },
  { value: 'low_energy', label: '低能 30/70' }
];
const DIFFICULTY_OPTIONS: ReadonlyArray<{ value: ReadingDifficultyTier; label: string }> = [
  { value: 'very_easy', label: 'very_easy' },
  { value: 'easy', label: 'easy' },
  { value: 'balanced', label: 'balanced' },
  { value: 'challenging', label: 'challenging' },
  { value: 'hard', label: 'hard' }
];
const GENRE_OPTIONS: ReadonlyArray<{ value: ReadingGenre; label: string }> = [
  { value: 'news', label: 'news' },
  { value: 'science', label: 'science' },
  { value: 'story', label: 'story' },
  { value: 'workplace', label: 'workplace' }
];

function toSourceKey(videoName: string, srtName: string) {
  return `${String(videoName || '').trim()}\u0000${String(srtName || '').trim()}`;
}

function readCachedVersion(versionId: string) {
  const safeVersionId = String(versionId || '').trim();
  if (!safeVersionId) return null;
  try {
    const raw = localStorage.getItem(`${VERSION_CACHE_PREFIX}${safeVersionId}`);
    if (!raw) return null;
    return JSON.parse(raw) as ReadingMaterialResponse;
  } catch {
    return null;
  }
}

function writeCachedVersion(payload: ReadingMaterialResponse) {
  const safeVersionId = String(payload.version_id || '').trim();
  if (!safeVersionId) return;
  try {
    localStorage.setItem(`${VERSION_CACHE_PREFIX}${safeVersionId}`, JSON.stringify(payload));
  } catch {
    // ignore
  }
}

function sortHistoryRecords(rows: HistoryRecord[]) {
  return [...rows].sort((left, right) => Number(right.timestamp || 0) - Number(left.timestamp || 0));
}

function getErrorMessage(error: unknown, fallback: string) {
  if (error instanceof ApiRequestError) return error.message || fallback;
  if (error instanceof Error) return error.message || fallback;
  return fallback;
}

function getReadingGenerateErrorMessage(error: unknown) {
  if (error instanceof ApiRequestError) {
    const payload = error.payload as { detail?: unknown } | null;
    const detail = payload && typeof payload === 'object' ? payload.detail : null;
    const detailCode = detail && typeof detail === 'object' ? String((detail as { code?: unknown }).code || '').trim() : '';
    if (detailCode === 'reading_llm_required') {
      return '系统模型通道异常，请稍后重试或联系管理员。';
    }
    if (detailCode === 'reading_generation_quality_failed' || detailCode === 'reading_quiz_generation_failed') {
      return '已自动重试 2 轮仍未达标，可更换模型或稍后重试。';
    }
  }
  return getErrorMessage(error, '生成阅读内容失败');
}

function formatSubmitTime(value: number) {
  const safe = Number(value || 0);
  if (!safe) return '-';
  try {
    return new Date(safe).toLocaleString();
  } catch {
    return '-';
  }
}

type ReadingConfigState = {
  scope: ReadingScope;
  ratio_preset: ReadingRatioPreset;
  difficulty_tier: ReadingDifficultyTier;
  genre: ReadingGenre;
  force_regenerate: boolean;
};

const DEFAULT_CONFIG: ReadingConfigState = {
  scope: 'all',
  ratio_preset: 'long_term',
  difficulty_tier: 'balanced',
  genre: 'news',
  force_regenerate: false
};

export function ReadingPage() {
  const location = useLocation();
  const { profile, error: profileError } = useProfileSettings();
  const [historyRows, setHistoryRows] = useState<HistoryRecord[]>([]);
  const [sourceRows, setSourceRows] = useState<ReadingSourceSummary[]>([]);
  const [readingHistory, setReadingHistory] = useState<ReadingHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState('');
  const [versionLoading, setVersionLoading] = useState(false);
  const [versionError, setVersionError] = useState('');
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState('');
  const [selectedSourceKey, setSelectedSourceKey] = useState('');
  const [selectedVersionId, setSelectedVersionId] = useState('');
  const [selectedVersion, setSelectedVersion] = useState<ReadingMaterialResponse | null>(null);
  const [userLevel, setUserLevel] = useState<string>('cet4');
  const [userLevelTouched, setUserLevelTouched] = useState(false);
  const [config, setConfig] = useState<ReadingConfigState>(DEFAULT_CONFIG);
  const [activeMaterialKind, setActiveMaterialKind] = useState<ReadingMaterialKind>('intensive');
  const [expandedAnswers, setExpandedAnswers] = useState<Record<string, boolean>>({});
  const [generateDialogOpen, setGenerateDialogOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ReadingHistoryItem | null>(null);
  const [deleteShortAnswerTargetQuestionId, setDeleteShortAnswerTargetQuestionId] = useState('');
  const [historyActionBusy, setHistoryActionBusy] = useState('');

  const [shortAnswerInputMap, setShortAnswerInputMap] = useState<Record<string, string>>({});
  const [shortAnswerLatestMap, setShortAnswerLatestMap] = useState<Record<string, ReadingShortAnswerHistoryItem>>({});
  const [shortAnswerHistoryMap, setShortAnswerHistoryMap] = useState<Record<string, ReadingShortAnswerHistoryItem[]>>({});
  const [shortAnswerLoadingMap, setShortAnswerLoadingMap] = useState<Record<string, boolean>>({});

  const sourceKeySet = useMemo(() => new Set(sourceRows.map((item) => toSourceKey(item.video_name, item.srt_name))), [sourceRows]);
  const availableHistory = useMemo(
    () => sortHistoryRecords(historyRows.filter((row) => sourceKeySet.has(toSourceKey(row.videoName, row.srtName)))),
    [historyRows, sourceKeySet]
  );
  const preferredSourceKey = useMemo(() => {
    const query = new URLSearchParams(location.search || '');
    const video = String(query.get('video') || '').trim();
    const srt = String(query.get('srt') || '').trim();
    if (!video || !srt) return '';
    return toSourceKey(video, srt);
  }, [location.search]);
  const selectedRecord = useMemo(
    () => availableHistory.find((item) => toSourceKey(item.videoName, item.srtName) === selectedSourceKey) || null,
    [availableHistory, selectedSourceKey]
  );
  const activeMaterial = useMemo(
    () => (selectedVersion?.materials || []).find((item) => item.kind === activeMaterialKind) || null,
    [selectedVersion, activeMaterialKind]
  );
  const deleteShortAnswerQuestionText = useMemo(() => {
    const safeQuestionId = String(deleteShortAnswerTargetQuestionId || '').trim();
    if (!safeQuestionId) return '';
    const matched = (selectedVersion?.quiz.short_questions || []).find((item) => String(item.question_id || '').trim() === safeQuestionId);
    return String(matched?.question || '').trim();
  }, [deleteShortAnswerTargetQuestionId, selectedVersion?.quiz.short_questions]);
  const deleteShortAnswerBusy = Boolean(shortAnswerLoadingMap[deleteShortAnswerTargetQuestionId]);
  const refreshHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const [recordsPayload, sourcesPayload, versionsPayload] = await Promise.all([
        fetchHistoryRecords(),
        fetchReadingSources(),
        fetchReadingHistory(20, 0)
      ]);
      setHistoryRows(sortHistoryRecords(recordsPayload.records || []));
      setSourceRows(sourcesPayload.sources || []);
      setReadingHistory(versionsPayload.items || []);
    } catch (error) {
      setHistoryError(getErrorMessage(error, '读取阅读历史失败'));
      setHistoryRows([]);
      setSourceRows([]);
      setReadingHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const applyVersionToView = useCallback((version: ReadingMaterialResponse) => {
    setSelectedVersion(version);
    setSelectedVersionId(version.version_id);
    setConfig({
      scope: version.scope,
      ratio_preset: version.ratio_preset,
      difficulty_tier: version.difficulty_tier,
      genre: version.genre,
      force_regenerate: false
    });
    setUserLevel(String(version.user_level || 'cet4').trim().toLowerCase() || 'cet4');
    const nextKind: ReadingMaterialKind = version.materials.some((item) => item.kind === 'intensive' && item.generated) ? 'intensive' : 'extensive';
    setActiveMaterialKind(nextKind);
  }, []);

  const loadVersion = useCallback(
    async (versionId: string) => {
      const safeVersionId = String(versionId || '').trim();
      if (!safeVersionId) return;
      setVersionLoading(true);
      setVersionError('');

      const cached = readCachedVersion(safeVersionId);
      if (cached) {
        applyVersionToView(cached);
      }

      try {
        const payload = await fetchReadingVersion(safeVersionId);
        const normalized: ReadingMaterialResponse = { ...payload.version, cached: true };
        applyVersionToView(normalized);
        writeCachedVersion(normalized);
      } catch (error) {
        if (!cached) {
          setSelectedVersion(null);
        }
        setVersionError(getErrorMessage(error, '读取历史版本失败'));
      } finally {
        setVersionLoading(false);
      }
    },
    [applyVersionToView]
  );

  const loadShortAnswerHistory = useCallback(async (questionId: string) => {
    const safeQuestionId = String(questionId || '').trim();
    const safeVersionId = String(selectedVersion?.version_id || '').trim();
    if (!safeVersionId || !safeQuestionId) return;
    setShortAnswerLoadingMap((prev) => ({ ...prev, [safeQuestionId]: true }));
    try {
      const payload = await fetchReadingShortAnswerHistory({
        version_id: safeVersionId,
        question_id: safeQuestionId,
        limit: 20
      });
      const rows = payload.items || [];
      setShortAnswerHistoryMap((prev) => ({ ...prev, [safeQuestionId]: rows }));
      if (rows[0]) {
        setShortAnswerLatestMap((prev) => ({ ...prev, [safeQuestionId]: rows[0] }));
      }
    } catch (error) {
      toast.error(getErrorMessage(error, '读取简答历史失败'));
    } finally {
      setShortAnswerLoadingMap((prev) => ({ ...prev, [safeQuestionId]: false }));
    }
  }, [selectedVersion?.version_id]);

  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory]);

  useEffect(() => {
    if (!userLevelTouched) {
      setUserLevel(profile.english_level);
    }
  }, [profile.english_level, userLevelTouched]);

  useEffect(() => {
    if (availableHistory.length === 0) {
      setSelectedSourceKey('');
      return;
    }
    if (preferredSourceKey && availableHistory.some((item) => toSourceKey(item.videoName, item.srtName) === preferredSourceKey)) {
      setSelectedSourceKey(preferredSourceKey);
      return;
    }
    if (!selectedSourceKey || !availableHistory.some((item) => toSourceKey(item.videoName, item.srtName) === selectedSourceKey)) {
      setSelectedSourceKey(toSourceKey(availableHistory[0].videoName, availableHistory[0].srtName));
    }
  }, [availableHistory, preferredSourceKey, selectedSourceKey]);

  useEffect(() => {
    if (!selectedVersionId) {
      if (readingHistory.length > 0) {
        setSelectedVersionId(readingHistory[0].version_id);
      }
      return;
    }
    void loadVersion(selectedVersionId);
  }, [readingHistory, selectedVersionId, loadVersion]);

  useEffect(() => {
    const shortQuestions = selectedVersion?.quiz?.short_questions || [];
    if (!selectedVersion || shortQuestions.length === 0) {
      setShortAnswerInputMap({});
      setShortAnswerLatestMap({});
      setShortAnswerHistoryMap({});
      setShortAnswerLoadingMap({});
      return;
    }
    setShortAnswerInputMap({});
    setShortAnswerLatestMap({});
    setShortAnswerHistoryMap({});
    setShortAnswerLoadingMap({});
    shortQuestions.forEach((item) => {
      if (item.question_id) {
        void loadShortAnswerHistory(item.question_id);
      }
    });
  }, [selectedVersion?.version_id, selectedVersion?.quiz?.short_questions, loadShortAnswerHistory]);
  const handleGenerate = async () => {
    if (!selectedRecord) {
      setGenerateError('请先选择素材来源');
      return;
    }

    setGenerating(true);
    setGenerateError('');
    try {
      const generated = await generateReadingMaterial({
        video_name: selectedRecord.videoName,
        srt_name: selectedRecord.srtName,
        user_level: userLevel,
        scope: config.scope,
        ratio_preset: config.ratio_preset,
        difficulty_tier: config.difficulty_tier,
        genre: config.genre,
        force_regenerate: config.force_regenerate,
        llm: selectLlmOptions(profile, 'reading')
      });
      applyVersionToView(generated);
      writeCachedVersion(generated);
      await refreshHistory();
      setGenerateDialogOpen(false);
    } catch (error) {
      setGenerateError(getReadingGenerateErrorMessage(error));
    } finally {
      setGenerating(false);
    }
  };

  const handleOpenGenerateFromHistory = (item: ReadingHistoryItem) => {
    setSelectedSourceKey(toSourceKey(item.video_name, item.srt_name));
    setConfig({
      scope: item.scope,
      ratio_preset: item.ratio_preset,
      difficulty_tier: item.difficulty_tier,
      genre: item.genre,
      force_regenerate: true
    });
    setGenerateDialogOpen(true);
  };

  const handleSelectHistoryVersion = (item: ReadingHistoryItem) => {
    setSelectedVersionId(item.version_id);
    setSelectedSourceKey(toSourceKey(item.video_name, item.srt_name));
  };

  const handleDeleteVersion = async () => {
    const target = deleteTarget;
    if (!target) return;
    setHistoryActionBusy(target.version_id);
    try {
      await deleteReadingVersion(target.version_id);
      setDeleteTarget(null);
      if (target.version_id === selectedVersionId) {
        setSelectedVersion(null);
        setSelectedVersionId('');
      }
      await refreshHistory();
      toast.success('版本已删除');
    } catch (error) {
      toast.error(getErrorMessage(error, '删除版本失败'));
    } finally {
      setHistoryActionBusy('');
    }
  };

  const toggleShortAnswer = (questionId: string) => {
    setExpandedAnswers((prev) => ({ ...prev, [questionId]: !prev[questionId] }));
  };

  const handleCopyAnswer = async (text: string) => {
    const value = String(text || '').trim();
    if (!value) return;
    if (!navigator.clipboard?.writeText) return;
    try {
      await navigator.clipboard.writeText(value);
      toast.success('参考答案已复制');
    } catch {
      toast.error('复制失败，请手动复制');
    }
  };

  const handleSubmitShortAnswer = async (questionId: string) => {
    const safeVersionId = String(selectedVersion?.version_id || '').trim();
    const safeQuestionId = String(questionId || '').trim();
    if (!safeVersionId || !safeQuestionId) return;
    const answer = String(shortAnswerInputMap[safeQuestionId] || '').trim();
    if (!answer) {
      toast.warning('请先输入简答内容');
      return;
    }
    setShortAnswerLoadingMap((prev) => ({ ...prev, [safeQuestionId]: true }));
    try {
      const payload = await submitReadingShortAnswer({
        version_id: safeVersionId,
        question_id: safeQuestionId,
        answer_text: answer
      });
      setShortAnswerLatestMap((prev) => ({ ...prev, [safeQuestionId]: payload }));
      setShortAnswerInputMap((prev) => ({ ...prev, [safeQuestionId]: '' }));
      await loadShortAnswerHistory(safeQuestionId);
      toast.success('简答评分已更新');
    } catch (error) {
      toast.error(getErrorMessage(error, '简答提交失败'));
    } finally {
      setShortAnswerLoadingMap((prev) => ({ ...prev, [safeQuestionId]: false }));
    }
  };

  const handleOpenDeleteShortAnswerDialog = (questionId: string) => {
    const safeQuestionId = String(questionId || '').trim();
    if (!safeQuestionId) return;
    setDeleteShortAnswerTargetQuestionId(safeQuestionId);
  };

  const handleDeleteShortAnswerGroup = async () => {
    const safeVersionId = String(selectedVersion?.version_id || '').trim();
    const safeQuestionId = String(deleteShortAnswerTargetQuestionId || '').trim();
    if (!safeVersionId || !safeQuestionId) return;
    setShortAnswerLoadingMap((prev) => ({ ...prev, [safeQuestionId]: true }));
    try {
      await deleteReadingShortAnswerGroup({ version_id: safeVersionId, question_id: safeQuestionId });
      setShortAnswerHistoryMap((prev) => ({ ...prev, [safeQuestionId]: [] }));
      setShortAnswerLatestMap((prev) => {
        const next = { ...prev };
        delete next[safeQuestionId];
        return next;
      });
      setDeleteShortAnswerTargetQuestionId('');
      toast.success('该题历史已清空');
    } catch (error) {
      toast.error(getErrorMessage(error, '删除简答历史失败'));
    } finally {
      setShortAnswerLoadingMap((prev) => ({ ...prev, [safeQuestionId]: false }));
    }
  };

  return (
    <div className="page-reading fade-in">
      <div className="reading-v2__layout">
        <Card className="reading-v2__history">
          <CardHeader
            title="历史版本"
            subtitle={historyLoading ? '加载中...' : `${readingHistory.length} 条版本`}
            action={<Badge tone={historyLoading ? 'warning' : 'default'}>{historyLoading ? '加载中' : '已就绪'}</Badge>}
          />
          <CardBody className="reading-v2__history-list">
            {historyError ? <TypographyP className="error-text">{historyError}</TypographyP> : null}
            <Button type="button" variant="secondary" size="sm" icon={<RefreshCw size={14} strokeWidth={1.8} />} onClick={() => void refreshHistory()} disabled={historyLoading}>
              刷新历史
            </Button>
            {(readingHistory || []).map((item) => {
              const sourceName = availableHistory.find((row) => toSourceKey(row.videoName, row.srtName) === toSourceKey(item.video_name, item.srt_name));
              const active = item.version_id === selectedVersionId;
              return (
                <article
                  key={item.version_id}
                  className={`reading-v2__history-item ${active ? 'is-active' : ''}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => handleSelectHistoryVersion(item)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      handleSelectHistoryVersion(item);
                    }
                  }}
                >
                  <div className="reading-v2__history-title">{sourceName?.displayName || item.video_name}</div>
                  <div className="reading-v2__history-meta">
                    <Badge tone="default">{item.scope}</Badge>
                    <Badge tone="default">{item.difficulty_tier}</Badge>
                    <Badge tone={item.i_plus_one_hit ? 'success' : 'warning'}>{item.i_plus_one_hit ? 'i+1 命中' : 'i+1 未命中'}</Badge>
                    <Badge tone={item.has_intensive ? 'info' : 'default'}>精</Badge>
                    <Badge tone={item.has_extensive ? 'info' : 'default'}>泛</Badge>
                  </div>
                  <div className="reading-v2__history-actions">
                    <Button size="sm" type="button" onClick={(event) => { event.stopPropagation(); handleSelectHistoryVersion(item); }}>
                      打开版本
                    </Button>
                    <Button size="sm" variant="secondary" type="button" onClick={(event) => { event.stopPropagation(); handleOpenGenerateFromHistory(item); }}>
                      重新生成阅读包
                    </Button>
                    <Button
                      size="sm"
                      type="button"
                      variant="destructive"
                      disabled={historyActionBusy === item.version_id}
                      onClick={(event) => {
                        event.stopPropagation();
                        setDeleteTarget(item);
                      }}
                    >
                      删除版本
                    </Button>
                  </div>
                </article>
              );
            })}
            {!historyLoading && readingHistory.length === 0 ? <TypographyMuted>暂无历史版本。</TypographyMuted> : null}
          </CardBody>
        </Card>
        <div className="reading-v2__main">
          <Card>
            <CardHeader
              title="生成配置"
              subtitle="默认英语等级来自个人中心，点击按钮后在弹窗确认生成参数。"
              action={<Badge>{`等级 ${profile.english_level}`}</Badge>}
            />
            <CardBody className="reading-v2__config-grid">
              {profileError ? <TypographyP className="error-text">{profileError}</TypographyP> : null}
              <div>
                <Label htmlFor="readingSourceSelect">素材来源</Label>
                <Select
                  id="readingSourceSelect"
                  value={selectedSourceKey}
                  onChange={(event) => setSelectedSourceKey(event.target.value)}
                  disabled={availableHistory.length === 0}
                >
                  {availableHistory.map((row) => (
                    <option key={toSourceKey(row.videoName, row.srtName)} value={toSourceKey(row.videoName, row.srtName)}>
                      {row.displayName || row.videoName}
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <Label htmlFor="readingUserLevel">学习等级（默认来自个人中心）</Label>
                <Select
                  id="readingUserLevel"
                  value={userLevel}
                  onChange={(event) => {
                    setUserLevel(event.target.value);
                    setUserLevelTouched(true);
                  }}
                >
                  {LEVEL_OPTIONS.map((item) => (
                    <option key={item.value} value={item.value}>{item.label}</option>
                  ))}
                </Select>
              </div>
              <div className="reading-v2__config-actions">
                <Button type="button" onClick={() => setGenerateDialogOpen(true)} disabled={!selectedRecord || generating} icon={<BookCheck size={16} strokeWidth={1.8} />}>
                  {generating ? '生成中...' : '生成阅读包'}
                </Button>
              </div>
              {generateError ? <TypographyP className="error-text">{generateError}</TypographyP> : null}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="阅读内容"
              subtitle={selectedVersion ? `${selectedVersion.video_name} · ${selectedVersion.version_id.slice(0, 8)}` : '请选择历史版本或先生成'}
              action={<Badge tone={selectedVersion?.cached ? 'default' : 'success'}>{selectedVersion?.cached ? '缓存' : '最新生成'}</Badge>}
            />
            <CardBody className="reading-v2__tabs">
              {versionLoading ? <TypographyMuted>加载版本中...</TypographyMuted> : null}
              {versionError ? <TypographyP className="error-text">{versionError}</TypographyP> : null}
              {!selectedVersion ? <TypographyMuted>暂无阅读内容。</TypographyMuted> : null}
              {selectedVersion ? (
                <>
                  <div className="reading-v2__tab-buttons">
                    <Button type="button" variant={activeMaterialKind === 'intensive' ? 'default' : 'outline'} onClick={() => setActiveMaterialKind('intensive')}>
                      精读
                    </Button>
                    <Button type="button" variant={activeMaterialKind === 'extensive' ? 'default' : 'outline'} onClick={() => setActiveMaterialKind('extensive')}>
                      泛读
                    </Button>
                  </div>
                  <Card className="reading-v2__material-card">
                    <CardHeader
                      title={`${activeMaterialKind === 'intensive' ? '精读' : '泛读'}文本`}
                      subtitle={`词数 ${activeMaterial?.word_count || 0} / 目标 ${activeMaterial?.target_word_count || 0}`}
                      action={<Badge tone={activeMaterial?.generated ? 'success' : 'warning'}>{activeMaterial?.generated ? '已生成' : '未生成'}</Badge>}
                    />
                    <CardBody>
                      <TypographyP className="reading-v2__rewrite-text">{activeMaterial?.text || '当前模式暂无文本。请在历史区点击“重新生成阅读包”。'}</TypographyP>
                    </CardBody>
                  </Card>

                  <Card className="reading-v2__difficulty-card">
                    <CardHeader title="难度报告" subtitle="自动评估生成内容与用户等级的匹配度。" />
                    <CardBody>
                      <TypographySmall>word_budget_total: {selectedVersion.config.word_budget_total}</TypographySmall>
                      <TypographySmall>source_score: {selectedVersion.difficulty_report.source_score}</TypographySmall>
                      <TypographySmall>generated_level: {selectedVersion.difficulty_report.generated_level}</TypographySmall>
                      <TypographySmall>target_level: {selectedVersion.difficulty_report.target_level}</TypographySmall>
                      <TypographySmall>gap_to_user: {selectedVersion.difficulty_report.gap_to_user}</TypographySmall>
                      <TypographySmall>recommended_ratio_preset: {selectedVersion.difficulty_report.recommended_ratio_preset}</TypographySmall>
                      <TypographySmall>hit_i_plus_one: {selectedVersion.difficulty_report.hit_i_plus_one ? 'true' : 'false'}</TypographySmall>
                    </CardBody>
                  </Card>

                  <Card>
                    <CardHeader title="Quiz" subtitle="选择题与简答题。" />
                    <CardBody className="reading-v2__quiz-wrap">
                      <div className="reading-v2__quiz-choice">
                        {(selectedVersion.quiz.choice_questions || []).map((item, index) => (
                          <article key={item.question_id || `${index}`} className="reading-v2__quiz-choice-item">
                            <TypographySmall>Q{index + 1}. {item.question}</TypographySmall>
                            <ul>
                              {(item.choices || []).map((choice, choiceIndex) => (
                                <li key={`${item.question_id}-${choiceIndex}`}>{String.fromCharCode(65 + choiceIndex)}. {choice}</li>
                              ))}
                            </ul>
                            <TypographyMuted>答案：{String.fromCharCode(65 + Math.max(0, Number(item.answer_index || 0)))}</TypographyMuted>
                          </article>
                        ))}
                      </div>
                      <div className="reading-v2__quiz-short">
                        {(selectedVersion.quiz.short_questions || []).map((item) => {
                          const safeQuestionId = String(item.question_id || '').trim();
                          const expanded = Boolean(expandedAnswers[safeQuestionId]);
                          const latest = shortAnswerLatestMap[safeQuestionId];
                          const historyRowsForQuestion = shortAnswerHistoryMap[safeQuestionId] || [];
                          const submitting = Boolean(shortAnswerLoadingMap[safeQuestionId]);
                          return (
                            <article key={safeQuestionId} className="reading-v2__quiz-short-item">
                              <TypographySmall>{item.question}</TypographySmall>
                              <Textarea
                                value={shortAnswerInputMap[safeQuestionId] || ''}
                                onChange={(event) => setShortAnswerInputMap((prev) => ({ ...prev, [safeQuestionId]: event.target.value }))}
                                rows={4}
                                placeholder="输入你的简答内容..."
                              />
                              <div className="reading-v2__short-actions">
                                <Button type="button" size="sm" disabled={submitting} onClick={() => void handleSubmitShortAnswer(safeQuestionId)}>
                                  {submitting ? '提交中...' : '提交评分'}
                                </Button>
                                <Button type="button" size="sm" variant="outline" onClick={() => toggleShortAnswer(safeQuestionId)}>
                                  {expanded ? '收起详情' : '展开详情'}
                                </Button>
                                <Button type="button" size="sm" variant="outline" onClick={() => void loadShortAnswerHistory(safeQuestionId)}>
                                  刷新历史
                                </Button>
                                <Button type="button" size="sm" variant="outline" icon={<Copy size={14} strokeWidth={1.8} />} onClick={() => void handleCopyAnswer(item.reference_answer)}>
                                  复制参考答案
                                </Button>
                                <Button type="button" size="sm" variant="destructive" icon={<Trash2 size={14} strokeWidth={1.8} />} disabled={submitting} onClick={() => handleOpenDeleteShortAnswerDialog(safeQuestionId)}>
                                  删除本题历史
                                </Button>
                              </div>
                              {latest ? (
                                <div className="reading-v2__short-result">
                                  <TypographySmall>最近得分：{latest.total_score} / {latest.max_score}</TypographySmall>
                                  <TypographyMuted>{latest.overall_comment}</TypographyMuted>
                                  <div className="reading-v2__short-dimensions">
                                    {(latest.dimensions || []).map((dimension, index) => (
                                      <TypographySmall key={`${safeQuestionId}-d-${index}`}>{dimension.name}: {dimension.score}/{dimension.max_score}</TypographySmall>
                                    ))}
                                  </div>
                                </div>
                              ) : null}
                              {expanded ? (
                                <div className="reading-v2__short-expanded">
                                  <TypographyP>{item.reference_answer || '暂无参考答案'}</TypographyP>
                                  <div className="reading-v2__short-history">
                                    {historyRowsForQuestion.length === 0 ? <TypographyMuted>暂无作答历史。</TypographyMuted> : null}
                                    {historyRowsForQuestion.map((historyItem) => (
                                      <div key={historyItem.attempt_id} className="reading-v2__short-history-item">
                                        <TypographySmall>{formatSubmitTime(historyItem.submitted_at)}</TypographySmall>
                                        <TypographySmall>{historyItem.total_score}/{historyItem.max_score}</TypographySmall>
                                        <TypographyMuted>{historyItem.answer_text}</TypographyMuted>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              ) : null}
                            </article>
                          );
                        })}
                      </div>
                    </CardBody>
                  </Card>
                </>
              ) : null}
            </CardBody>
          </Card>
        </div>
      </div>

      <Drawer open={generateDialogOpen} onOpenChange={setGenerateDialogOpen} direction="right">
        <DrawerContent>
          <DrawerHeader>
            <DrawerTitle>生成参数确认</DrawerTitle>
            <DrawerDescription>确认本次生成范围与参数后开始生成。</DrawerDescription>
          </DrawerHeader>
          <div className="reading-v2__dialog-grid">
            <TypographyMuted>智能重生成已启用：当模型策略或质量策略变化时会自动绕过缓存。</TypographyMuted>
            <div>
              <Label htmlFor="readingScope">范围</Label>
              <Select id="readingScope" value={config.scope} onChange={(event) => setConfig((prev) => ({ ...prev, scope: event.target.value as ReadingScope }))}>
                {SCOPE_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="readingRatio">精泛读比例</Label>
              <Select id="readingRatio" value={config.ratio_preset} onChange={(event) => setConfig((prev) => ({ ...prev, ratio_preset: event.target.value as ReadingRatioPreset }))}>
                {RATIO_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="readingDifficulty">难度</Label>
              <Select id="readingDifficulty" value={config.difficulty_tier} onChange={(event) => setConfig((prev) => ({ ...prev, difficulty_tier: event.target.value as ReadingDifficultyTier }))}>
                {DIFFICULTY_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="readingGenre">体裁</Label>
              <Select id="readingGenre" value={config.genre} onChange={(event) => setConfig((prev) => ({ ...prev, genre: event.target.value as ReadingGenre }))}>
                {GENRE_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </Select>
            </div>
            <label className="reading-v2__force-row" htmlFor="readingForceRegenerate">
              <Checkbox
                id="readingForceRegenerate"
                checked={config.force_regenerate}
                onCheckedChange={(checked) => setConfig((prev) => ({ ...prev, force_regenerate: checked === true }))}
              />
              <span>强制重新生成</span>
            </label>
          </div>
          <DrawerFooter>
            <div className="reading-v2__dialog-actions">
              <Button type="button" variant="outline" onClick={() => setGenerateDialogOpen(false)}>取消</Button>
              <Button type="button" onClick={() => void handleGenerate()} disabled={!selectedRecord || generating}>
                {generating ? '生成中...' : '确认生成'}
              </Button>
            </div>
          </DrawerFooter>
        </DrawerContent>
      </Drawer>

      <AlertDialog open={Boolean(deleteShortAnswerTargetQuestionId)} onOpenChange={(open) => { if (!open) setDeleteShortAnswerTargetQuestionId(''); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除本题简答历史</AlertDialogTitle>
            <AlertDialogDescription>将删除该题全部作答历史，删除后不可恢复。</AlertDialogDescription>
          </AlertDialogHeader>
          <div className="reading-v2__dialog-grid">
            <TypographyP>确认删除：{deleteShortAnswerQuestionText || deleteShortAnswerTargetQuestionId}</TypographyP>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setDeleteShortAnswerTargetQuestionId('')}>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void handleDeleteShortAnswerGroup()} disabled={deleteShortAnswerBusy}>
              {deleteShortAnswerBusy ? '删除中...' : '确认删除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除阅读版本</AlertDialogTitle>
            <AlertDialogDescription>仅删除该版本及其简答历史，其他版本不受影响。</AlertDialogDescription>
          </AlertDialogHeader>
          <div className="reading-v2__dialog-grid">
            <TypographyP>确认删除版本：{deleteTarget?.version_id?.slice(0, 8)}</TypographyP>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setDeleteTarget(null)}>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void handleDeleteVersion()} disabled={historyActionBusy === deleteTarget?.version_id}>
              {historyActionBusy === deleteTarget?.version_id ? '删除中...' : '确认删除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
