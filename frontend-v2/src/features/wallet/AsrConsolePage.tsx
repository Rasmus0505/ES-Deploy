import { Coins, ReceiptText, RefreshCw, Wrench } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardBody, CardHeader } from '../../components/ui/card';
import { TypographyMuted, TypographyP, TypographySmall } from '../../components/ui/typography';
import { fetchAsrConsole } from '../../lib/api/wallet';
import type { AsrConsoleResponse } from '../../types/backend';

function formatQuota(value: number) {
  return Number(value || 0).toLocaleString('zh-CN');
}

function formatCny(value: number) {
  return `¥${Number(value || 0).toFixed(6)}`;
}

function formatDateTime(value: number) {
  if (!value || value <= 0) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString('zh-CN', { hour12: false });
}

function routeModeLabel(mode: string) {
  if (mode === 'dashscope_direct') return 'DashScope 直连';
  return 'OneAPI 回退';
}

export function AsrConsolePage() {
  const [payload, setPayload] = useState<AsrConsoleResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await fetchAsrConsole(50);
      setPayload(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : '读取 ASR 管理台数据失败';
      setError(message || '读取 ASR 管理台数据失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const stats = useMemo(
    () => ({
      remain: formatQuota(payload?.remaining_quota || 0),
      asrUsed: formatQuota(payload?.asr_used_quota || 0),
      asrCount: formatQuota(payload?.asr_charge_count || 0),
      ratio: `${Number(payload?.cost_multiplier || 0).toFixed(2)}x`
    }),
    [payload?.asr_charge_count, payload?.asr_used_quota, payload?.cost_multiplier, payload?.remaining_quota]
  );

  return (
    <div className="page-wallet fade-in">
      <Card className="wallet-overview-card">
        <CardHeader
          title="ASR 管理台"
          subtitle="查看当前语音识别路由、倍率、余额联动和最近扣费流水。"
          action={<Badge tone={loading ? 'warning' : 'default'}>{loading ? '加载中' : routeModeLabel(payload?.route_mode || '')}</Badge>}
        />
        <CardBody className="wallet-overview-grid">
          <article className="wallet-stat-card">
            <TypographySmall>剩余额度</TypographySmall>
            <TypographyP className="wallet-stat-value">{stats.remain}</TypographyP>
          </article>
          <article className="wallet-stat-card">
            <TypographySmall>ASR 已扣额度</TypographySmall>
            <TypographyP className="wallet-stat-value">{stats.asrUsed}</TypographyP>
          </article>
          <article className="wallet-stat-card">
            <TypographySmall>ASR 扣费次数</TypographySmall>
            <TypographyP className="wallet-stat-value">{stats.asrCount}</TypographyP>
          </article>
          <article className="wallet-stat-card">
            <TypographySmall>当前倍率</TypographySmall>
            <TypographyP className="wallet-stat-value">{stats.ratio}</TypographyP>
          </article>
        </CardBody>
      </Card>

      <div className="wallet-main-grid">
        <Card>
          <CardHeader
            title="运行配置"
            subtitle="当前生效的 ASR 路由与计费参数。"
            action={<Wrench size={16} strokeWidth={1.8} />}
          />
          <CardBody className="wallet-redeem-body">
            <div className="profile-field">
              <TypographySmall>路由模式</TypographySmall>
              <TypographyP>{routeModeLabel(payload?.route_mode || '')}</TypographyP>
            </div>
            <div className="profile-field">
              <TypographySmall>上游地址</TypographySmall>
              <TypographyP>{payload?.route_base_url || '-'}</TypographyP>
            </div>
            <div className="profile-field">
              <TypographySmall>API Key 状态</TypographySmall>
              <TypographyP>{payload?.api_key_configured ? `已配置（${payload.api_key_masked || '***'}）` : '未配置（将回退 OneAPI）'}</TypographyP>
            </div>
            <div className="profile-field">
              <TypographySmall>倍率 / 每元额度</TypographySmall>
              <TypographyP>{`${Number(payload?.cost_multiplier || 0).toFixed(2)}x / ${formatQuota(payload?.quota_per_cny || 0)}`}</TypographyP>
            </div>
            <div className="profile-field">
              <TypographySmall>提交前最低剩余额度</TypographySmall>
              <TypographyP>{formatQuota(payload?.submit_min_remaining_quota || 0)}</TypographyP>
            </div>
            <div className="wallet-redeem-actions">
              <Button
                type="button"
                variant="secondary"
                icon={<RefreshCw size={16} strokeWidth={1.8} />}
                onClick={() => void refresh()}
                disabled={loading}
              >
                刷新管理台
              </Button>
            </div>
            {error ? <TypographyP className="error-text">{error}</TypographyP> : null}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="成本总览"
            subtitle="按 3 倍倍率后的 ASR 成本折算展示。"
            action={<Coins size={16} strokeWidth={1.8} />}
          />
          <CardBody className="wallet-pack-list">
            <article className="wallet-pack-item">
              <div className="wallet-pack-head">
                <TypographyP>ASR 基础成本（累计）</TypographyP>
                <Badge>{formatCny(payload?.asr_base_cost_cny || 0)}</Badge>
              </div>
              <TypographyMuted>云端供应商原始成本累计值</TypographyMuted>
            </article>
            <article className="wallet-pack-item">
              <div className="wallet-pack-head">
                <TypographyP>ASR 计费成本（累计）</TypographyP>
                <Badge>{formatCny(payload?.asr_billed_cost_cny || 0)}</Badge>
              </div>
              <TypographyMuted>按倍率折算后的平台计费成本</TypographyMuted>
            </article>
            <article className="wallet-pack-item">
              <div className="wallet-pack-head">
                <TypographyP>ASR 累计扣费额度</TypographyP>
                <Badge>{formatQuota(payload?.asr_used_quota || 0)}</Badge>
              </div>
              <TypographyMuted>该值已合并进额度中心「已消耗」</TypographyMuted>
            </article>
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="最近扣费流水"
          subtitle="每次任务完成后自动入账，支持去重避免重复扣费。"
          action={<ReceiptText size={16} strokeWidth={1.8} />}
        />
        <CardBody>
          <div className="dashboard-v2__table-wrap">
            <table className="dashboard-v2__table">
              <thead>
                <tr>
                  <th><TypographySmall asChild><span>时间</span></TypographySmall></th>
                  <th><TypographySmall asChild><span>任务ID</span></TypographySmall></th>
                  <th><TypographySmall asChild><span>识别秒数</span></TypographySmall></th>
                  <th><TypographySmall asChild><span>基础成本</span></TypographySmall></th>
                  <th><TypographySmall asChild><span>倍率后成本</span></TypographySmall></th>
                  <th><TypographySmall asChild><span>扣费额度</span></TypographySmall></th>
                </tr>
              </thead>
              <tbody>
                {(payload?.charges || []).map((item) => (
                  <tr key={`${item.job_id}_${item.created_at}`}>
                    <td><TypographyP asChild><span>{formatDateTime(item.created_at)}</span></TypographyP></td>
                    <td><TypographyP asChild><span>{item.job_id || '-'}</span></TypographyP></td>
                    <td><TypographyP asChild><span>{Number(item.billed_seconds || 0).toFixed(3)}</span></TypographyP></td>
                    <td><TypographyP asChild><span>{formatCny(item.base_cost_cny || 0)}</span></TypographyP></td>
                    <td><TypographyP asChild><span>{formatCny(item.billed_cost_cny || 0)}</span></TypographyP></td>
                    <td><TypographyP asChild><span>{formatQuota(item.billed_quota || 0)}</span></TypographyP></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(payload?.charges || []).length === 0 ? <TypographyMuted>暂无 ASR 扣费记录。</TypographyMuted> : null}
        </CardBody>
      </Card>

      <TypographyMuted>
        提示：如果你希望像 OneAPI 一样做多用户管理（按用户调倍率、按模型禁用、人工补扣/回滚），我可以继续加管理接口和管理员权限页。
      </TypographyMuted>
      <Button type="button" variant="outline" onClick={() => toast.info('如果要继续扩展管理员权限页，直接告诉我“继续做管理员模式”')}>
        继续扩展管理员模式
      </Button>
    </div>
  );
}
