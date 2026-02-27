import { Coins, RefreshCw, Save } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardBody, CardHeader } from '../../components/ui/card';
import { Label } from '../../components/ui/label';
import { Select } from '../../components/ui/select';
import { TypographyMuted, TypographyP, TypographySmall } from '../../components/ui/typography';
import { useProfileSettings, type ExtendedProfileSettings } from '../../lib/hooks/useProfileSettings';
import type { ProfileSettingsUpdateRequest } from '../../types/backend';

const LEVEL_OPTIONS: ReadonlyArray<{ value: ExtendedProfileSettings['english_level']; label: string }> = [
  { value: 'junior', label: 'junior (3-4)' },
  { value: 'senior', label: 'senior (5-6)' },
  { value: 'cet4', label: 'cet4 (7-8)' },
  { value: 'cet6', label: 'cet6 (9-10)' },
  { value: 'kaoyan', label: 'kaoyan (10-11)' },
  { value: 'toefl', label: 'toefl (11-12)' },
  { value: 'sat', label: 'sat (11-12)' }
];

function buildPatch(currentLevel: ExtendedProfileSettings['english_level'], draftLevel: ExtendedProfileSettings['english_level']): ProfileSettingsUpdateRequest {
  if (currentLevel === draftLevel) return {};
  return { english_level: draftLevel };
}

export function ProfilePage() {
  const navigate = useNavigate();
  const { profile, loading, error, refresh, save } = useProfileSettings();
  const [draftLevel, setDraftLevel] = useState<ExtendedProfileSettings['english_level']>(profile.english_level);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [saveSuccess, setSaveSuccess] = useState('');

  useEffect(() => {
    setDraftLevel(profile.english_level);
  }, [profile.english_level]);

  const patch = useMemo(
    () => buildPatch(profile.english_level, draftLevel),
    [draftLevel, profile.english_level]
  );
  const canSave = Object.keys(patch).length > 0 && !loading && !saving;

  const handleSave = async () => {
    if (!canSave) return;
    setSaving(true);
    setSaveError('');
    setSaveSuccess('');
    try {
      await save(patch);
      setSaveSuccess('已保存');
    } catch (err) {
      const message = err instanceof Error ? err.message : '保存失败';
      setSaveError(message || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page-profile fade-in">
      <Card className="profile-card">
        <CardHeader
          title="个人中心"
          subtitle="普通用户无需配置 API Key 和 Base URL，系统会自动分配模型通道。"
          action={<Badge tone={loading ? 'warning' : 'default'}>{loading ? '加载中' : '已就绪'}</Badge>}
        />
        <CardBody className="profile-card__body">
          {error ? <TypographyP className="error-text">{error}</TypographyP> : null}

          <section className="profile-section">
            <TypographySmall asChild className="profile-level-title">
              <h3>英语等级</h3>
            </TypographySmall>
            <div className="profile-field-grid">
              <div className="profile-field">
                <Label htmlFor="profileEnglishLevel">学习等级</Label>
                <Select
                  id="profileEnglishLevel"
                  value={draftLevel}
                  onChange={(event) => setDraftLevel(event.target.value as ExtendedProfileSettings['english_level'])}
                  disabled={loading || saving}
                >
                  {LEVEL_OPTIONS.map((item) => (
                    <option key={item.value} value={item.value}>{item.label}</option>
                  ))}
                </Select>
              </div>
              <div className="profile-field">
                <Label>等级信息</Label>
                <TypographyP>{profile.english_level_cefr} / {profile.english_level_numeric}</TypographyP>
              </div>
            </div>
          </section>

          <section className="profile-section">
            <TypographySmall asChild className="profile-level-title">
              <h3>学习额度说明</h3>
            </TypographySmall>
            <div className="wallet-overview-grid">
              <article className="wallet-stat-card">
                <TypographySmall>计费方式</TypographySmall>
                <TypographyP className="wallet-stat-value">按量扣减</TypographyP>
                <TypographyMuted>不是月费到期清零，用多少扣多少。</TypographyMuted>
              </article>
              <article className="wallet-stat-card">
                <TypographySmall>充值方式</TypographySmall>
                <TypographyP className="wallet-stat-value">兑换码</TypographyP>
                <TypographyMuted>闲鱼成交后，站内输入兑换码即可到账。</TypographyMuted>
              </article>
            </div>
          </section>

          <section className="profile-section profile-llm-group">
            <div className="wallet-pack-head">
              <TypographyP>购买入口</TypographyP>
              <Badge tone="info">推荐</Badge>
            </div>
            <TypographyMuted>前往额度中心查看档位和余额，兑换成功后可直接开始听力/阅读生成。</TypographyMuted>
            <Button type="button" icon={<Coins size={16} strokeWidth={1.8} />} onClick={() => navigate('/wallet')}>
              打开额度中心
            </Button>
          </section>

          {saveError ? <TypographyP className="error-text">{saveError}</TypographyP> : null}
          {saveSuccess ? <TypographyP className="success-text">{saveSuccess}</TypographyP> : null}

          <div className="profile-actions">
            <Button
              type="button"
              variant="secondary"
              icon={<RefreshCw size={16} strokeWidth={1.8} />}
              onClick={() => void refresh()}
              disabled={loading || saving}
            >
              刷新
            </Button>
            <Button type="button" icon={<Save size={16} strokeWidth={1.8} />} onClick={() => void handleSave()} disabled={!canSave}>
              {saving ? '保存中...' : '保存设置'}
            </Button>
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
