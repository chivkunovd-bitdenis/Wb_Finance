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
  const [planFactEnabled, setPlanFactEnabled] = useState(false);
  const [planFactEdit, setPlanFactEdit] = useState(false);
  const [planFactMonths, setPlanFactMonths] = useState([]);
  const [planFactLoading, setPlanFactLoading] = useState(false);
  const [planFactError, setPlanFactError] = useState('');
  const [planInputsByMonth, setPlanInputsByMonth] = useState({});
  const [funnelRows, setFunnelRows] = useState(() =>
    cache?.funnel && Array.isArray(cache.funnel) ? cache.funnel : [],
  );
  const [_, setLoadingFunnel] = useState(() => !(cache?.funnel?.length));
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
    if (!planFactEnabled) return;
    if (!dateFrom || !dateTo) return;
    setPlanFactLoading(true);
    setPlanFactError('');
    api
      .getPlanFactMonths(dateFrom, dateTo)
      .then((data) => {
        const list = Array.isArray(data) ? data : [];
        setPlanFactMonths(list);
        // Initialize inputs from server plans (only for months we fetched).
        const next = {};
        for (const m of list) {
          const monthIso = m?.month;
          const metricRows = Array.isArray(m?.metrics) ? m.metrics : [];
          const monthInputs = {};
          for (const row of metricRows) {
            if (!row || !row.metric_key) continue;
            if (row.plan !== null && row.plan !== undefined) monthInputs[row.metric_key] = Number(row.plan);
          }
          next[monthIso] = monthInputs;
        }
        setPlanInputsByMonth(next);
      })
      .catch((e) => setPlanFactError(e.message || 'Ошибка загрузки планов'))
      .finally(() => setPlanFactLoading(false));
  }, [planFactEnabled, dateFrom, dateTo, refreshTrigger]);

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

  // Воронка может догружаться в фоне (429/ретраи WB). Это не должно блокировать весь дашборд,
  // если P&L уже доступен — иначе получаем «вечный лоадер».
  const showFullLoader = loading && filtered.length === 0;

  const planFactByMonth = useMemo(() => {
    const map = new Map();
    for (const m of planFactMonths || []) {
      if (!m?.month) continue;
      map.set(m.month, m);
    }
    return map;
  }, [planFactMonths]);

  const monthSections = useMemo(() => {
    if (!planFactEnabled) return [{ month: null, rows: filtered }];
    const groups = new Map();
    for (const r of filtered || []) {
      const month = (r?.date || '').slice(0, 7) ? `${(r?.date || '').slice(0, 7)}-01` : null;
      if (!groups.has(month)) groups.set(month, []);
      groups.get(month).push(r);
    }
    return Array.from(groups.entries())
      .filter(([m]) => m)
      .sort((a, b) => String(a[0]).localeCompare(String(b[0])))
      .map(([month, rows]) => ({ month, rows }));
  }, [filtered, planFactEnabled]);

  const cols = useMemo(() => ([
    { label: 'Выручка', key: 'revenue', isPercent: false, editable: true },
    { label: 'Заказы ₽', key: 'orders_sum', isPercent: false, editable: true },
    { label: 'Ком', key: 'commission', isPercent: false, editable: false }, // derived from % комиссии
    { label: '% ком', key: 'commission_pct', isPercent: true, editable: true },
    { label: 'Лог', key: 'logistics', isPercent: false, editable: false }, // derived from % логистики
    { label: '% лог', key: 'logistics_pct', isPercent: true, editable: true },
    { label: 'Штрафы', key: 'penalties', isPercent: false, editable: true },
    { label: 'Себес', key: 'cogs', isPercent: false, editable: true },
    { label: 'Налог', key: 'tax', isPercent: false, editable: true },
    { label: 'Реклама', key: 'ads_spend', isPercent: false, editable: false }, // derived from % рекламы
    { label: '% рекл', key: 'ads_pct', isPercent: true, editable: true },
    { label: 'Хран', key: 'storage', isPercent: false, editable: false }, // derived from % хранения
    { label: '% хран', key: 'storage_pct', isPercent: true, editable: true },
    { label: 'Опер. расходы', key: 'operation_expenses', isPercent: false, editable: true },
    { label: 'Маржа', key: 'margin', isPercent: false, editable: true },
    { label: '% маржи', key: 'margin_pct', isPercent: true, editable: true },
    { label: 'ROI', key: 'roi', isPercent: true, editable: true },
  ]), []);

  function monthTitle(monthIso) {
    if (!monthIso) return '';
    const d = new Date(`${monthIso}T12:00:00`);
    if (Number.isNaN(d.getTime())) return monthIso;
    return d.toLocaleDateString('ru', { month: 'long', year: 'numeric' });
  }

  function formatCellValue(key, v) {
    if (v == null) return '—';
    const isPercent = cols.find((c) => c.key === key)?.isPercent;
    if (isPercent) return `${Math.round(Number(v))}%`;
    return formatNum(v);
  }

  function onPlanInputChange(monthIso, metricKey, nextRaw) {
    setPlanInputsByMonth((prev) => {
      const next = { ...(prev || {}) };
      const m = { ...(next[monthIso] || {}) };
      if (nextRaw === '' || nextRaw === null || nextRaw === undefined) {
        delete m[metricKey];
      } else {
        m[metricKey] = nextRaw;
      }
      next[monthIso] = m;
      return next;
    });
  }

  async function savePlansForVisibleMonths() {
    const months = monthSections.map((s) => s.month).filter(Boolean);
    for (const monthIso of months) {
      const values = planInputsByMonth?.[monthIso] || {};
      // Convert to numbers; keep only finite
      const payload = {};
      for (const [k, v] of Object.entries(values)) {
        const n = Number(v);
        if (Number.isFinite(n)) payload[k] = n;
      }
      await api.savePlanFactMonth(monthIso, payload);
    }
    // Refresh from server to show derived sums and updated plans.
    const refreshed = await api.getPlanFactMonths(dateFrom, dateTo);
    const list = Array.isArray(refreshed) ? refreshed : [];
    setPlanFactMonths(list);
    const next = {};
    for (const m of list) {
      const monthIso = m?.month;
      const metricRows = Array.isArray(m?.metrics) ? m.metrics : [];
      const monthInputs = {};
      for (const row of metricRows) {
        if (!row || !row.metric_key) continue;
        if (row.plan !== null && row.plan !== undefined) monthInputs[row.metric_key] = Number(row.plan);
      }
      next[monthIso] = monthInputs;
    }
    setPlanInputsByMonth(next);
  }

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
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, cursor: 'pointer', userSelect: 'none' }}>
              <input
                type="checkbox"
                checked={planFactEnabled}
                onChange={(e) => {
                  const checked = Boolean(e.target.checked);
                  setPlanFactEnabled(checked);
                  setPlanFactEdit(false);
                }}
              />
              План-факт
            </label>
            {planFactEnabled && (
              <button
                className="btn btn-sm btn-outline-primary"
                onClick={async () => {
                  if (!planFactEdit) {
                    setPlanFactEdit(true);
                    return;
                  }
                  try {
                    await savePlansForVisibleMonths();
                    setPlanFactEdit(false);
                  } catch (e) {
                    alert(e?.message || 'Ошибка сохранения планов');
                  }
                }}
                disabled={planFactLoading}
              >
                {planFactEdit ? 'Сохранить план' : 'Изменить план'}
              </button>
            )}
            <span className="tag tag-gray">{daysCount} дней</span>
          </div>
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
                monthSections.flatMap((section) => {
                  const rows = section.rows || [];
                  const out = [];
                  if (planFactEnabled && section.month) {
                    out.push(
                      <tr key={`sep-${section.month}`} style={{ background: 'var(--bg-secondary)' }}>
                        <td className="left" colSpan={18} style={{ fontWeight: 700 }}>
                          {monthTitle(section.month)}
                        </td>
                      </tr>
                    );
                  }

                  for (const r of rows) {
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

                    out.push(
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
                  }

                  if (planFactEnabled && section.month) {
                    const monthData = planFactByMonth.get(section.month);
                    const metrics = Array.isArray(monthData?.metrics) ? monthData.metrics : [];
                    const byKey = new Map(metrics.map((x) => [x.metric_key, x]));

                    const rowDefs = [
                      { label: 'План', kind: 'plan' },
                      { label: 'Факт', kind: 'fact' },
                      { label: '% выполнения', kind: 'pct_of_plan' },
                      { label: 'Прогноз выполнения', kind: 'forecast' },
                      { label: 'Прогноз выполнения плана', kind: 'forecast_pct_of_plan' },
                    ];

                    for (const def of rowDefs) {
                      const isFirst = def.kind === 'plan';
                      const isLast = def.kind === 'forecast_pct_of_plan';
                      const blockBg = 'rgba(124, 111, 247, 0.06)'; // brand-ish, subtle
                      const blockBorder = 'rgba(124, 111, 247, 0.22)';
                      out.push(
                        <tr
                          key={`${section.month}-${def.kind}`}
                          style={{
                            background: blockBg,
                            boxShadow: isFirst ? `inset 0 2px 0 0 ${blockBorder}` : undefined,
                            borderBottom: isLast ? `2px solid ${blockBorder}` : undefined,
                          }}
                        >
                          <td
                            className="left"
                            style={{
                              fontWeight: 800,
                              color: 'var(--text-primary)',
                              borderLeft: `3px solid ${blockBorder}`,
                            }}
                          >
                            {def.label}
                          </td>
                          {cols.map((c) => {
                            const m = byKey.get(c.key);
                            const isPercent = Boolean(m?.is_percent);
                            const v = m ? m[def.kind] : null;

                            const hideForPercent =
                              isPercent &&
                              (def.kind === 'pct_of_plan' ||
                                def.kind === 'forecast' ||
                                def.kind === 'forecast_pct_of_plan');

                            if (def.kind === 'plan' && planFactEdit) {
                              const editable = Boolean(c.editable);
                              const cur = planInputsByMonth?.[section.month]?.[c.key];
                              return (
                                <td key={c.key}>
                                  <input
                                    type="number"
                                    inputMode="numeric"
                                    value={cur ?? ''}
                                    onChange={(e) => onPlanInputChange(section.month, c.key, e.target.value)}
                                    disabled={!editable}
                                    style={{
                                      width: '100%',
                                      padding: '6px 8px',
                                      borderRadius: 8,
                                      border: '1px solid rgba(0,0,0,0.12)',
                                      background: editable ? 'white' : 'rgba(0,0,0,0.04)',
                                    }}
                                  />
                                </td>
                              );
                            }

                            if (hideForPercent) {
                              return (
                                <td key={c.key} style={{ color: 'var(--text-tertiary)' }}>
                                  —
                                </td>
                              );
                            }

                            if (def.kind === 'pct_of_plan' || def.kind === 'forecast_pct_of_plan') {
                              if (v == null) return <td key={c.key} style={{ color: 'var(--text-tertiary)' }}>—</td>;
                              return <td key={c.key} style={{ fontWeight: 700 }}>{`${Math.round(Number(v) * 100)}%`}</td>;
                            }

                            return <td key={c.key}>{formatCellValue(c.key, v)}</td>;
                          })}
                        </tr>
                      );
                    }
                  }

                  return out;
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
          {planFactEnabled && planFactLoading && (
            <div style={{ padding: 10, color: 'var(--text-tertiary)' }}>Загрузка план-факт...</div>
          )}
          {planFactEnabled && planFactError && (
            <div className="alert alert-danger" style={{ marginTop: 10 }}>{planFactError}</div>
          )}
        </div>
      </div>

      </>
      )}
    </>
  );
}

