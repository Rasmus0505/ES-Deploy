import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardBody, CardHeader } from '../../components/ui/card';
import { HoverExplain } from '../../components/ui/hover-explain';
import { Kbd, KbdGroup } from '../../components/ui/kbd';
import { Progress } from '../../components/ui/progress';
import { TypographyLarge, TypographyMuted, TypographyP, TypographySmall } from '../../components/ui/typography';
import {
  type ListeningShortcutAction,
  type ListeningShortcutBinding,
  type ListeningShortcutBindings,
  type ListeningLetterFeedbackMode,
  type ParsedSubtitleItem,
  type PracticeStatus,
  getLegacyFileAsFile,
  getLegacyPracticeProgress,
  getDefaultListeningShortcutBindings,
  getListeningIndependentThreshold,
  getListeningRevealGapThreshold,
  getListeningLetterFeedbackMode,
  getListeningShortcutBindings,
  parseSrtText,
  persistLegacyPracticeProgress,
  setListeningLetterFeedbackMode,
  setListeningShortcutBindings
} from '../../lib/storage/compat';
import { clamp, cn, toNumber } from '../../lib/utils';

type WordState = 'idle' | 'correct' | 'wrong' | 'revealed';
type LetterState = 'neutral' | 'correct' | 'wrong';
type SoundType = 'typing' | 'wordCorrect' | 'sentenceComplete';

type SoundPoolItem = {
  src: string;
  volume: number;
  pool: HTMLAudioElement[];
};

