'use client';

import { FormEvent, useEffect, useState } from 'react';
import Link from 'next/link';

import { API_BASE, apiFetch } from '../../lib/api';

type JobStatus = {
  jobId: string;
  status: string;
  progressPercent: number;
  stage: string;
  errorCode: string;
  errorMessage: string;
  exerciseSetId?: string;
};

export default function DashboardPage() {
  const [token, setToken] = useState('');
  const [wallet, setWallet] = useState(0);
  const [redeemCode, setRedeemCode] = useState('');
  const [url, setUrl] = useState('');
  const [asrModel, setAsrModel] = useState('paraformer-v2');
  const [mtModel, setMtModel] = useState('qwen-mt');
  const [job, setJob] = useState<JobStatus | null>(null);
  const [message, setMessage] = useState('');

  useEffect(() => {
    const saved = localStorage.getItem('v2_token') || '';
    if (!saved) {
      window.location.href = '/login';
      return;
    }
    setToken(saved);
    refreshWallet(saved).catch(console.error);
  }, []);

  async function refreshWallet(currentToken = token) {
    if (!currentToken) return;
    const resp = await apiFetch<{ balanceCredits: number }>('/api/v2/wallet', {}, currentToken);
    setWallet(resp.data.balanceCredits);
  }

  async function submitRedeem(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    const idempotency = `redeem_${Date.now()}`;
    await apiFetch('/api/v2/wallet/redeem', {
      method: 'POST',
      body: JSON.stringify({ code: redeemCode, idempotency_key: idempotency }),
    }, token);
    setRedeemCode('');
    setMessage('兑换成功');
    await refreshWallet(token);
  }

  async function submitUrlJob(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    const form = new FormData();
    form.append('source_url', url);
    form.append('asr_model', asrModel);
    form.append('mt_model', mtModel);

    const res = await fetch(`${API_BASE}/api/v2/jobs`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.message || '任务创建失败');
    setJob({ jobId: payload.data.jobId, status: payload.data.status, progressPercent: 0, stage: 'queued', errorCode: '', errorMessage: '' });
    setMessage(`任务已创建: ${payload.data.jobId}`);
  }

  async function submitFileJob(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!token) return;
    const input = e.currentTarget.elements.namedItem('video_file') as HTMLInputElement | null;
    const file = input?.files?.[0];
    if (!file) {
      setMessage('请先选择视频文件');
      return;
    }
    const form = new FormData();
    form.append('video_file', file);
    form.append('asr_model', asrModel);
    form.append('mt_model', mtModel);

    const res = await fetch(`${API_BASE}/api/v2/jobs`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.message || '任务创建失败');
    setJob({ jobId: payload.data.jobId, status: payload.data.status, progressPercent: 0, stage: 'queued', errorCode: '', errorMessage: '' });
    setMessage(`任务已创建: ${payload.data.jobId}`);
  }

  async function refreshJob() {
    if (!token || !job) return;
    const resp = await apiFetch<JobStatus>(`/api/v2/jobs/${job.jobId}`, {}, token);
    setJob(resp.data);
  }

  async function retryJob() {
    if (!token || !job) return;
    const resp = await apiFetch<{ jobId: string; status: string; queueAttempts: number }>(
      `/api/v2/jobs/${job.jobId}/retry`,
      { method: 'POST', body: JSON.stringify({}) },
      token,
    );
    setMessage(`任务已重试，次数 ${resp.data.queueAttempts}`);
    await refreshJob();
  }

  return (
    <main>
      <div className="card">
        <h1>Listening V2 控制台</h1>
        <p>余额：<b>{wallet}</b> credits</p>
      </div>

      <div className="card">
        <h2>兑换额度</h2>
        <form className="row" onSubmit={submitRedeem}>
          <input value={redeemCode} onChange={(e) => setRedeemCode(e.target.value)} placeholder="输入兑换码" required />
          <button type="submit">兑换</button>
        </form>
      </div>

      <div className="card">
        <h2>创建 URL 任务</h2>
        <form className="row" onSubmit={submitUrlJob}>
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="视频链接" required style={{ minWidth: 360 }} />
          <select value={asrModel} onChange={(e) => setAsrModel(e.target.value)}>
            <option value="paraformer-v2">paraformer-v2</option>
            <option value="qwen3-asr-flash">qwen3-asr-flash</option>
          </select>
          <select value={mtModel} onChange={(e) => setMtModel(e.target.value)}>
            <option value="qwen-mt">qwen-mt</option>
          </select>
          <button type="submit">开始处理</button>
        </form>
      </div>

      <div className="card">
        <h2>创建上传任务</h2>
        <form className="row" onSubmit={submitFileJob}>
          <input name="video_file" type="file" accept="video/*" required />
          <button type="submit">上传并处理</button>
        </form>
      </div>

      <div className="card">
        <h2>任务状态</h2>
        {job ? (
          <>
            <p>jobId: {job.jobId}</p>
            <p>status: {job.status}</p>
            <p>stage: {job.stage}</p>
            <p>progress: {job.progressPercent}%</p>
            <button onClick={refreshJob}>刷新状态</button>
            {job.status === 'failed' || job.status === 'cancelled' ? (
              <button className="secondary" onClick={retryJob}>重试任务</button>
            ) : null}
            {job.exerciseSetId ? (
              <p>
                <Link href={`/practice/${job.exerciseSetId}`}>进入练习</Link>
              </p>
            ) : null}
            {job.errorMessage ? <p style={{ color: 'crimson' }}>{job.errorMessage}</p> : null}
          </>
        ) : (
          <p>暂无任务</p>
        )}
      </div>

      {message ? <div className="card"><p>{message}</p></div> : null}
    </main>
  );
}
