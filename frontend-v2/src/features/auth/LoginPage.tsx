import { AlertTriangle, LogIn, Sparkles, UserPlus } from 'lucide-react';
import { type FormEvent, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Button } from '../../components/ui/button';
import { Card, CardBody, CardHeader } from '../../components/ui/card';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { TypographyP, TypographySmall } from '../../components/ui/typography';
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
  const modeTitle = mode === 'login' ? '欢迎回来' : '创建学习账号';
  const submitLabel = submitting ? '提交中...' : mode === 'login' ? '登录并继续学习' : '注册并自动登录';

  const summary = useMemo(() => {
    if (mode === 'login') {
      return '登录后可继续听写、跟读、阅读与复盘记录。';
    }
    return '注册后将自动登录，可直接进入学习任务。';
  }, [mode]);

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
    <div className="auth-page fade-in">
      <aside className="auth-hero">
        <div className="auth-hero__badge">
          <Sparkles size={14} />
          <span>English Learning Hub</span>
        </div>
        <h1 className="auth-hero__title">把兴趣素材变成可持续的英语进步</h1>
        <TypographyP className="auth-hero__text">
          通过听写、跟读、阅读和输出复盘形成闭环训练。登录后系统会按你的节奏持续积累学习结果。
        </TypographyP>
        <ul className="auth-hero__list">
          <li>一次性充值额度，用多少扣多少</li>
          <li>学习记录自动保存，可随时续练</li>
          <li>同一账号覆盖听说读写全链路</li>
        </ul>
      </aside>

      <Card className="auth-card">
        <CardHeader title={modeTitle} subtitle={summary} subtitleBehavior="inline" />
        <CardBody className="auth-card__body">
          <form className="auth-form" onSubmit={(event) => void submit(event)}>
            <div className="auth-form__field">
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
            <div className="auth-form__field">
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
              <div className="auth-alert auth-alert--warn" role="status" aria-live="polite">
                <AlertTriangle size={14} />
                <span>{apiInfo.warning}</span>
              </div>
            ) : null}

            {error ? (
              <div className="auth-alert auth-alert--error" role="alert" aria-live="assertive">
                <AlertTriangle size={14} />
                <span>{error}</span>
              </div>
            ) : null}

            <div className="auth-actions">
              <Button type="submit" disabled={submitting}>
                {mode === 'login' ? <LogIn size={14} /> : <UserPlus size={14} />}
                {submitLabel}
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={submitting}
                onClick={() => {
                  setMode((prev) => (prev === 'login' ? 'register' : 'login'));
                  setError('');
                }}
              >
                {mode === 'login' ? '没有账号？去注册' : '已有账号？去登录'}
              </Button>
            </div>
          </form>

          <TypographySmall className="auth-footnote">
            登录后将跳转到 {nextPath}，并继续你上次的学习路径。
          </TypographySmall>
        </CardBody>
      </Card>
    </div>
  );
}
