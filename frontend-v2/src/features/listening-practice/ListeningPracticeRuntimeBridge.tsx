import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getLearningHistory,
  getLegacyPracticeProgress,
  persistLegacyPracticeProgress
} from '../../lib/storage/compat';
import { clamp } from '../../lib/utils';
import { ListeningPracticePage } from './ListeningPracticePage';

const SENTENCE_LABEL_SELECTOR = '.practice-video-card .ui-card__subtitle';
const EDITABLE_INPUT_SELECTOR = '.page-listening-practice .practice-word-input-native:not(:disabled)';
const SHORTCUT_LIST_SELECTOR = '.practice-shortcut-list';
const HOVER_HINT_SELECTOR = '.practice-aux-info, .practice-shortcut-hint';

function readSentenceLabel() {
  return document.querySelector(SENTENCE_LABEL_SELECTOR)?.textContent?.trim() || '';
}

function isPreviousSentenceShortcut(event: KeyboardEvent) {
  if (!event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return false;
  const key = String(event.key || '').trim().toLowerCase();
  const code = String(event.code || '').trim().toLowerCase();
  return key === 'e' || code === 'keye';
}

function resolveTotalSentences(progress: ReturnType<typeof getLegacyPracticeProgress>) {
  const safeVideo = String(progress.videoFileName || '').trim();
  const safeSrt = String(progress.srtFileName || '').trim();
  const fallback = Math.max(1, Number(progress.currentIndex || 0) + 1);

  if (!safeVideo || !safeSrt) return fallback;
  const matched = getLearningHistory().find((item) => (
    String(item.videoName || '').trim() === safeVideo
    && String(item.srtName || '').trim() === safeSrt
  ));
  const fromHistory = Math.max(0, Number(matched?.totalSentences || 0));
  return Math.max(1, fromHistory || fallback);
}

function stripHoverHint(value: string) {
  return String(value || '')
    .replace('（悬停查看）', '')
    .replace('(悬停查看)', '')
    .trim();
}

function syncHoverHintText() {
  const hintNodes = Array.from(document.querySelectorAll<HTMLElement>(HOVER_HINT_SELECTOR));
  hintNodes.forEach((node) => {
    const raw = node.textContent || '';
    const cleaned = stripHoverHint(raw);
    if (cleaned && cleaned !== raw) {
      node.textContent = cleaned;
    }
  });
}

function ensurePreviousShortcutItem() {
  const list = document.querySelector<HTMLElement>(SHORTCUT_LIST_SELECTOR);
  if (!list) return;
  if (list.querySelector('[data-runtime-shortcut="previous"]')) return;

  const row = document.createElement('div');
  row.className = 'practice-shortcut-item';
  row.setAttribute('data-runtime-shortcut', 'previous');

  const label = document.createElement('p');
  label.className = 'ui-type-small practice-shortcut-item__label';
  label.textContent = '上一句';

  const controls = document.createElement('div');
  controls.className = 'practice-shortcut-item__controls';

  const kbdGroup = document.createElement('div');
  kbdGroup.className = 'ui-kbd-group practice-shortcut-kbd';
  kbdGroup.setAttribute('aria-label', 'Shift + E');

  const shiftKbd = document.createElement('kbd');
  shiftKbd.className = 'ui-kbd';
  shiftKbd.textContent = 'Shift';

  const sep = document.createElement('span');
  sep.className = 'ui-kbd-sep';
  sep.textContent = '+';

  const eKbd = document.createElement('kbd');
  eKbd.className = 'ui-kbd';
  eKbd.textContent = 'E';

  const meta = document.createElement('span');
  meta.className = 'ui-type-small';
  meta.textContent = '默认';

  kbdGroup.append(shiftKbd, sep, eKbd);
  controls.append(kbdGroup, meta);
  row.append(label, controls);

  const firstActionRow = list.children[0] || null;
  if (firstActionRow?.nextSibling) {
    list.insertBefore(row, firstActionRow.nextSibling);
    return;
  }
  list.append(row);
}

export function ListeningPracticeRuntimeBridge() {
  const [instanceKey, setInstanceKey] = useState(0);
  const lastSentenceLabelRef = useRef('');
  const focusRafRef = useRef<number | null>(null);
  const focusTimerRef = useRef<number | null>(null);

  const clearFocusSchedule = useCallback(() => {
    if (focusRafRef.current !== null) {
      window.cancelAnimationFrame(focusRafRef.current);
      focusRafRef.current = null;
    }
    if (focusTimerRef.current !== null) {
      window.clearTimeout(focusTimerRef.current);
      focusTimerRef.current = null;
    }
  }, []);

  const focusEditableInput = useCallback((reason: string) => {
    const input = document.querySelector<HTMLInputElement>(EDITABLE_INPUT_SELECTOR);
    if (!input) return;
    if (document.activeElement === input) return;

    input.focus({ preventScroll: true });
    const length = input.value.length;
    try {
      input.setSelectionRange(length, length);
    } catch {}
  }, []);

  const scheduleFocus = useCallback((reason: string) => {
    clearFocusSchedule();
    focusRafRef.current = window.requestAnimationFrame(() => {
      focusEditableInput(reason);
      focusTimerRef.current = window.setTimeout(() => {
        focusEditableInput(`${reason}-settled`);
      }, 80);
    });
  }, [clearFocusSchedule, focusEditableInput]);

  const jumpToPreviousSentence = useCallback(() => {
    const progress = getLegacyPracticeProgress();
    const totalSentences = resolveTotalSentences(progress);
    const currentIndex = clamp(Number(progress.currentIndex || 0), 0, Math.max(0, totalSentences - 1));
    if (currentIndex <= 0) return;

    const previousIndex = currentIndex - 1;
    persistLegacyPracticeProgress({
      currentIndex: previousIndex,
      totalAttempts: Math.max(0, Number(progress.totalAttempts || 0)),
      correctAttempts: Math.max(0, Number(progress.correctAttempts || 0)),
      totalSentences,
      videoFileName: String(progress.videoFileName || ''),
      srtFileName: String(progress.srtFileName || ''),
      practiceStatus: 'PLAYING'
    });

    setInstanceKey((prev) => prev + 1);
  }, []);

  useEffect(() => {
    const syncShortcutPanelUi = () => {
      ensurePreviousShortcutItem();
      syncHoverHintText();
    };

    syncShortcutPanelUi();
    const observer = new MutationObserver(() => {
      syncShortcutPanelUi();
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });

    return () => {
      observer.disconnect();
    };
  }, [instanceKey]);

  useEffect(() => {
    const syncSentenceFocus = () => {
      const sentenceLabel = readSentenceLabel();
      if (!sentenceLabel) return;
      if (!lastSentenceLabelRef.current) {
        lastSentenceLabelRef.current = sentenceLabel;
        scheduleFocus('initial-sentence');
        return;
      }
      if (sentenceLabel !== lastSentenceLabelRef.current) {
        lastSentenceLabelRef.current = sentenceLabel;
        scheduleFocus('sentence-changed');
      }
    };

    syncSentenceFocus();
    const observer = new MutationObserver(() => {
      syncSentenceFocus();
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });

    return () => {
      observer.disconnect();
      clearFocusSchedule();
    };
  }, [clearFocusSchedule, scheduleFocus, instanceKey]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.repeat || event.isComposing) return;
      if (!isPreviousSentenceShortcut(event)) return;
      const target = event.target as Element | null;
      if (target?.closest('.practice-shortcut-panel')) return;

      event.preventDefault();
      event.stopPropagation();
      jumpToPreviousSentence();
    };

    window.addEventListener('keydown', handleKeyDown, { capture: true });
    return () => {
      window.removeEventListener('keydown', handleKeyDown, { capture: true });
    };
  }, [jumpToPreviousSentence]);

  return <ListeningPracticePage key={instanceKey} />;
}
