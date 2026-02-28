import { type FormEvent, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { loginAuth, registerAuth } from '../../lib/api/auth';
import { getApiBaseDiagnostics } from '../../lib/api/base';

function resolveNextPath(state: unknown): string {
  if (!state || typeof state !== 'object') return '/listening';
  const payload = state as Record<string, unknown>;
  const from = String(payload.from || '').trim();
  if (!from) return '/listening';
  if (!from.startsWith('/')) return '/listening';
  return from;
}

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const nextPath = resolveNextPath(location.state);
  const apiInfo = getApiBaseDiagnostics();
  const modeTitle = mode === 'login' ? '登录你的账号' : '注册学习账号';
  const summary = mode === 'login' ? '输入用户名和密码后继续学习。' : '创建账号后将自动登录并进入学习流程。';
  const submitLabel = submitting ? '提交中...' : mode === 'login' ? '登录' : '注册并自动登录';
  const switchLabel = mode === 'login' ? '没有账号？去注册' : '已有账号？去登录';

  const submit = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    const safeUsername = String(username || '').trim();
    const safePassword = String(password || '').trim();
    if (safeUsername.length < 3) {
      setError('用户名至少 3 位');
      return;
    }
    if (safePassword.length < 8) {
      setError('密码至少 8 位');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      if (mode === 'login') {
        await loginAuth({ username: safeUsername, password: safePassword });
      } else {
        await registerAuth({ username: safeUsername, password: safePassword });
      }
      navigate(nextPath, { replace: true });
    } catch (err) {
      const message = err instanceof Error ? err.message : '登录失败';
      setError(message || '登录失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="bg-background flex min-h-svh w-full items-center justify-center p-6 md:p-10">
      <div className="w-full max-w-sm">
        <div className="flex flex-col gap-6">
          <div className="bg-card text-card-foreground flex flex-col gap-6 rounded-xl border py-6 shadow-sm">
            <div className="grid auto-rows-min items-start gap-1.5 px-6">
              <h1 className="leading-none font-semibold">{modeTitle}</h1>
              <p className="text-muted-foreground text-sm">{summary}</p>
            </div>
            <div className="px-6">
              <form className="flex flex-col gap-6" onSubmit={(event) => void submit(event)}>
                <div className="grid gap-2">
                  <Label htmlFor="authUsername">用户名</Label>
                  <Input
                    id="authUsername"
                    autoComplete="username"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    placeholder="3-64 位字母/数字/下划线/短横线"
                    disabled={submitting}
                  />
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="authPassword">密码</Label>
                  <Input
                    id="authPassword"
                    type="password"
                    autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="至少 8 位"
                    disabled={submitting}
                  />
                </div>

                {apiInfo.warning ? (
                  <p className="text-sm text-amber-500" role="status" aria-live="polite">
                    {apiInfo.warning}
                  </p>
                ) : null}

                {error ? (
                  <p className="text-destructive text-sm" role="alert" aria-live="assertive">
                    {error}
                  </p>
                ) : null}

                <div className="flex flex-col gap-3">
                  <Button type="submit" disabled={submitting}>
                    {submitLabel}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={submitting}
                    onClick={() => {
                      setMode((prev) => (prev === 'login' ? 'register' : 'login'));
                      setError('');
                    }}
                  >
                    {switchLabel}
                  </Button>
                </div>
              </form>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
