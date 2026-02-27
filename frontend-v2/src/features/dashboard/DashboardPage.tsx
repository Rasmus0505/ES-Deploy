import { RefreshCw } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardBody, CardHeader } from '../../components/ui/card';
import { Select } from '../../components/ui/select';
import { Tabs } from '../../components/ui/tabs';
import { TypographyH2, TypographyMuted, TypographyP, TypographySmall } from '../../components/ui/typography';
import {
  getDailyStats,
  getDashboardRangeDays,
  getModuleDailyStats,
  secondsToDurationLabel,
  setDashboardRangeDays
} from '../../lib/storage/compat';
import { toNumber } from '../../lib/utils';

type RangeValue = '7' | '30' | '90';

type DashboardDayRow = {
  dateKey: string;
  totalSeconds: number;
  listeningSeconds: number;
  sentences: number;
  words: number;
  active: boolean;
};

function getRecentDateKeys(days: number) {
  const keys: string[] = [];
  for (let index = 0; index < days; index += 1) {
    const date = new Date();
    date.setDate(date.getDate() - index);
    const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    keys.push(key);
  }
  return keys.reverse();
}

function parseDateKey(dateKey: string) {
  const [yearText, monthText, dayText] = String(dateKey).split('-');
  const year = toNumber(yearText, 0);
  const month = toNumber(monthText, 1);
  const day = toNumber(dayText, 1);
  return new Date(year, Math.max(0, month - 1), day);
}

function formatDateShort(dateKey: string) {
  const date = parseDateKey(dateKey);
  if (Number.isNaN(date.getTime())) return dateKey;
  return `${date.getMonth() + 1}/${date.getDate()}`;
}