const WORD_PATTERN = /[A-Za-z0-9']+/g;
const IMMERSIVE_BODY_CLASS = 'listening-immersive-active';
const SPACE_DEBOUNCE_MS = 220;
const PREVIOUS_SUBTITLE_EMPTY_TEXT = '暂无上一句原字幕';
const PREVIOUS_TRANSLATION_EMPTY_TEXT = '暂无翻译';
const IS_APPLE_PLATFORM = typeof navigator !== 'undefined'
  && /Mac|iPhone|iPad|iPod/i.test(String(navigator.platform || navigator.userAgent || ''));
const SHORTCUT_EDIT_BLOCKED_CODES = new Set([
  'Enter',
  'NumpadEnter',
  'Tab',
  'Escape',
  'Backspace'
]);
const SHORTCUT_MODIFIER_ONLY_CODES = new Set([
  'ShiftLeft',
  'ShiftRight',
  'ControlLeft',
  'ControlRight',
  'AltLeft',
  'AltRight',
  'MetaLeft',
  'MetaRight'
]);
const SHORTCUT_ACTIONS: ListeningShortcutAction[] = ['progress', 'replay', 'reveal', 'immersive'];
const SHORTCUT_ACTION_LABELS: Record<ListeningShortcutAction, string> = {
  progress: '推进/切句',
  replay: '重播本句',
  reveal: '揭示当前词',
  immersive: '沉浸模式切换'
};
const SHORTCUT_ACTION_DESCRIPTIONS: Record<ListeningShortcutAction, string> = {
  progress: '进入下一句；当你接近目标时，会优先揭示当前词。',
  replay: '把当前句再播放一次，方便反复听。',
  reveal: '直接显示当前卡住的词，帮你继续推进。',
  immersive: '切换到更专注的沉浸界面。'
};

const SOUND_SOURCES: Record<SoundType, { paths: string[]; volume: number; copies: number }> = {
  typing: {
    paths: [
      '/audio/typing1.mp3',
      '/audio/typing2.mp3',
      '/audio/typing3.mp3',
      '/audio/typing4.mp3',
      '/audio/typing5.mp3',
      '/audio/typing6.mp3',
      '/audio/typing7.mp3',
      '/audio/typing8.mp3'
    ],
    volume: 0.52,
    copies: 2
  },
  wordCorrect: {
    paths: ['/audio/word1.mp3', '/audio/word2.mp3'],
    volume: 0.74,
    copies: 3
  },
  sentenceComplete: {
    paths: ['/audio/sentence1.mp3', '/audio/sentence2.mp3'],
    volume: 0.78,
    copies: 2
  }
};

function extractWords(sentence: string) {
  const matches = String(sentence || '').match(WORD_PATTERN);
  return Array.isArray(matches) ? matches : [];
}

function normalizeWord(value: string) {
  return String(value || '')
    .toLowerCase()
    .replace(/[’']/g, '')
    .replace(/[^a-z0-9]/g, '')
    .trim();
}

function isApostropheChar(value: string) {
  return value === '\'' || value === '’';
}

function normalizePracticeStatus(value: string): PracticeStatus {
  const raw = String(value || '').toUpperCase();
  if (raw === 'PLAYING') return 'PLAYING';
  if (raw === 'WAITING_INPUT') return 'WAITING_INPUT';
  if (raw === 'NEAR_MATCH') return 'WAITING_INPUT';
  if (raw === 'AUTO_NEXT' || raw === 'AUTO_NEXT_COUNTDOWN') return 'AUTO_NEXT';
  return 'IDLE';
}

function resolveStatusTone(status: PracticeStatus, awaitingSpaceForNext: boolean) {
  if (status === 'PLAYING') return 'info';
  if (status === 'AUTO_NEXT') return 'warning';
  if (awaitingSpaceForNext) return 'warning';
  return 'success';
}

function createAudioElement(src: string, volume: number) {
  const audio = new Audio(src);
  audio.volume = volume;
  audio.preload = 'auto';
  return audio;
}

function createSoundPool(paths: string[], volume: number, copies: number) {
  return paths.map((src) => ({
    src,
    volume,
    pool: Array.from({ length: copies }, () => createAudioElement(src, volume))
  }));
}

function resolveTypedCharState(expectedChar: string, typedChar: string | undefined): LetterState {
  if (!typedChar) return 'neutral';
  if (isApostropheChar(typedChar)) {
    return isApostropheChar(expectedChar) ? 'correct' : 'wrong';
  }

  const normalizedExpected = normalizeWord(expectedChar);
  const normalizedTyped = normalizeWord(typedChar);
  if (!normalizedExpected) return 'wrong';
  if (!normalizedExpected || !normalizedTyped) {
    return expectedChar.toLowerCase() === typedChar.toLowerCase() ? 'correct' : 'wrong';
  }
  return normalizedExpected === normalizedTyped ? 'correct' : 'wrong';
}

function buildTypedCharStates(expectedWord: string, inputValue: string, isComposing: boolean) {
  const expectedChars = Array.from(String(expectedWord || '')).filter((char) => !isApostropheChar(char));
  const typedChars = Array.from(String(inputValue || ''));
  const expectedHasApostrophe = Array.from(String(expectedWord || '')).some((char) => isApostropheChar(char));
  let expectedCursor = 0;

  return typedChars.map((char) => {
    if (isComposing) {
      return {
        char,
        state: 'neutral' as LetterState
      };
    }
    if (isApostropheChar(char)) {
      return {
        char,
        state: expectedHasApostrophe ? 'correct' as LetterState : 'wrong' as LetterState
      };
    }

    const state = resolveTypedCharState(expectedChars[expectedCursor] || '', char);
    expectedCursor += 1;
    return { char, state };
  });
}

function findNextOpenWordIndex(states: WordState[]) {
  return states.findIndex((state) => state !== 'correct' && state !== 'revealed');
}

function normalizeShortcutKeyValue(key: string) {
  return key === ' ' ? ' ' : String(key || '').trim();
}

function toShortcutBinding(event: KeyboardEvent): ListeningShortcutBinding {
  const key = normalizeShortcutKeyValue(String(event.key || ''));
  const code = String(event.code || '').trim();
  return {
    code: code || key,
    key: key || code,
    shiftKey: event.shiftKey,
    ctrlKey: event.ctrlKey,
    altKey: event.altKey,
    metaKey: event.metaKey
  };
}

function isShortcutMatch(event: KeyboardEvent, binding: ListeningShortcutBinding) {
  const eventCode = String(event.code || '').trim().toLowerCase();
  const eventKey = normalizeShortcutKeyValue(String(event.key || '')).toLowerCase();
  const targetCode = String(binding.code || '').trim().toLowerCase();
  const targetKey = normalizeShortcutKeyValue(String(binding.key || '')).toLowerCase();
  const keyMatched = (targetCode && eventCode === targetCode) || (targetKey && eventKey === targetKey);
  if (!keyMatched) return false;
  return event.shiftKey === binding.shiftKey
    && event.ctrlKey === binding.ctrlKey
    && event.altKey === binding.altKey
    && event.metaKey === binding.metaKey;
}

function formatShortcutKeyLabel(binding: ListeningShortcutBinding, isApplePlatform: boolean) {
  const key = normalizeShortcutKeyValue(String(binding.key || ''));
  const code = String(binding.code || '').trim();
  const source = code || key;
  const sourceLower = source.toLowerCase();

  if (key === ' ' || sourceLower === 'space' || sourceLower === 'spacebar') return 'Space';
  if (sourceLower === 'escape') return 'Esc';
  if (/^Key[A-Za-z]$/.test(source)) return source.slice(3).toUpperCase();
  if (/^Digit[0-9]$/.test(source)) return source.slice(5);
  if (/^Numpad[0-9]$/.test(source)) return `Num ${source.slice(6)}`;
  if (sourceLower === 'numpadenter') return 'Num Enter';
  if (sourceLower.startsWith('arrow')) return source.replace(/^Arrow/i, '');
  if (sourceLower === 'meta' || sourceLower === 'metaleft' || sourceLower === 'metaright' || sourceLower === 'os') {
    return isApplePlatform ? '⌘' : 'Win';
  }
  if (!source) return '未设置';
  if (source.length === 1) return source.toUpperCase();
  return source.charAt(0).toUpperCase() + source.slice(1);
}

function formatShortcutParts(binding: ListeningShortcutBinding, isApplePlatform = IS_APPLE_PLATFORM) {
  const parts: string[] = [];
  if (binding.ctrlKey) parts.push(isApplePlatform ? '⌃' : 'Ctrl');
  if (binding.altKey) parts.push(isApplePlatform ? '⌥' : 'Alt');
  if (binding.shiftKey) parts.push(isApplePlatform ? '⇧' : 'Shift');
  if (binding.metaKey) parts.push(isApplePlatform ? '⌘' : 'Win');
  parts.push(formatShortcutKeyLabel(binding, isApplePlatform));
  return parts;
}

function formatShortcutLabel(binding: ListeningShortcutBinding) {
  return formatShortcutParts(binding).join(' + ');
}

function ShortcutKbd({ binding, className }: { binding: ListeningShortcutBinding; className?: string }) {
  const parts = formatShortcutParts(binding);
  return (
    <KbdGroup className={cn('practice-shortcut-kbd', className)} aria-label={formatShortcutLabel(binding)}>
      {parts.map((part, index) => (
        <Fragment key={`${part}-${index}`}>
          {index > 0 ? <span className="ui-kbd-sep" aria-hidden="true">+</span> : null}
          <Kbd>{part}</Kbd>
        </Fragment>
      ))}
    </KbdGroup>
  );
}

function isSameShortcut(left: ListeningShortcutBinding, right: ListeningShortcutBinding) {
  return String(left.code || '').trim().toLowerCase() === String(right.code || '').trim().toLowerCase()
    && normalizeShortcutKeyValue(left.key).toLowerCase() === normalizeShortcutKeyValue(right.key).toLowerCase()
    && left.shiftKey === right.shiftKey
    && left.ctrlKey === right.ctrlKey
    && left.altKey === right.altKey
    && left.metaKey === right.metaKey;
}

function normalizeSubtitleDisplayText(value: unknown) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function normalizeWordLineMap(value: unknown) {
  if (!Array.isArray(value)) return undefined;
  const map = value
    .map((item) => Math.max(1, Math.floor(toNumber(item, 1))))
    .filter((item) => Number.isFinite(item));
  return map.length > 0 ? map : undefined;
}

function normalizeSubtitleRow(raw: unknown, fallbackIndex: number): ParsedSubtitleItem | null {
  if (!isRecord(raw)) return null;

  const text = normalizeSubtitleDisplayText(raw.text);
  if (!text) return null;

  const start = Math.max(0, toNumber(raw.start, 0));
  const endRaw = toNumber(raw.end, start);
  const end = endRaw >= start ? endRaw : start;
  const index = Math.max(0, Math.floor(toNumber(raw.index, fallbackIndex)));
  const id = Math.max(1, Math.floor(toNumber(raw.id, fallbackIndex + 1)));
  const translation = normalizeSubtitleDisplayText(raw.translation);
  const wordLineMap = normalizeWordLineMap(raw.wordLineMap);

  return {
    id,
    start,
    end,
    text,
    translation,
    index,
    wordLineMap
  };
}

function parseSubtitleJsonRows(source: string): ParsedSubtitleItem[] {
  const normalized = String(source || '').trim();
  if (!normalized) return [];

  try {
    const payload = JSON.parse(normalized) as unknown;
    if (!Array.isArray(payload)) return [];
    return payload
      .map((item, index) => normalizeSubtitleRow(item, index))
      .filter(Boolean) as ParsedSubtitleItem[];
  } catch {
    return [];
  }
}

function mergeSubtitleRows(srtRows: ParsedSubtitleItem[], jsonRows: ParsedSubtitleItem[]) {
  if (srtRows.length > 0 && jsonRows.length > 0) {
    return srtRows.map((row, index) => ({
      ...row,
      translation: normalizeSubtitleDisplayText(jsonRows[index]?.translation || '')
    }));
  }
  if (jsonRows.length > 0) return jsonRows;
  return srtRows;
}

export function ListeningPracticePage() {
  const navigate = useNavigate();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const playStopTimerRef = useRef<(() => void) | null>(null);
  const sentenceResolveTimerRef = useRef<number | null>(null);
  const inputRefs = useRef<Array<HTMLInputElement | null>>([]);
  const evaluatedInputRef = useRef<string[]>([]);
  const isComposingRef = useRef(false);
  const queuedAutoPlayIndexRef = useRef<number | null>(null);
  const playbackFlowTokenRef = useRef(0);
  const lastProgressHandledAtRef = useRef(0);
  const soundPoolsRef = useRef<Record<SoundType, SoundPoolItem[]> | null>(null);

  const [videoUrl, setVideoUrl] = useState('');
  const [videoFileName, setVideoFileName] = useState('');
  const [srtFileName, setSrtFileName] = useState('');
  const [subtitles, setSubtitles] = useState<ParsedSubtitleItem[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [status, setStatus] = useState<PracticeStatus>('IDLE');
  const [statusMessage, setStatusMessage] = useState('正在加载练习资源...');
  const [wordInputs, setWordInputs] = useState<string[]>([]);
  const [wordTypedSnapshots, setWordTypedSnapshots] = useState<string[]>([]);
  const [wordStates, setWordStates] = useState<WordState[]>([]);
  const [currentWordIndex, setCurrentWordIndex] = useState(0);
  const [totalAttempts, setTotalAttempts] = useState(0);
  const [correctAttempts, setCorrectAttempts] = useState(0);
  const [immersiveMode, setImmersiveMode] = useState(false);
  const [errorText, setErrorText] = useState('');
  const [ready, setReady] = useState(false);
  const [awaitingSpaceForNext, setAwaitingSpaceForNext] = useState(false);
  const [lastIndependentRate, setLastIndependentRate] = useState<number | null>(null);
  const [letterFeedbackMode, setLetterFeedbackMode] = useState<ListeningLetterFeedbackMode>(() => getListeningLetterFeedbackMode());
  const [composingWordIndex, setComposingWordIndex] = useState<number | null>(null);
  const [shortcutBindings, setShortcutBindingsState] = useState<ListeningShortcutBindings>(() => getListeningShortcutBindings());
  const [editingShortcutAction, setEditingShortcutAction] = useState<ListeningShortcutAction | null>(null);
  const [shortcutHint, setShortcutHint] = useState('');

  const independentThreshold = useMemo(() => getListeningIndependentThreshold(), []);
  const revealGapThreshold = useMemo(() => getListeningRevealGapThreshold(), []);
  const totalSentences = Math.max(1, subtitles.length);
  const sentencePosition = clamp(currentIndex + 1, 1, totalSentences);
  const sentenceProgress = useMemo(
    () => Math.round((sentencePosition / totalSentences) * 100),
    [sentencePosition, totalSentences]
  );
  const sentenceLabel = `第 ${sentencePosition} / ${totalSentences} 句`;
  const currentSubtitle = subtitles[currentIndex] || null;
  const previousSubtitle = useMemo(() => {
    if (currentIndex <= 0) return null;
    return subtitles[currentIndex - 1] || null;
  }, [currentIndex, subtitles]);
  const currentWords = useMemo(() => extractWords(currentSubtitle?.text || ''), [currentSubtitle?.text]);
  const previousSubtitleRawText = useMemo(
    () => normalizeSubtitleDisplayText(previousSubtitle?.text),
    [previousSubtitle?.text]
  );
  const previousTranslationRawText = useMemo(
    () => normalizeSubtitleDisplayText(previousSubtitle?.translation),
    [previousSubtitle?.translation]
  );
  const previousSubtitleText = previousSubtitleRawText || PREVIOUS_SUBTITLE_EMPTY_TEXT;
  const previousSubtitleTranslation = previousTranslationRawText || PREVIOUS_TRANSLATION_EMPTY_TEXT;
  const hasPreviousSubtitleText = Boolean(previousSubtitleRawText);
  const hasPreviousSubtitleTranslation = Boolean(previousTranslationRawText);
  const currentIndependentRate = useMemo(() => {
    const totalWords = Math.max(1, currentWords.length);
    const correctWords = wordStates.filter((state) => state === 'correct').length;
    return Math.round((correctWords / totalWords) * 100);
  }, [currentWords.length, wordStates]);
  const revealThresholdFloor = useMemo(() => (
    clamp(independentThreshold - revealGapThreshold, 0, 100)
  ), [independentThreshold, revealGapThreshold]);
  const currentWordState = wordStates[currentWordIndex] || 'idle';
  const revealEligible = useMemo(() => {
    if (currentWords.length === 0) return false;
    if (currentWordState === 'correct' || currentWordState === 'revealed') return false;
    return currentIndependentRate < independentThreshold && currentIndependentRate >= revealThresholdFloor;
  }, [currentIndependentRate, currentWordState, currentWords.length, independentThreshold, revealThresholdFloor]);
  const progressShortcutLabel = useMemo(
    () => formatShortcutLabel(shortcutBindings.progress),
    [shortcutBindings.progress]
  );

  const clearPlaybackTimer = useCallback(() => {
    const cleanup = playStopTimerRef.current;
    if (!cleanup) return;
    playStopTimerRef.current = null;
    cleanup();
  }, []);

  const clearSentenceResolveTimer = useCallback(() => {
    if (sentenceResolveTimerRef.current) {
      window.clearTimeout(sentenceResolveTimerRef.current);
      sentenceResolveTimerRef.current = null;
    }
  }, []);

  const clearPlaybackFlow = useCallback((reason: string) => {
    clearPlaybackTimer();
    clearSentenceResolveTimer();
    playbackFlowTokenRef.current += 1;
    const video = videoRef.current;
    if (video && !video.paused) video.pause();
  }, [clearPlaybackTimer, clearSentenceResolveTimer]);

  const exitPractice = useCallback(() => {
    if (immersiveMode) {
      setImmersiveMode(false);
      return;
    }
    clearPlaybackFlow('esc-exit-button');
    navigate('/listening');
  }, [clearPlaybackFlow, immersiveMode, navigate]);

  const openLearningHistory = useCallback(() => {
    clearPlaybackFlow('jump-learning-history');
    navigate('/listening#history-records');
  }, [clearPlaybackFlow, navigate]);

  const openReadingMaterial = useCallback(() => {
    clearPlaybackFlow('jump-reading-material');
    const query = new URLSearchParams();
    const safeVideoName = String(videoFileName || '').trim();
    const safeSrtName = String(srtFileName || '').trim();
    if (safeVideoName) query.set('video', safeVideoName);
    if (safeSrtName) query.set('srt', safeSrtName);
    const queryText = query.toString();
    navigate(queryText ? `/reading?${queryText}` : '/reading');
  }, [clearPlaybackFlow, navigate, srtFileName, videoFileName]);

  const focusWordInput = useCallback((index: number) => {
    window.requestAnimationFrame(() => {
      const input = inputRefs.current[index];
      if (input) {
        input.focus({ preventScroll: true });
        const length = input.value.length;
        try {
          input.setSelectionRange(length, length);
        } catch {}
      }
    });
  }, []);

  const playSound = useCallback((type: SoundType) => {
    const soundPools = soundPoolsRef.current?.[type];
    if (!Array.isArray(soundPools) || soundPools.length === 0) return;
    const sound = soundPools[Math.floor(Math.random() * soundPools.length)];
    let audio = sound.pool.find((item) => item.paused || item.ended);
    if (!audio) {
      audio = createAudioElement(sound.src, sound.volume);
      sound.pool.push(audio);
    }

    try {
      audio.currentTime = 0;
      const playPromise = audio.play();
      if (playPromise && typeof playPromise.catch === 'function') {
        playPromise.catch(() => {});
      }
    } catch {}
  }, []);

  const goToSentence = useCallback((index: number, options: { autoPlay?: boolean; reason?: string } = {}) => {
    if (subtitles.length === 0) return;
    const nextIndex = clamp(index, 0, subtitles.length - 1);
    clearPlaybackFlow(options.reason || 'go-to-sentence');
    queuedAutoPlayIndexRef.current = options.autoPlay ? nextIndex : null;

    setCurrentIndex(nextIndex);
    setStatus(options.autoPlay ? 'PLAYING' : 'WAITING_INPUT');
    setAwaitingSpaceForNext(false);
    setLastIndependentRate(null);
    setStatusMessage(options.autoPlay ? '正在进入下一句并播放' : '继续输入当前词');
  }, [clearPlaybackFlow, subtitles.length]);

  const playSubtitleSegment = useCallback(async (
    subtitle: ParsedSubtitleItem,
    options: { onEnded?: () => void } = {}
  ) => {
    const video = videoRef.current;
    if (!video || !subtitle) return;

    clearPlaybackTimer();
    clearSentenceResolveTimer();
    const token = ++playbackFlowTokenRef.current;

    try {
      video.currentTime = Math.max(0, subtitle.start);
      setStatus('PLAYING');
      setStatusMessage('正在播放本句');
      const sentenceEndBoundary = Math.max(subtitle.start, subtitle.end + 0.06);
      let finished = false;
      const finalizeSegment = () => {
        if (finished) return;
        finished = true;
        clearPlaybackTimer();
        if (token !== playbackFlowTokenRef.current) return;
        video.pause();
        if (typeof options.onEnded === 'function') {
          options.onEnded();
          return;
        }
        setStatus((prev) => (prev === 'PLAYING' ? 'WAITING_INPUT' : prev));
        setStatusMessage('继续输入当前词');
      };
      const handleTimeUpdate = () => {
        if (video.currentTime >= sentenceEndBoundary) {
          finalizeSegment();
        }
      };
      const handleEnded = () => {
        finalizeSegment();
      };
      video.addEventListener('timeupdate', handleTimeUpdate);
      video.addEventListener('ended', handleEnded);
      playStopTimerRef.current = () => {
        video.removeEventListener('timeupdate', handleTimeUpdate);
        video.removeEventListener('ended', handleEnded);
      };
      await video.play();
      handleTimeUpdate();
    } catch {
      if (token !== playbackFlowTokenRef.current) return;
      clearPlaybackTimer();
      setStatus('WAITING_INPUT');
      setStatusMessage(`播放失败，按 ${progressShortcutLabel} 进入下一句`);
    }
  }, [clearPlaybackTimer, clearSentenceResolveTimer, progressShortcutLabel]);

  const playCurrentSentence = useCallback(async () => {
    if (!currentSubtitle) return;
    setAwaitingSpaceForNext(false);
    await playSubtitleSegment(currentSubtitle);
  }, [currentSubtitle, playSubtitleSegment]);

  const buildNextWordStates = useCallback((index: number, state: WordState) => (
    wordStates.map((item, itemIndex) => (itemIndex === index ? state : item))
  ), [wordStates]);

  const markWordResult = useCallback((index: number, state: WordState, text: string) => {
    setWordStates((prev) => prev.map((item, itemIndex) => (itemIndex === index ? state : item)));
    setWordInputs((prev) => prev.map((item, itemIndex) => (itemIndex === index ? text : item)));
  }, []);

  const completeSentence = useCallback((finalStates: WordState[]) => {
    if (!currentSubtitle) return;

    const totalWords = Math.max(1, currentWords.length);
    const independentWords = finalStates.filter((state) => state === 'correct').length;
    const independentRate = Math.round((independentWords / totalWords) * 100);
    const nextIndex = currentIndex + 1;
    const isLastSentence = nextIndex >= subtitles.length;
    const shouldAutoContinue = !isLastSentence && independentRate >= independentThreshold;

    setLastIndependentRate(independentRate);
    setStatus('AUTO_NEXT');
    setStatusMessage('本句完成，正在重播');
    setAwaitingSpaceForNext(false);
    playSound('sentenceComplete');

    void playSubtitleSegment(currentSubtitle, {
      onEnded: () => {
        if (isLastSentence) {
          setStatus('IDLE');
          setAwaitingSpaceForNext(false);
          setStatusMessage('已完成全部句子');
          return;
        }

        if (shouldAutoContinue) {
          setStatus('PLAYING');
          setStatusMessage(`独立完成 ${independentRate}% ，正在续播下一句`);
          goToSentence(nextIndex, {
            autoPlay: true,
            reason: 'sentence-complete-auto-next'
          });
          return;
        }

        setStatus('WAITING_INPUT');
        setAwaitingSpaceForNext(true);
        setStatusMessage(`独立完成 ${independentRate}% ，按 ${progressShortcutLabel} 进入下一句`);
      }
    });
  }, [
    currentSubtitle,
    currentWords.length,
    goToSentence,
    independentThreshold,
    playSound,
    playSubtitleSegment,
    progressShortcutLabel,
    subtitles.length
  ]);

  const verifyWord = useCallback((index: number, nextValue: string) => {
    if (!currentWords[index]) return;

    const expected = currentWords[index];
    const normalizedCurrent = normalizeWord(nextValue);
    const normalizedExpected = normalizeWord(expected);
    const expectedLength = normalizedExpected.length;

    if (evaluatedInputRef.current[index] === normalizedCurrent && normalizedCurrent === normalizedExpected) {
      return;
    }

    if (!normalizedCurrent) {
      evaluatedInputRef.current[index] = '';
      setWordTypedSnapshots((prev) => prev.map((item, itemIndex) => (itemIndex === index ? '' : item)));
      markWordResult(index, 'idle', nextValue);
      setStatus('WAITING_INPUT');
      setStatusMessage('继续输入当前词');
      return;
    }

    if (normalizedCurrent === normalizedExpected) {
      evaluatedInputRef.current[index] = normalizedCurrent;
      setTotalAttempts((prev) => prev + 1);
      setWordTypedSnapshots((prev) => prev.map((item, itemIndex) => (itemIndex === index ? nextValue : item)));
      const nextStates = buildNextWordStates(index, 'correct');
      markWordResult(index, 'correct', nextValue);
      setCorrectAttempts((prev) => prev + 1);
      setStatus('WAITING_INPUT');
      setStatusMessage('这个词正确，已跳到下一词');
      playSound('wordCorrect');

      const nextIndex = findNextOpenWordIndex(nextStates);
      if (nextIndex >= 0) {
        setCurrentWordIndex(nextIndex);
        focusWordInput(nextIndex);
      } else {
        completeSentence(nextStates);
      }
      return;
    }

    if (expectedLength > 0 && normalizedCurrent.length >= expectedLength) {
      setTotalAttempts((prev) => prev + 1);
      setWordTypedSnapshots((prev) => prev.map((item, itemIndex) => (itemIndex === index ? nextValue : item)));
      markWordResult(index, 'wrong', nextValue);
      setStatus('WAITING_INPUT');
      setStatusMessage('这个词还差一点，改对再继续');
      return;
    }

    setWordTypedSnapshots((prev) => prev.map((item, itemIndex) => (itemIndex === index ? nextValue : item)));
    markWordResult(index, 'idle', nextValue);
    setStatus('WAITING_INPUT');
    setStatusMessage('继续输入当前词');
  }, [
    buildNextWordStates,
    completeSentence,
    currentWords,
    focusWordInput,
    markWordResult,
    playSound,
    setWordTypedSnapshots
  ]);

  const revealCurrentWord = useCallback(() => {
    if (!currentWords[currentWordIndex]) return;
    const expected = currentWords[currentWordIndex];
    const typedSnapshot = wordInputs[currentWordIndex] || '';
    const nextStates = buildNextWordStates(currentWordIndex, 'revealed');
    setWordTypedSnapshots((prev) => prev.map((item, itemIndex) => (
      itemIndex === currentWordIndex ? typedSnapshot : item
    )));
    markWordResult(currentWordIndex, 'revealed', expected);
    setTotalAttempts((prev) => prev + 1);
    setStatus('WAITING_INPUT');
    setStatusMessage('已揭示当前词，已跳到下一词');

    const nextIndex = findNextOpenWordIndex(nextStates);
    if (nextIndex >= 0) {
      setCurrentWordIndex(nextIndex);
      focusWordInput(nextIndex);
      return;
    }
    completeSentence(nextStates);
  }, [buildNextWordStates, completeSentence, currentWordIndex, currentWords, focusWordInput, markWordResult, wordInputs]);

  const jumpToNextSentenceByProgress = useCallback(() => {
    if (subtitles.length === 0) return;
    const nextIndex = currentIndex + 1;

    if (nextIndex >= subtitles.length) {
      setStatus('IDLE');
      setAwaitingSpaceForNext(false);
      setStatusMessage('已完成全部句子');
      return;
    }

    goToSentence(nextIndex, {
      autoPlay: true,
      reason: 'space-next-sentence'
    });
  }, [currentIndex, goToSentence, subtitles.length]);

  const loadAssets = useCallback(async () => {
    setErrorText('');
    setReady(false);

    try {
      const progress = getLegacyPracticeProgress();
      const [videoFile, srtFile, subtitleJsonFile] = await Promise.all([
        getLegacyFileAsFile('current_video', 'video.mp4'),
        getLegacyFileAsFile('current_srt', 'subtitle.srt'),
        getLegacyFileAsFile('current_subtitles', 'subtitle.json')
      ]);

      if (!videoFile) {
        setErrorText('未找到练习资源，请先在“听力上传”里完成任务并点击开始练习。');
        setReady(true);
        return;
      }

      if (!srtFile && !subtitleJsonFile) {
        setErrorText('未找到字幕资源，请先在“听力上传”里完成任务并点击开始练习。');
        setReady(true);
        return;
      }

      const [parsedFromSrt, parsedFromJson] = await Promise.all([
        srtFile ? srtFile.text().then((srtText) => parseSrtText(srtText)) : Promise.resolve([]),
        subtitleJsonFile ? subtitleJsonFile.text().then((text) => parseSubtitleJsonRows(text)) : Promise.resolve([])
      ]);

      const parsed = mergeSubtitleRows(parsedFromSrt, parsedFromJson);
      if (parsed.length === 0) {
        setErrorText('字幕解析失败，请重新生成字幕任务。');
        setReady(true);
        return;
      }

      console.debug('[DEBUG] listening-practice subtitle rows merged', {
        srtRows: parsedFromSrt.length,
        jsonRows: parsedFromJson.length,
        effectiveRows: parsed.length
      });

      const objectUrl = URL.createObjectURL(videoFile);
      setVideoUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return objectUrl;
      });

      setSubtitles(parsed);
      setVideoFileName(videoFile.name);
      setSrtFileName(String(srtFile?.name || progress.srtFileName || 'subtitle.srt'));
      setTotalAttempts(Math.max(0, progress.totalAttempts || 0));
      setCorrectAttempts(Math.max(0, progress.correctAttempts || 0));
      setAwaitingSpaceForNext(false);
      setLastIndependentRate(null);

      const restoredIndex = clamp(progress.currentIndex || 0, 0, Math.max(0, parsed.length - 1));
      queuedAutoPlayIndexRef.current = restoredIndex;
      setCurrentIndex(restoredIndex);
      setStatus(normalizePracticeStatus('PLAYING'));
      setStatusMessage('资源就绪，正在播放本句');
      setReady(true);
    } catch (error) {
      const message = error instanceof Error ? error.message : '读取练习资源失败';
      setErrorText(message || '读取练习资源失败');
      setReady(true);
    }
  }, []);

  useEffect(() => {
    soundPoolsRef.current = {
      typing: createSoundPool(SOUND_SOURCES.typing.paths, SOUND_SOURCES.typing.volume, SOUND_SOURCES.typing.copies),
      wordCorrect: createSoundPool(SOUND_SOURCES.wordCorrect.paths, SOUND_SOURCES.wordCorrect.volume, SOUND_SOURCES.wordCorrect.copies),
      sentenceComplete: createSoundPool(SOUND_SOURCES.sentenceComplete.paths, SOUND_SOURCES.sentenceComplete.volume, SOUND_SOURCES.sentenceComplete.copies)
    };
  }, []);

  useEffect(() => {
    void loadAssets();
    return () => {
      clearPlaybackFlow('component-unmount');
    };
  }, [clearPlaybackFlow, loadAssets]);

  useEffect(() => {
    return () => {
      if (videoUrl) URL.revokeObjectURL(videoUrl);
    };
  }, [videoUrl]);

  useEffect(() => {
    const rows = currentWords;
    setWordInputs(rows.map(() => ''));
    setWordTypedSnapshots(rows.map(() => ''));
    setWordStates(rows.map(() => 'idle'));
    setCurrentWordIndex(0);
    setComposingWordIndex(null);
    evaluatedInputRef.current = rows.map(() => '');

    if (rows.length > 0) {
      focusWordInput(0);
    }
  }, [currentIndex, currentWords, focusWordInput]);

  useEffect(() => {
    if (!ready) return;
    if (status !== 'WAITING_INPUT') return;
    if (awaitingSpaceForNext) return;
    if (editingShortcutAction) return;
    if (isComposingRef.current || composingWordIndex !== null) return;
    if (currentWords.length === 0) return;

    const targetIndex = clamp(currentWordIndex, 0, currentWords.length - 1);
    const activeElement = document.activeElement;
    const targetInput = inputRefs.current[targetIndex];
    if (!targetInput) return;
    if (activeElement === targetInput) return;

    focusWordInput(targetIndex);
  }, [
    awaitingSpaceForNext,
    composingWordIndex,
    currentIndex,
    currentWordIndex,
    currentWords.length,
    editingShortcutAction,
    focusWordInput,
    ready,
    status
  ]);

  useEffect(() => {
    if (!ready) return;
    if (queuedAutoPlayIndexRef.current !== currentIndex) return;
    queuedAutoPlayIndexRef.current = null;

    clearSentenceResolveTimer();
    sentenceResolveTimerRef.current = window.setTimeout(() => {
      void playCurrentSentence();
    }, 90);
  }, [clearSentenceResolveTimer, currentIndex, playCurrentSentence, ready]);

  useEffect(() => {
    if (!videoFileName && !srtFileName) return;
    persistLegacyPracticeProgress({
      currentIndex,
      totalAttempts,
      correctAttempts,
      totalSentences: subtitles.length,
      videoFileName,
      srtFileName,
      practiceStatus: status
    });
  }, [correctAttempts, currentIndex, srtFileName, status, subtitles.length, totalAttempts, videoFileName]);

  useEffect(() => {
    setListeningLetterFeedbackMode(letterFeedbackMode);
  }, [letterFeedbackMode]);

  useEffect(() => {
    setListeningShortcutBindings(shortcutBindings);
  }, [shortcutBindings]);

  const validateShortcutBinding = useCallback((
    action: ListeningShortcutAction,
    binding: ListeningShortcutBinding,
    bindings: ListeningShortcutBindings
  ) => {
    const code = String(binding.code || '').trim();
    const key = normalizeShortcutKeyValue(binding.key);
    if (!code && !key) return '请按下有效按键。';
    if (SHORTCUT_EDIT_BLOCKED_CODES.has(code)) {
      return '该按键不允许设置为快捷键，请换一个。';
    }
    if (action !== 'progress' && !binding.shiftKey && !binding.ctrlKey && !binding.altKey && !binding.metaKey) {
      return '建议为该动作添加修饰键（如 Shift），避免误触。';
    }
    if ((binding.ctrlKey || binding.metaKey) && ['KeyR', 'KeyW', 'KeyT', 'KeyN', 'KeyL'].includes(code)) {
      return '该组合容易触发浏览器系统行为，请换一个组合。';
    }

    for (const item of SHORTCUT_ACTIONS) {
      if (item === action) continue;
      if (isSameShortcut(binding, bindings[item])) {
        return `与“${SHORTCUT_ACTION_LABELS[item]}”冲突，请换一个组合。`;
      }
    }
    return '';
  }, []);

  const toggleShortcutEditor = useCallback((action: ListeningShortcutAction) => {
    setEditingShortcutAction((prev) => {
      if (prev === action) {
        setShortcutHint('已取消快捷键修改。');
        return null;
      }
      setShortcutHint(`请按下“${SHORTCUT_ACTION_LABELS[action]}”的新组合键，Esc 可取消。`);
      return action;
    });
  }, []);

  const resetShortcutBindings = useCallback(() => {
    const defaults = getDefaultListeningShortcutBindings();
    setShortcutBindingsState(defaults);
    setEditingShortcutAction(null);
    setShortcutHint('快捷键已恢复默认。');
  }, []);

  useEffect(() => {
    const body = document.body;
    if (immersiveMode) {
      body.classList.add(IMMERSIVE_BODY_CLASS);
    } else {
      body.classList.remove(IMMERSIVE_BODY_CLASS);
    }

    return () => {
      body.classList.remove(IMMERSIVE_BODY_CLASS);
    };
  }, [immersiveMode]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const key = String(event.key || '');
      const code = String(event.code || '');
      const keyLower = key.toLowerCase();
      const codeLower = code.toLowerCase();

      if (keyLower === 'enter' || codeLower === 'enter' || codeLower === 'numpadenter') {
        event.preventDefault();
        event.stopPropagation();
        return;
      }

      if (editingShortcutAction) {
        event.preventDefault();
        event.stopPropagation();
        if (event.repeat) return;

        if (code === 'Escape' || keyLower === 'escape') {
          setEditingShortcutAction(null);
          setShortcutHint('已取消快捷键修改。');
          return;
        }
        if (SHORTCUT_MODIFIER_ONLY_CODES.has(code)) {
          setShortcutHint('请按下主键，不能只按修饰键。');
          return;
        }

        const nextBinding = toShortcutBinding(event);
        const validateMessage = validateShortcutBinding(editingShortcutAction, nextBinding, shortcutBindings);
        if (validateMessage) {
          setShortcutHint(validateMessage);
          return;
        }

        setShortcutBindingsState((prev) => ({ ...prev, [editingShortcutAction]: nextBinding }));
        setEditingShortcutAction(null);
        setShortcutHint(`已设置“${SHORTCUT_ACTION_LABELS[editingShortcutAction]}”：${formatShortcutLabel(nextBinding)}`);
        return;
      }

      if (!ready) return;
      if (event.isComposing || isComposingRef.current) return;

      if (code === 'Escape' || keyLower === 'escape') {
        event.preventDefault();
        event.stopPropagation();
        if (event.repeat) return;
        exitPractice();
        return;
      }

      if (isShortcutMatch(event, shortcutBindings.reveal)) {
        event.preventDefault();
        if (event.repeat) return;
        revealCurrentWord();
        return;
      }

      if (isShortcutMatch(event, shortcutBindings.replay)) {
        event.preventDefault();
        if (event.repeat) return;
        void playCurrentSentence();
        return;
      }

      if (isShortcutMatch(event, shortcutBindings.immersive)) {
        event.preventDefault();
        if (event.repeat) return;
        setImmersiveMode((prev) => !prev);
        return;
      }

      if (isShortcutMatch(event, shortcutBindings.progress)) {
        event.preventDefault();
        if (event.repeat) return;
        const now = Date.now();
        if (now - lastProgressHandledAtRef.current < SPACE_DEBOUNCE_MS) return;
        lastProgressHandledAtRef.current = now;
        if (revealEligible) {
          revealCurrentWord();
          return;
        }
        jumpToNextSentenceByProgress();
      }
    };

    window.addEventListener('keydown', handler, { capture: true });
    return () => window.removeEventListener('keydown', handler, { capture: true });
  }, [
    editingShortcutAction,
    exitPractice,
    jumpToNextSentenceByProgress,
    playCurrentSentence,
    ready,
    revealCurrentWord,
    revealEligible,
    shortcutBindings,
    validateShortcutBinding
  ]);

  const statusTone = useMemo(
    () => resolveStatusTone(status, awaitingSpaceForNext),
    [awaitingSpaceForNext, status]
  );

  if (!ready) {
    return (
      <div className="page-listening-practice fade-in">
        <Card>
          <CardHeader
            title={(
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                <Button type="button" size="sm" variant="outline" onClick={exitPractice}>Esc 退出</Button>
                <span>听力练习</span>
              </span>
            )}
            subtitle="正在加载练习资源..."
            subtitleBehavior="inline"
          />
        </Card>
      </div>
    );
  }

  if (errorText) {
    return (
      <div className="page-listening-practice fade-in">
        <Card>
          <CardHeader
            title={(
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                <Button type="button" size="sm" variant="outline" onClick={exitPractice}>Esc 退出</Button>
                <span>听力练习</span>
              </span>
            )}
            subtitle="加载失败"
            subtitleBehavior="inline"
            action={<Badge tone="danger">unavailable</Badge>}
          />
          <CardBody className="inline-stack">
            <TypographyP className="error-text">{errorText}</TypographyP>
            <div className="inline-actions">
              <Button type="button" variant="secondary" onClick={() => navigate('/listening')}>
                去听力上传
              </Button>
            </div>
          </CardBody>
        </Card>
      </div>
    );
  }

  return (
    <div className={`page-listening-practice fade-in${immersiveMode ? ' is-immersive' : ''}`}>
      <Card className="practice-video-card">
        <CardHeader
          title={(
            <span className="practice-video-heading">
              <span
                className="practice-video-heading__left"
                style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', minWidth: 0, flexWrap: 'wrap' }}
              >
                <Button type="button" size="sm" variant="outline" onClick={exitPractice}>Esc 退出</Button>
                <Button type="button" size="sm" variant="outline" onClick={openLearningHistory}>学习记录</Button>
                <Button type="button" size="sm" variant="outline" onClick={openReadingMaterial}>阅读素材</Button>
                <span className="practice-video-heading__title">听力练习</span>
              </span>
              <span className="practice-video-heading__meta">
                <span className="practice-video-heading__sentence">{sentenceLabel}</span>
                <Progress value={sentenceProgress} className="practice-video-heading__progress" />
              </span>
            </span>
          )}
          subtitle={sentenceLabel}
          subtitleBehavior="inline"
          action={(
            <div className="practice-video-actions">
              <Badge tone={statusTone}>{statusMessage}</Badge>
              {!immersiveMode ? (
                <Button type="button" size="sm" variant="outline" onClick={() => setImmersiveMode(true)}>
                  进入沉浸模式
                </Button>
              ) : null}
            </div>
          )}
        />
        <CardBody className="practice-video-body">
          <div className="practice-video-wrap">
            <video
              ref={videoRef}
              className="practice-video"
              src={videoUrl}
              controls={!immersiveMode}
              preload="metadata"
            />
          </div>
        </CardBody>
      </Card>
      <div className="practice-video-input-divider" aria-hidden="true" />

      <Card className="practice-input-card">
        <CardHeader
          title="输入"
          subtitle={`独立阈值 ${independentThreshold}% · 揭示差值 ${revealGapThreshold}%${lastIndependentRate !== null ? ` · 本句 ${lastIndependentRate}%` : ''}`}
          subtitleBehavior="inline"
        />
        <CardBody className="practice-input-body">
          <div className="practice-input-center">
            {currentWords.length > 0 ? (
              <div className={`practice-word-grid mode-${letterFeedbackMode}`}>
                {currentWords.map((word, index) => {
                  const state = wordStates[index] || 'idle';
                  const wordInputValue = wordInputs[index] || '';
                  const typedChars = buildTypedCharStates(word, wordInputValue, composingWordIndex === index);
                  const isLocked = state === 'correct' || state === 'revealed';
                  const revealedTypedValue = state === 'revealed' ? (wordTypedSnapshots[index] || '') : '';
                  const revealedTypedChars = buildTypedCharStates(word, revealedTypedValue, false);
                  const showRevealedTyped = state === 'revealed';

                  return (
                    <div
                      key={`${currentIndex}-${index}-${word}`}
                      className={`practice-word-item state-${state}${index === currentWordIndex ? ' is-current' : ''}`}
                    >
                      <div className="practice-word-input-shell">
                        <div className="practice-word-input-overlay" aria-hidden="true">
                          {typedChars.length === 0 ? (
                            <span className="practice-typed-char practice-typed-char--placeholder">...</span>
                          ) : typedChars.map((letter, letterIndex) => (
                            <span
                              key={`${currentIndex}-${index}-${letterIndex}-${letter.char}`}
                              className={`practice-typed-char practice-typed-char--${letter.state}`}
                            >
                              {letter.char}
                            </span>
                          ))}
                        </div>
                        <input
                          ref={(node) => {
                            inputRefs.current[index] = node;
                          }}
                          value={wordInputValue}
                          disabled={isLocked}
                          onChange={(event) => {
                            const nextValue = event.target.value;
                            const prevValue = wordInputs[index] || '';
                            const nativeEvent = event.nativeEvent as InputEvent;
                            const composing = Boolean(nativeEvent?.isComposing) || isComposingRef.current;

                            evaluatedInputRef.current[index] = '';
                            setWordInputs((prev) => prev.map((item, itemIndex) => (itemIndex === index ? nextValue : item)));
                            setCurrentWordIndex(index);
                            if (!isLocked) {
                              setWordStates((prev) => prev.map((item, itemIndex) => (itemIndex === index ? 'idle' : item)));
                            }

                            if (!composing && nextValue.length > prevValue.length) {
                              playSound('typing');
                            }

                            if (!composing) {
                              verifyWord(index, nextValue);
                            }
                          }}
                          onFocus={() => setCurrentWordIndex(index)}
                          onCompositionStart={() => {
                            isComposingRef.current = true;
                            setComposingWordIndex(index);
                          }}
                          onCompositionEnd={(event) => {
                            isComposingRef.current = false;
                            setComposingWordIndex(null);
                            verifyWord(index, event.currentTarget.value);
                          }}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') {
                              event.preventDefault();
                              event.stopPropagation();
                            }
                          }}
                          aria-label={`第 ${index + 1} 个词输入`}
                          autoComplete="off"
                          autoCorrect="off"
                          spellCheck={false}
                          className="practice-word-input-native"
                        />
                      </div>

                      <div className={`practice-revealed-typed-slot${showRevealedTyped ? '' : ' is-empty'}`}>
                        <div className={`practice-revealed-typed${showRevealedTyped ? '' : ' practice-revealed-typed--placeholder'}`} aria-label="你的输入（揭示前）">
                          {showRevealedTyped ? (
                            revealedTypedChars.length === 0 ? (
                              <span className="practice-typed-char practice-typed-char--neutral">（未输入）</span>
                            ) : revealedTypedChars.map((letter, letterIndex) => (
                              <span
                                key={`${currentIndex}-${index}-bottom-${letterIndex}-${letter.char}`}
                                className={`practice-typed-char practice-typed-char--${letter.state}`}
                              >
                                {letter.char}
                              </span>
                            ))
                          ) : (
                            <span className="practice-typed-char practice-typed-char--neutral">占位</span>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <TypographyMuted>
                当前句没有可输入词，按
                <ShortcutKbd binding={shortcutBindings.progress} className="practice-shortcut-inline" />
                进入下一句。
              </TypographyMuted>
            )}

            <section className="practice-previous-strip" aria-label="上一句字幕与翻译">
              <TypographyP className={cn('practice-previous-strip__source', !hasPreviousSubtitleText && 'is-placeholder')}>
                {previousSubtitleText}
              </TypographyP>
              <TypographyMuted className={cn('practice-previous-strip__translation', !hasPreviousSubtitleTranslation && 'is-placeholder')}>
                {previousSubtitleTranslation}
              </TypographyMuted>
            </section>

            <TypographyP className="practice-live-feedback" aria-live="polite">{statusMessage}</TypographyP>
            {revealEligible ? (
              <TypographyMuted className="practice-reveal-hint">
                已接近目标，可按
                <ShortcutKbd binding={shortcutBindings.progress} className="practice-shortcut-inline" />
                揭示当前词。
              </TypographyMuted>
            ) : null}
            <HoverExplain
              asChild
              content="推进键会在接近目标时优先揭示当前词，否则进入下一句并播放。Enter 已禁用，防止误触。"
            >
              <TypographyMuted className="practice-aux-info">推进规则说明</TypographyMuted>
            </HoverExplain>

            {!immersiveMode ? (
              <section className="practice-shortcut-panel" aria-label="快捷键设置">
                <div className="practice-shortcut-panel__header">
                  <TypographyLarge>快捷键设置</TypographyLarge>
                  <Button type="button" size="sm" variant="outline" onClick={resetShortcutBindings}>恢复默认</Button>
                </div>
                <div className="practice-shortcut-list">
                  {SHORTCUT_ACTIONS.map((action) => (
                    <div key={action} className="practice-shortcut-item">
                      <HoverExplain asChild content={SHORTCUT_ACTION_DESCRIPTIONS[action]}>
                        <TypographySmall className="practice-shortcut-item__label">{SHORTCUT_ACTION_LABELS[action]}</TypographySmall>
                      </HoverExplain>
                      <div className="practice-shortcut-item__controls">
                        <ShortcutKbd binding={shortcutBindings[action]} />
                        <Button
                          type="button"
                          size="sm"
                          variant={editingShortcutAction === action ? 'secondary' : 'outline'}
                          onClick={() => toggleShortcutEditor(action)}
                        >
                          {editingShortcutAction === action ? '取消' : '修改'}
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
                {shortcutHint ? (
                  <TypographyMuted className="practice-shortcut-hint">{shortcutHint}</TypographyMuted>
                ) : (
                  <HoverExplain asChild content="建议给非推进动作加修饰键（如 Shift+R），减少误触。">
                    <TypographyMuted className="practice-shortcut-hint">输入建议</TypographyMuted>
                  </HoverExplain>
                )}
              </section>
            ) : null}
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
