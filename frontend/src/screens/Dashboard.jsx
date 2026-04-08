/* eslint react-hooks/set-state-in-effect: off */
import { useEffect, useMemo, useState } from 'react';
import * as api from '../api';
import ChartCard from '../components/ChartCard';
import KpiCard from '../components/KpiCard';
import DailyBriefBlock from '../components/DailyBriefBlock';

function buildDateRange(fromIso, toIso) {
  if (!fromIso || !toIso) return [];
  const toLocalIsoDate = (d) => {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  };
  const start = new Date(fromIso + 'T00:00:00');
  const end = new Date(toIso + 'T00:00:00');
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start > end) return [];
  const dates = [];
  const cur = new Date(start);
  while (cur <= end) {
    // Важно: не используем toISOString(), иначе сдвигаемся в UTC и теряем день (например, 07.04 → 06.04).
    dates.push(toLocalIsoDate(cur));
    cur.setDate(cur.getDate() + 1);
  }
  return dates;
}

function formatDate(iso) {
  if (!iso) return '';
  const parts = iso.split('-');
  return parts.length >= 2 ? `${parts[2]}.${parts[1]}` : iso;
}

function formatNum(n) {
  if (n == null || n === '') return '—';
  return Math.round(Number(n)).toLocaleString('ru');
}