function formatDateLong(dateKey: string) {
  const date = parseDateKey(dateKey);
  if (Number.isNaN(date.getTime())) return dateKey;
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function formatAxisSeconds(value: number) {
  const safe = Math.max(0, Math.round(toNumber(value, 0)));
  if (safe >= 3600) return `${Math.round(safe / 3600)}h`;
  if (safe >= 60) return `${Math.round(safe / 60)}m`;
  return `${safe}s`;
}

function normalizeRangeValue(value: string): RangeValue {
  if (value === '30' || value === '90') return value;
  return '7';
}

export function DashboardPage() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [rangeValue, setRangeValue] = useState<RangeValue>(normalizeRangeValue(String(getDashboardRangeDays())));
  const dailyStats = useMemo(() => getDailyStats(), [refreshKey]);
  const moduleDailyStats = useMemo(() => getModuleDailyStats(), [refreshKey]);

  const rangeDays = Number(rangeValue);
  const rangeKeys = useMemo(() => getRecentDateKeys(rangeDays), [rangeDays]);

  const dailyRows = useMemo<DashboardDayRow[]>(() => {
    return rangeKeys.map((dateKey) => {
      const day = dailyStats[dateKey] || {};
      const totalSeconds = toNumber(day.seconds, 0);
      const listeningSeconds = toNumber(moduleDailyStats.listening?.[dateKey]?.seconds, 0);
      const sentences = toNumber(day.sentences, 0);
      const words = toNumber(day.words, 0);
      return {
        dateKey,
        totalSeconds,
        listeningSeconds,
        sentences,
        words,
        active: totalSeconds > 0
      };
    });
  }, [dailyStats, moduleDailyStats, rangeKeys]);

  const summary = useMemo(() => {
    const totalSeconds = dailyRows.reduce((sum, item) => sum + item.totalSeconds, 0);
    const listeningSeconds = dailyRows.reduce((sum, item) => sum + item.listeningSeconds, 0);
    const totalSentences = dailyRows.reduce((sum, item) => sum + item.sentences, 0);
    const totalWords = dailyRows.reduce((sum, item) => sum + item.words, 0);
    const activeDays = dailyRows.filter((item) => item.active).length;
    const averageSeconds = rangeDays > 0 ? Math.round(totalSeconds / rangeDays) : 0;

    return {
      totalSeconds,
      listeningSeconds,
      activeDays,
      averageSeconds,
      totalSentences,
      totalWords
    };
  }, [dailyRows, rangeDays]);

  const tableRows = useMemo(() => [...dailyRows].reverse(), [dailyRows]);
  const hasData = dailyRows.some((item) => (
    item.totalSeconds > 0
    || item.listeningSeconds > 0
    || item.sentences > 0
    || item.words > 0
  ));

  const applyRangeValue = (next: string) => {
    const safe = normalizeRangeValue(next);
    setRangeValue(safe);
    setDashboardRangeDays(Number(safe));
  };

  return (
    <div className="page-dashboard dashboard-v2 fade-in">
      <Card className="dashboard-v2__range-card">
        <CardHeader
          title="学习数据中心"
          subtitle="看看你最近学习了多久、哪天最活跃。"
          action={
            <div className="inline-actions">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                icon={<RefreshCw size={16} strokeWidth={1.8} />}
                onClick={() => setRefreshKey((prev) => prev + 1)}
              >
                刷新
              </Button>
            </div>
          }
        />
        <CardBody>
          <div className="dashboard-v2__range-controls">
            <Tabs
              className="dashboard-v2__range-tabs"
              items={[
                { label: '近 7 天', value: '7' },
                { label: '近 30 天', value: '30' },
                { label: '近 90 天', value: '90' }
              ]}
              value={rangeValue}
              onChange={applyRangeValue}
            />
            <Select
              className="dashboard-v2__range-select"
              aria-label="选择统计时间区间"
              value={rangeValue}
              onChange={(event) => applyRangeValue(event.target.value)}
            >
              <option value="7">近 7 天</option>
              <option value="30">近 30 天</option>
              <option value="90">近 90 天</option>
            </Select>
          </div>
          <div className="dashboard-v2__meta">
            <TypographyMuted asChild>
              <span>当前区间：近 {rangeDays} 天</span>
            </TypographyMuted>
            <TypographyMuted asChild>
              <span>活跃天数：{summary.activeDays}</span>
            </TypographyMuted>
          </div>
        </CardBody>
      </Card>

      <div className="dashboard-v2__summary-grid">
        <Card>
          <CardHeader title="总学习时长" subtitle={`近 ${rangeDays} 天学习总时长`} action={<Badge tone="info">总览</Badge>} />
          <CardBody>
            <TypographyH2 asChild>
              <span className="dashboard-v2__metric-value">{secondsToDurationLabel(summary.totalSeconds)}</span>
            </TypographyH2>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="听力学习时长" subtitle={`近 ${rangeDays} 天听力时长`} action={<Badge tone="success">听力</Badge>} />
          <CardBody>
            <TypographyH2 asChild>
              <span className="dashboard-v2__metric-value">{secondsToDurationLabel(summary.listeningSeconds)}</span>
            </TypographyH2>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="活跃天数" subtitle="这段时间里有学习记录的天数" action={<Badge tone="default">活跃</Badge>} />
          <CardBody>
            <TypographyH2 asChild>
              <span className="dashboard-v2__metric-value">{summary.activeDays}</span>
            </TypographyH2>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="日均学习时长" subtitle="总时长除以天数，方便看学习节奏" action={<Badge tone="warning">均值</Badge>} />
          <CardBody>
            <TypographyH2 asChild>
              <span className="dashboard-v2__metric-value">{secondsToDurationLabel(summary.averageSeconds)}</span>
            </TypographyH2>
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="学习时长趋势"
          subtitle="对比每天总学习时长和听力时长的变化。"
          action={
            <div className="dashboard-v2__legend">
              <span className="dashboard-v2__legend-item">
                <i className="dashboard-v2__legend-dot is-total" />
                <TypographySmall asChild>
                  <span>总学习时长</span>
                </TypographySmall>
              </span>
              <span className="dashboard-v2__legend-item">
                <i className="dashboard-v2__legend-dot is-listening" />
                <TypographySmall asChild>
                  <span>听力时长</span>
                </TypographySmall>
              </span>
            </div>
          }
        />
        <CardBody>
          <div className="dashboard-v2__chart-wrap">
            {hasData ? (
              <ResponsiveContainer width="100%" height={320}>
                <AreaChart data={dailyRows} margin={{ top: 8, right: 18, left: 4, bottom: 0 }}>
                  <defs>
                    <linearGradient id="dashboardTotalFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#e5e7eb" stopOpacity={0.42} />
                      <stop offset="95%" stopColor="#e5e7eb" stopOpacity={0.05} />
                    </linearGradient>
                    <linearGradient id="dashboardListeningFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#9ca3af" stopOpacity={0.5} />
                      <stop offset="95%" stopColor="#9ca3af" stopOpacity={0.07} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="4 4" vertical={false} stroke="hsl(0 0% 24%)" />
                  <XAxis
                    dataKey="dateKey"
                    tickLine={false}
                    axisLine={false}
                    tickMargin={10}
                    minTickGap={28}
                    tick={{ fill: 'hsl(0 0% 74%)', fontSize: 12 }}
                    tickFormatter={(value) => formatDateShort(String(value))}
                  />
                  <YAxis
                    width={48}
                    tickLine={false}
                    axisLine={false}
                    tickMargin={8}
                    tick={{ fill: 'hsl(0 0% 74%)', fontSize: 12 }}
                    tickFormatter={(value) => formatAxisSeconds(toNumber(value, 0))}
                  />
                  <Tooltip
                    cursor={false}
                    formatter={(value, name) => {
                      const toneLabel = name === 'listeningSeconds' ? '听力时长' : '总学习时长';
                      return [secondsToDurationLabel(toNumber(value, 0)), toneLabel];
                    }}
                    labelFormatter={(value) => `日期 ${formatDateLong(String(value))}`}
                    contentStyle={{
                      borderRadius: 12,
                      border: '1px solid hsl(0 0% 24%)',
                      background: 'rgba(8, 9, 11, 0.96)',
                      boxShadow: '0 16px 32px rgba(0, 0, 0, 0.55)'
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="listeningSeconds"
                    stroke="#9ca3af"
                    strokeWidth={2.2}
                    strokeDasharray="6 4"
                    fill="url(#dashboardListeningFill)"
                    activeDot={{ r: 4 }}
                  />
                  <Area
                    type="monotone"
                    dataKey="totalSeconds"
                    stroke="#e5e7eb"
                    strokeWidth={2.4}
                    fill="url(#dashboardTotalFill)"
                    activeDot={{ r: 4 }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <TypographyMuted className="dashboard-v2__empty">
                暂无学习数据，先完成一次练习后自动生成看板。
              </TypographyMuted>
            )}
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="按天学习明细" subtitle="按日期从近到远查看每天数据。" />
        <CardBody>
          <div className="dashboard-v2__table-wrap">
            <table className="dashboard-v2__table">
              <thead>
                <tr>
                  <th><TypographySmall asChild><span>日期</span></TypographySmall></th>
                  <th><TypographySmall asChild><span>总时长</span></TypographySmall></th>
                  <th><TypographySmall asChild><span>听力时长</span></TypographySmall></th>
                  <th><TypographySmall asChild><span>句子数</span></TypographySmall></th>
                  <th><TypographySmall asChild><span>词数</span></TypographySmall></th>
                  <th><TypographySmall asChild><span>活跃</span></TypographySmall></th>
                </tr>
              </thead>
              <tbody>
                {tableRows.map((item) => (
                  <tr key={item.dateKey}>
                    <td><TypographyP asChild><span>{formatDateLong(item.dateKey)}</span></TypographyP></td>
                    <td><TypographyP asChild><span>{secondsToDurationLabel(item.totalSeconds)}</span></TypographyP></td>
                    <td><TypographyP asChild><span>{secondsToDurationLabel(item.listeningSeconds)}</span></TypographyP></td>
                    <td><TypographyP asChild><span>{item.sentences.toLocaleString()}</span></TypographyP></td>
                    <td><TypographyP asChild><span>{item.words.toLocaleString()}</span></TypographyP></td>
                    <td>
                      <Badge tone={item.active ? 'success' : 'default'}>
                        {item.active ? '已学习' : '未学习'}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
