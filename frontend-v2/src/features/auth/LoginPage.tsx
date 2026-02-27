import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Button } from '../../components/ui/button';
import { Card, CardBody, CardHeader } from '../../components/ui/card';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { TypographyP, TypographySmall } from '../../components/ui/typography';
import { loginAuth, registerAuth } from '../../lib/api/auth';

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

  const submit = async () => {
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
    <div className="page-profile fade-in" style={{ maxWidth: 560, margin: '0 auto' }}>
      <Card className="profile-card">
        <CardHeader title={mode === 'login' ? '登录' : '注册'} />
        <CardBody className="profile-card__body">
          <div className="profile-field-grid">
            <div className="profile-field">
              <Label htmlFor="authUsername">用户名</Label>
              <Input
                id="authUsername"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="3-64 位字母/数字/下划线/短横线"
              />
            </div>
            <div className="profile-field">
              <Label htmlFor="authPassword">密码</Label>
              <Input
                id="authPassword"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="至少 8 位"
              />
            </div>
          </div>

          {error ? <TypographyP className="error-text">{error}</TypographyP> : null}

          <div className="profile-actions">
            <Button type="button" onClick={() => void submit()} disabled={submitting}>
              {submitting ? '提交中...' : mode === 'login' ? '登录' : '注册并登录'}
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={submitting}
              onClick={() => setMode((prev) => (prev === 'login' ? 'register' : 'login'))}
            >
              {mode === 'login' ? '切换到注册' : '切换到登录'}
            </Button>
          </div>

          <TypographySmall>
            首次使用请先注册，随后将自动登录。
          </TypographySmall>
        </CardBody>
      </Card>
    </div>
  );
}

