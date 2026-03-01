'use client';

import { FormEvent, useState } from 'react';
import { useRouter } from 'next/navigation';

import { apiFetch } from '../../lib/api';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [error, setError] = useState('');

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError('');
    try {
      const endpoint = mode === 'login' ? '/api/v2/auth/login' : '/api/v2/auth/register';
      const response = await apiFetch<{ token: string; user: { id: string; email: string } }>(endpoint, {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      });
      localStorage.setItem('v2_token', response.data.token);
      localStorage.setItem('v2_user_email', response.data.user.email);
      router.push('/dashboard');
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败');
    }
  }

  return (
    <main>
      <div className="card">
        <h1>Listening V2 登录</h1>
        <p>只保留核心功能：登录、任务、练习、额度、兑换码。</p>
        <form onSubmit={onSubmit} className="row">
          <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="邮箱" required />
          <input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="密码（至少8位）"
            required
            type="password"
          />
          <button type="submit">{mode === 'login' ? '登录' : '注册'}</button>
          <button type="button" className="secondary" onClick={() => setMode(mode === 'login' ? 'register' : 'login')}>
            切换为{mode === 'login' ? '注册' : '登录'}
          </button>
        </form>
        {error ? <p style={{ color: 'crimson' }}>{error}</p> : null}
      </div>
    </main>
  );
}
