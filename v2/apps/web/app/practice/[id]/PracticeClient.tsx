'use client';

import { useEffect, useMemo, useState } from 'react';

import { API_BASE, apiFetch } from '../../../lib/api';

type ExerciseItem = {
  id: string;
  segmentIndex: number;
  startMs: number;
  endMs: number;
  transcriptEn: string;
  translationZh: string;
  wordCount?: number;
  audioUrl?: string;
};

type RevealState = {
  transcriptEn: string;
  translationZh: string;
  score: number;
  wordResults: boolean[];
};

export default function PracticeClient({ exerciseId }: { exerciseId: string }) {
  const [token, setToken] = useState('');
  const [items, setItems] = useState<ExerciseItem[]>([]);
  const [index, setIndex] = useState(0);
  const [wordInput, setWordInput] = useState('');
  const [typedWords, setTypedWords] = useState<string[]>([]);
  const [reveal, setReveal] = useState<RevealState | null>(null);
  const [audioSrc, setAudioSrc] = useState('');

  useEffect(() => {
    const saved = localStorage.getItem('v2_token') || '';
    if (!saved) {
      window.location.href = '/login';
      return;
    }
    setToken(saved);
    loadExercise(saved).catch(console.error);
  }, []);

  useEffect(() => {
    const current = items[index];
    if (!current || !token) {
      setAudioSrc('');
      return;
    }

    let released = false;
    let objectUrl = '';

    async function loadAudio() {
      if (!current.audioUrl) {
        setAudioSrc('');
        return;
      }
      if (current.audioUrl.startsWith('http')) {
        setAudioSrc(current.audioUrl);
        return;
      }
      const res = await fetch(`${API_BASE}${current.audioUrl}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        setAudioSrc('');
        return;
      }
      const blob = await res.blob();
      objectUrl = URL.createObjectURL(blob);
      if (!released) {
        setAudioSrc(objectUrl);
      }
    }

    loadAudio().catch(() => setAudioSrc(''));

    return () => {
      released = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [items, index, token]);

  async function loadExercise(currentToken: string) {
    const res = await apiFetch<{ items: ExerciseItem[] }>(`/api/v2/exercises/${exerciseId}`, {}, currentToken);
    setItems(res.data.items || []);
  }

  function addWord() {
    const safe = wordInput.trim();
    if (!safe) return;
    setTypedWords((prev) => [...prev, safe]);
    setWordInput('');
  }

  function removeLastWord() {
    setTypedWords((prev) => prev.slice(0, -1));
  }

  async function submitCurrent() {
    const current = items[index];
    if (!current || !token) return;

    let submitted = [...typedWords];
    const tail = wordInput.trim();
    if (tail) {
      submitted = [...submitted, tail];
    }

    if (submitted.length === 0) return;

    const res = await apiFetch<{ score: number; isCorrect: boolean; wordResults: boolean[] }>(
      `/api/v2/exercises/${exerciseId}/attempts`,
      {
        method: 'POST',
        body: JSON.stringify({ item_id: current.id, submitted_words: submitted }),
      },
      token,
    );

    setReveal({
      transcriptEn: current.transcriptEn,
      translationZh: current.translationZh,
      score: res.data.score,
      wordResults: res.data.wordResults || [],
    });

    if (index < items.length - 1) {
      setIndex(index + 1);
      setTypedWords([]);
      setWordInput('');
    }
  }

  const current = items[index];
  const progressText = useMemo(() => `句子 ${Math.min(index + 1, items.length)} / ${items.length}`, [index, items.length]);

  return (
    <main>
      <div className="card">
        <h1>逐词拼写练习</h1>
        {!current ? (
          <p>暂无题目</p>
        ) : (
          <>
            <p>{progressText}</p>
            <p>先播放音频，再按顺序一个词一个词输入。完成后才显示当前句中文。</p>
            {audioSrc ? <audio controls src={audioSrc} /> : <p>该句暂无音频</p>}

            <div className="row">
              <input
                value={wordInput}
                onChange={(e) => setWordInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    addWord();
                  }
                }}
                placeholder="输入一个单词后回车"
              />
              <button type="button" onClick={addWord}>添加单词</button>
              <button type="button" className="secondary" onClick={removeLastWord}>撤销上一个</button>
              <button type="button" onClick={submitCurrent}>提交本句</button>
            </div>

            <p>已输入 {typedWords.length} 词{current.wordCount ? ` / 目标约 ${current.wordCount} 词` : ''}</p>
            <pre>{typedWords.join(' ') || '(尚未输入)'}</pre>

            {index > 0 ? (
              <div>
                <p>上一句英文：{items[index - 1]?.transcriptEn}</p>
                <p>上一句翻译：{items[index - 1]?.translationZh}</p>
              </div>
            ) : null}

            {reveal ? (
              <div className="card">
                <p>本句得分：{reveal.score}</p>
                <p>逐词命中：{reveal.wordResults.filter(Boolean).length} / {reveal.wordResults.length || 0}</p>
                <p>本句英文：{reveal.transcriptEn}</p>
                <p>本句翻译：{reveal.translationZh}</p>
              </div>
            ) : null}
          </>
        )}
      </div>
    </main>
  );
}
