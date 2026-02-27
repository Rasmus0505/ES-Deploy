import { Coins, ReceiptText, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardBody, CardHeader } from '../../components/ui/card';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { TypographyMuted, TypographyP, TypographySmall } from '../../components/ui/typography';
import { fetchWalletPacks, fetchWalletQuota, redeemWalletCode } from '../../lib/api/wallet';
import type { WalletPackItem, WalletQuotaResponse } from '../../types/backend';

function formatQuota(value: number) {
  return Number(value || 0).toLocaleString('zh-CN');
}

export function WalletPage() {
  const [quota, setQuota] = useState<WalletQuotaResponse | null>(null);
  const [packs, setPacks] = useState<WalletPackItem[]>([]);
  const [costMultiplier, setCostMultiplier] = useState(3);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [redeemCode, setRedeemCode] = useState('');
  const [redeeming, setRedeeming] = useState(false);

  const refreshWalletData = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [quotaPayload, packPayload] = await Promise.all([
        fetchWalletQuota(),
        fetchWalletPacks()
      ]);
      setQuota(quotaPayload);
      setPacks(packPayload.packs || []);
      setCostMultiplier(Number(packPayload.cost_multiplier || 3));
    } catch (err) {
      const message = err instanceof Error ? err.message : '读取额度信息失败';
      setError(message || '读取额度信息失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshWalletData();
  }, [refreshWalletData]);

  const handleRedeem = async () => {
    const safeCode = String(redeemCode || '').trim();
    if (!safeCode) {
      toast.warning('请先输入兑换码');
      return;
    }
    setRedeeming(true);
    setError('');
    try {
      const payload = await redeemWalletCode({ key: safeCode });
      setQuota({
        user_id: payload.user_id,
        username: payload.username,
        quota: payload.quota,
        used_quota: payload.used_quota,
        remaining_quota: payload.remaining_quota,
        request_count: payload.request_count
      });
      setRedeemCode('');
      toast.success(`兑换成功，到账 ${formatQuota(payload.added_quota)} 学习额度`);
    } catch (err) {
      const message = err instanceof Error ? err.message : '兑换失败';
      setError(message || '兑换失败');
      toast.error(message || '兑换失败');
    } finally {
      setRedeeming(false);
    }
  };

  const quotaStats = useMemo(
    () => ({
      total: formatQuota(quota?.quota || 0),
      used: formatQuota(quota?.used_quota || 0),
      remain: formatQuota(quota?.remaining_quota || 0)
    }),
    [quota?.quota, quota?.remaining_quota, quota?.used_quota]
  );

  return (
    <div className="page-wallet fade-in">
      <Card className="wallet-overview-card">
        <CardHeader
          title="额度中心"
          subtitle="一次性购买，按调用量扣减，不走月费到期清零。"
          action={<Badge tone={loading ? 'warning' : 'default'}>{loading ? '同步中' : '已同步'}</Badge>}
        />
        <CardBody className="wallet-overview-grid">
          <article className="wallet-stat-card">
            <TypographySmall>总额度</TypographySmall>
            <TypographyP className="wallet-stat-value">{quotaStats.total}</TypographyP>
          </article>
          <article className="wallet-stat-card">
            <TypographySmall>已消耗</TypographySmall>
            <TypographyP className="wallet-stat-value">{quotaStats.used}</TypographyP>
          </article>
          <article className="wallet-stat-card">
            <TypographySmall>剩余额度</TypographySmall>
            <TypographyP className="wallet-stat-value">{quotaStats.remain}</TypographyP>
          </article>
          <article className="wallet-stat-card">
            <TypographySmall>请求次数</TypographySmall>
            <TypographyP className="wallet-stat-value">{formatQuota(quota?.request_count || 0)}</TypographyP>
          </article>
        </CardBody>
      </Card>

      <div className="wallet-main-grid">
        <Card>
          <CardHeader
            title="兑换码充值"
            subtitle="在闲鱼购买后，粘贴兑换码即可即时到账。"
            action={<Coins size={16} strokeWidth={1.8} />}
          />
          <CardBody className="wallet-redeem-body">
            <div className="profile-field">
              <Label htmlFor="walletRedeemCode">兑换码</Label>
              <Input
                id="walletRedeemCode"
                value={redeemCode}
                onChange={(event) => setRedeemCode(event.target.value)}
                placeholder="请输入兑换码"
                disabled={redeeming}
              />
            </div>
            <div className="wallet-redeem-actions">
              <Button type="button" onClick={() => void handleRedeem()} disabled={redeeming || loading}>
                {redeeming ? '兑换中...' : '立即兑换'}
              </Button>
              <Button
                type="button"
                variant="secondary"
                icon={<RefreshCw size={16} strokeWidth={1.8} />}
                onClick={() => void refreshWalletData()}
                disabled={loading || redeeming}
              >
                刷新额度
              </Button>
            </div>
            <TypographyMuted>流程：闲鱼下单 → 获取兑换码 → 这里兑换 → 立即可用。</TypographyMuted>
            {error ? <TypographyP className="error-text">{error}</TypographyP> : null}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="可购买档位"
            subtitle={`当前运营参考系数：${costMultiplier}x`}
            action={<ReceiptText size={16} strokeWidth={1.8} />}
          />
          <CardBody className="wallet-pack-list">
            {packs.map((item) => (
              <article key={item.id} className="wallet-pack-item">
                <div className="wallet-pack-head">
                  <TypographyP>{item.label}</TypographyP>
                  <Badge>{`￥${item.price}`}</Badge>
                </div>
                <TypographySmall>{`额度 ${formatQuota(item.quota)}`}</TypographySmall>
                {item.description ? <TypographyMuted>{item.description}</TypographyMuted> : null}
              </article>
            ))}
            {packs.length === 0 ? <TypographyMuted>暂无可用档位，请联系管理员配置。</TypographyMuted> : null}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