export default function Dashboard({ range, refreshTrigger, cache, updateCache }) {
  const { dateFrom, dateTo } = range || {};
  const [funnelRows, setFunnelRows] = useState(() =>
    cache?.funnel && Array.isArray(cache.funnel) ? cache.funnel : [],
  );
  const [loadingFunnel, setLoadingFunnel] = useState(() => !(cache?.funnel?.length));
  const daysCount = useMemo(() => {
    if (!dateFrom || !dateTo) return 0;
    const a = new Date(dateFrom + 'T00:00:00');
    const b = new Date(dateTo + 'T00:00:00');
    const diff = Math.round((b - a) / 86400000);
    return diff >= 0 ? diff + 1 : 0;
  }, [dateFrom, dateTo]);

  const [pnl, setPnl] = useState(() =>
    cache?.pnl && Array.isArray(cache.pnl) ? cache.pnl : [],
  );
  const [loading, setLoading] = useState(() => !(cache?.pnl?.length));
  const [error, setError] = useState('');
  const [errorFunnel, setErrorFunnel] = useState('');

  useEffect(() => {
    if (!dateFrom || !dateTo) return;
    setLoading(true);
    setError('');
    api
      .getPnl(dateFrom, dateTo)
      .then((data) => {
        const list = Array.isArray(data) ? data : [];
        setPnl(list);
        if (typeof updateCache === 'function') updateCache('pnl', list);
      })
      .catch((e) => setError(e.message || 'Ошибка загрузки'))
      .finally(() => setLoading(false));
  }, [dateFrom, dateTo, refreshTrigger, updateCache]);

  useEffect(() => {
    if (!dateFrom || !dateTo) return;
    setLoadingFunnel(true);
    setErrorFunnel('');
    api
      .getFunnel(dateFrom, dateTo)
      .then((data) => {
        const list = Array.isArray(data) ? data : [];
        setFunnelRows(list);
        if (typeof updateCache === 'function') updateCache('funnel', list);
      })
      .catch((e) => setErrorFunnel(e.message || 'Ошибка загрузки'))
      .finally(() => setLoadingFunnel(false));
  }, [dateFrom, dateTo, refreshTrigger, updateCache]);

  const filtered = useMemo(() => {
    if (!dateFrom || !dateTo) return [];
    const pnlByDate = new Map(
      (pnl || [])
        .filter((r) => r.date >= dateFrom && r.date <= dateTo)
        .map((r) => [r.date, r]),
    );
    return buildDateRange(dateFrom, dateTo).map((d) => {
      const row = pnlByDate.get(d);
      if (row) return row;
      return {
        date: d,
        revenue: 0,
        commission: 0,
        logistics: 0,
        penalties: 0,
        storage: 0,
        ads_spend: 0,
        cogs: 0,
        tax: 0,
        operation_expenses: 0,
        margin: 0,
      };
    });
  }, [pnl, dateFrom, dateTo]);

  const showFullLoader = (loading && filtered.length === 0) || (loadingFunnel && funnelRows.length === 0);

  const totals = useMemo(() => {
    return (filtered || []).reduce(
      (acc, r) => {
        acc.revenue += Number(r.revenue) || 0;
        acc.commission += Number(r.commission) || 0;
        acc.logistics += Number(r.logistics) || 0;
        acc.penalties += Number(r.penalties) || 0;
        acc.storage += Number(r.storage) || 0;
        acc.ads += Number(r.ads_spend) || 0;
        acc.cogs += Number(r.cogs) || 0;
        acc.tax += Number(r.tax) || 0;
        acc.operation_expenses += Number(r.operation_expenses) || 0;
        acc.margin += Number(r.margin) || 0;
        return acc;
      },
      { revenue: 0, commission: 0, logistics: 0, penalties: 0, storage: 0, ads: 0, cogs: 0, tax: 0, operation_expenses: 0, margin: 0 },
    );
  }, [filtered]);

  const ordersByDate = useMemo(() => {
    const map = {};
    for (const r of funnelRows || []) {
      const d = r.date;
      if (!d) continue;
      if (dateFrom && d < dateFrom) continue;
      if (dateTo && d > dateTo) continue;
      map[d] = (map[d] || 0) + (Number(r.order_sum) || 0);
    }
    return map;
  }, [funnelRows, dateFrom, dateTo]);

  const labels = filtered.map((r) => formatDate(r.date));
  const revenueSeries = filtered.map((r) => Number(r.revenue) || 0);
  const ordersSeries = filtered.map((r) => ordersByDate[r.date] || 0);
  const marginSeries = filtered.map((r) => Number(r.margin) || 0);

  const marginPercentOfRevenue = totals.revenue > 0 ? (totals.margin / totals.revenue) * 100 : 0;
  const marginIsPositive = totals.margin >= 0;

  const roiPercent = totals.cogs > 0 ? (totals.margin / totals.cogs) * 100 : 0;
  const roiAvailable = totals.cogs > 0;

  return (
    <>
      {/* Ежедневная AI-оперативная сводка — монтируется сразу, независимо от загрузки PnL/Funnel */}
      <DailyBriefBlock />

      {showFullLoader ? (
        <div className="loader-center">
          <div className="loader-spinner" />
          <p>Загрузка данных...</p>
        </div>
      ) : error ? (
        <div className="alert alert-danger">{error}</div>
      ) : errorFunnel ? (
        <div className="alert alert-danger">{errorFunnel}</div>
      ) : (
      <>

      <div className="kpi-grid">
        <KpiCard
          label="Выручка"
          value={`${formatNum(totals.revenue)} ₽`}
          delta="за период"
          valueClassName=""
          bar={{ widthPct: 100, background: '#7c6ff7', opacity: 0.3 }}
        />
        <KpiCard
          label="Маржа"
          value={`${formatNum(totals.margin)} ₽`}
          delta={`${
            marginIsPositive ? '▲' : '▼'
          } ${Math.abs(marginPercentOfRevenue).toFixed(1)}% от выручки`}
          valueClassName=""
          deltaColor={marginIsPositive ? 'var(--green)' : 'var(--red)'}
          valueStyle={{ color: marginIsPositive ? 'var(--green)' : 'var(--red)' }}
          bar={{
            widthPct: marginPercentOfRevenue > 0 ? marginPercentOfRevenue : 0,
            background: marginIsPositive ? 'var(--green)' : 'var(--red)',
            opacity: 0.5,
          }}
        />
        <KpiCard
          label="ROI"
          value={roiAvailable ? `${Math.round(roiPercent)}%` : '0%'}
          delta={roiAvailable ? 'за период' : 'Данные не внесены'}
          valueStyle={{ color: 'var(--text-tertiary)' }}
          deltaColor="var(--text-tertiary)"
        />
      </div>

      <ChartCard title="Заказы, ₽" badge="сумма за день" labels={labels} data={ordersSeries} borderColor="#7c6ff7" />
      <ChartCard title="Продажи, ₽" badge="сумма за день" labels={labels} data={revenueSeries} borderColor="#14b8a6" />
      <ChartCard title="Маржа, ₽" badge="сумма за день" labels={labels} data={marginSeries} borderColor="#16a34a" />

      <div className="table-card">
        <div className="table-head-row">
          <h3>Детализация по дням</h3>
          <span className="tag tag-gray">{daysCount} дней</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="left">Дата</th>
                <th>Выручка</th>
                <th>Заказы ₽</th>
                <th>Ком</th>
                <th>% ком</th>
                <th>Лог</th>
                <th>% лог</th>
                <th>Штрафы</th>
                <th>Себес</th>
                <th>Налог</th>
                <th>Реклама</th>
                <th>% рекл</th>
                <th>Хран</th>
                <th>% хран</th>
                <th>Опер. расходы</th>
                <th>Маржа</th>
                <th>% маржи</th>
                <th>ROI</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={18} style={{ textAlign: 'center', padding: 16 }}>
                    Нет данных
                  </td>
                </tr>
              ) : (
                filtered.map((r) => {
                  const revenue = Number(r.revenue) || 0;
                  const orders = ordersByDate[r.date] || 0;
                  const commission = Number(r.commission) || 0;
                  const logistics = Number(r.logistics) || 0;
                  const penalties = Number(r.penalties) || 0;
                  const ads = Number(r.ads_spend) || 0;
                  const storage = Number(r.storage) || 0;
                  const opExp = Number(r.operation_expenses) || 0;
                  const cogs = Number(r.cogs) || 0;
                  const tax = Number(r.tax) || 0;
                  const margin = Number(r.margin) || 0;

                  const neg = margin < 0;
                  const comPct = revenue > 0 ? ((commission / revenue) * 100) : 0;
                  const logPct = revenue > 0 ? ((logistics / revenue) * 100) : 0;
                  const reklPct = revenue > 0 ? ((ads / revenue) * 100) : 0;
                  const storPct = revenue > 0 ? ((storage / revenue) * 100) : 0;
                  const marginPct = revenue > 0 ? ((margin / revenue) * 100) : 0;
                  const roi = cogs > 0 ? (margin / cogs) * 100 : 0;

                  return (
                    <tr key={r.date}>
                      <td className="left">{formatDate(r.date)}</td>
                      <td>{formatNum(r.revenue)}</td>
                      <td>{formatNum(orders)}</td>
                      <td>{formatNum(commission)}</td>
                      <td>{revenue > 0 ? comPct.toFixed(1) : '0'}%</td>
                      <td>{formatNum(logistics)}</td>
                      <td>{revenue > 0 ? logPct.toFixed(1) : '0'}%</td>
                      <td>{formatNum(penalties)}</td>
                      <td>{formatNum(cogs)}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>{formatNum(tax)}</td>
                      <td>{formatNum(ads)}</td>
                      <td>{revenue > 0 ? reklPct.toFixed(1) : '0'}%</td>
                      <td>{formatNum(storage)}</td>
                      <td>{revenue > 0 ? storPct.toFixed(1) : '0'}%</td>
                      <td style={{ color: 'var(--red)', fontWeight: 600 }}>{formatNum(opExp)}</td>
                      <td style={{ fontWeight: 500, color: neg ? 'var(--red)' : 'var(--green)' }}>
                        {formatNum(margin)}
                      </td>
                      <td style={{ fontWeight: 500, color: neg ? 'var(--red)' : 'var(--green)' }}>
                        {revenue > 0 ? marginPct.toFixed(1) : '0'}%
                      </td>
                      <td>{cogs > 0 ? `${Math.round(roi)}%` : '0%'}</td>
                    </tr>
                  );
                })
              )}

              {filtered.length > 0 && (
                <tr style={{ fontWeight: 500, background: 'var(--bg-secondary)' }}>
                  <td className="left">ИТОГО</td>
                  <td>{formatNum(totals.revenue)}</td>
                  <td>{formatNum(Object.values(ordersByDate).reduce((a, b) => a + (Number(b) || 0), 0))}</td>
                  <td>{formatNum(totals.commission)}</td>
                  <td>{totals.revenue > 0 ? ((totals.commission / totals.revenue) * 100).toFixed(1) : '0'}%</td>
                  <td>{formatNum(totals.logistics)}</td>
                  <td>{totals.revenue > 0 ? ((totals.logistics / totals.revenue) * 100).toFixed(1) : '0'}%</td>
                  <td>{formatNum(totals.penalties)}</td>
                  <td>{formatNum(totals.cogs)}</td>
                  <td style={{ color: 'var(--text-secondary)' }}>{formatNum(totals.tax)}</td>
                  <td>{formatNum(totals.ads)}</td>
                  <td>{totals.revenue > 0 ? ((totals.ads / totals.revenue) * 100).toFixed(1) : '0'}%</td>
                  <td>{formatNum(totals.storage)}</td>
                  <td>{totals.revenue > 0 ? ((totals.storage / totals.revenue) * 100).toFixed(1) : '0'}%</td>
                  <td style={{ color: 'var(--red)', fontWeight: 600 }}>{formatNum(totals.operation_expenses)}</td>
                  <td style={{ fontWeight: 500, color: 'var(--green)' }}>{formatNum(totals.margin)}</td>
                  <td style={{ fontWeight: 500, color: 'var(--green)' }}>
                    {totals.revenue > 0 ? ((totals.margin / totals.revenue) * 100).toFixed(1) : '0'}%
                  </td>
                  <td>{totals.cogs > 0 ? `${Math.round(roiPercent)}%` : '0%'}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      </>
      )}
    </>
  );
}

