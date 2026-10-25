/* eslint react-hooks/set-state-in-effect: off */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as api from '../api';
import { isStaleStoreResponse, useActiveStoreId, useResetOnStoreChange } from '../storeDataGuard';
import ChartCard from '../components/ChartCard';
import KpiCard from '../components/KpiCard';
import TableExpandButton from '../components/TableExpandButton';
import useFullscreenCard from '../components/useFullscreenCard';
import { addKnown, isKnown, percentOf, sumKnown, toNumber } from '../marketplaceValues';

function computeCogsFromShare(revenue, sharePct) {
  const rev = Number(revenue);
  const pct = Number(sharePct);
  if (!Number.isFinite(rev) || !Number.isFinite(pct)) return null;
  return rev * (pct / 100);
}

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

/**
 * The same daily dashboard for WB, Ozon and the combined view.
 * WB reads /dashboard/pnl and /dashboard/funnel. Other marketplaces read the marketplace endpoints and
 * report unknown values as null, which renders as a dash: they never show a zero that was not measured.
 */
export default function Dashboard({ range, refreshTrigger, cache, updateCache, marketplace = 'wb' }) {
  const { dateFrom, dateTo } = range || {};
  const storeId = useActiveStoreId();
  const detailsTable = useFullscreenCard();
  const isWb = marketplace === 'wb';
  const unknownAsNull = !isWb;
  const [planFactEnabled, setPlanFactEnabled] = useState(false);
  // Plans are stored per store and compared with WB facts only; other marketplaces do not offer plan-fact yet.
  const planFactOn = isWb && planFactEnabled;
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

  const resetForStore = useCallback(() => {
    setPnl([]);
    setFunnelRows([]);
    setError('');
    setErrorFunnel('');
    setLoading(true);
    setLoadingFunnel(true);
  }, []);

  useResetOnStoreChange(storeId, resetForStore);

  useEffect(() => {
    if (!dateFrom || !dateTo) return;
    const reqStore = storeId;
    setLoading(true);
    setError('');
    const request = isWb
      ? api.getPnl(dateFrom, dateTo)
      : api.getMarketplacePnl(marketplace, dateFrom, dateTo);
    request
      .then((data) => {
        if (isStaleStoreResponse(reqStore, storeId)) return;
        const list = Array.isArray(data) ? data : [];
        setPnl(list);
        // The WB cache is keyed by store only; marketplace rows must never be cached under it.
        if (isWb && typeof updateCache === 'function') updateCache('pnl', list, reqStore);
      })
      .catch((e) => {
        if (!isStaleStoreResponse(reqStore, storeId)) setError(e.message || 'Ошибка загрузки');
      })
      .finally(() => {
        if (!isStaleStoreResponse(reqStore, storeId)) setLoading(false);
      });
  }, [dateFrom, dateTo, refreshTrigger, updateCache, storeId, isWb, marketplace]);

  useEffect(() => {
    if (!planFactOn) return;
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

          // Ensure derived plan metric is available in inputs:
          // cogs = revenue * (cogs_share / 100)
          if (monthInputs.cogs == null) {
            const derived = computeCogsFromShare(monthInputs.revenue, monthInputs.cogs_share);
            if (derived != null) monthInputs.cogs = derived;
          }
          next[monthIso] = monthInputs;
        }
        setPlanInputsByMonth(next);
      })
      .catch((e) => setPlanFactError(e.message || 'Ошибка загрузки планов'))
      .finally(() => setPlanFactLoading(false));
  }, [planFactOn, dateFrom, dateTo, refreshTrigger]);

  useEffect(() => {
    // The funnel is WB-only (orders come from WB analytics), so other marketplaces show unknown order sums.
    if (!isWb) {
      setFunnelRows([]);
      setErrorFunnel('');
      setLoadingFunnel(false);
      return;
    }
    if (!dateFrom || !dateTo) return;
    const reqStore = storeId;
    setLoadingFunnel(true);
    setErrorFunnel('');
    api
      .getFunnel(dateFrom, dateTo)
      .then((data) => {
        if (isStaleStoreResponse(reqStore, storeId)) return;
        const list = Array.isArray(data) ? data : [];
        setFunnelRows(list);
        if (typeof updateCache === 'function') updateCache('funnel', list, reqStore);
      })
      .catch((e) => {
        if (!isStaleStoreResponse(reqStore, storeId)) setErrorFunnel(e.message || 'Ошибка загрузки');
      })
      .finally(() => {
        if (!isStaleStoreResponse(reqStore, storeId)) setLoadingFunnel(false);
      });
  }, [dateFrom, dateTo, refreshTrigger, updateCache, storeId, isWb]);

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
      // A day without saved data is unknown for marketplaces, not a zero day.
      const fill = unknownAsNull ? null : 0;
      return {
        date: d,
        revenue: fill,
        commission: fill,
        logistics: fill,
        penalties: fill,
        storage: fill,
        ads_spend: fill,
        cogs: fill,
        tax: fill,
        operation_expenses: fill,
        margin: fill,
      };
    });
  }, [pnl, dateFrom, dateTo, unknownAsNull]);

  // Воронка может догружаться в фоне (429/ретраи WB). Это не должно блокировать весь дашборд,
  // если P&L уже доступен — иначе получаем «вечный лоадер».
  // Marketplace days are unknown until the response arrives, so they must not render as dashes while loading.
  const showFullLoader = loading && (unknownAsNull ? pnl.length === 0 : filtered.length === 0);

  const planFactByMonth = useMemo(() => {
    const map = new Map();
    for (const m of planFactMonths || []) {
      if (!m?.month) continue;
      map.set(m.month, m);
    }
    return map;
  }, [planFactMonths]);

  const monthSections = useMemo(() => {
    if (!planFactOn) return [{ month: null, rows: filtered }];
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
  }, [filtered, planFactOn]);

  const STORAGE_KEY = 'wb_finance_dashboard_columns_v1';

  const mandatoryKeys = useMemo(() => (['revenue', 'margin', 'margin_pct']), []);

  const allColumns = useMemo(
    () => ([
      { label: 'Выручка', key: 'revenue', isPercent: false, editable: true },
      { label: 'Заказы ₽', key: 'orders_sum', isPercent: false, editable: true },
      { label: 'Ком', key: 'commission', isPercent: false, editable: false }, // derived from % комиссии
      { label: '% ком', key: 'commission_pct', isPercent: true, editable: true },
      { label: 'Лог', key: 'logistics', isPercent: false, editable: false }, // derived from % логистики
      { label: '% лог', key: 'logistics_pct', isPercent: true, editable: true },
      { label: 'Штрафы', key: 'penalties', isPercent: false, editable: true },
      // "Себес" считается от "Доля себеса" и выручки плана (read-only).
      { label: 'Себес', key: 'cogs', isPercent: false, editable: false },
      { label: 'Доля себеса', key: 'cogs_share', isPercent: true, editable: true },
      { label: 'Налог', key: 'tax', isPercent: false, editable: true },
      { label: 'Реклама', key: 'ads_spend', isPercent: false, editable: false }, // derived from % рекламы
      { label: '% рекл', key: 'ads_pct', isPercent: true, editable: true },
      { label: 'Хран', key: 'storage', isPercent: false, editable: false }, // derived from % хранения
      { label: '% хран', key: 'storage_pct', isPercent: true, editable: true },
      { label: 'Доля расходов ВБ', key: 'wb_expenses_share', isPercent: true, editable: true },
      { label: 'Опер. расходы', key: 'operation_expenses', isPercent: false, editable: true },
      { label: 'Маржа', key: 'margin', isPercent: false, editable: true },
      { label: '% маржи', key: 'margin_pct', isPercent: true, editable: true },
      { label: 'ROI', key: 'roi', isPercent: true, editable: true },
    ]),
    [],
  );

  const optionalColumns = useMemo(() => {
    const mandatory = new Set(mandatoryKeys);
    return allColumns.filter((c) => !mandatory.has(c.key));
  }, [allColumns, mandatoryKeys]);

  const [visibleColumnKeys, setVisibleColumnKeys] = useState(() => allColumns.map((c) => c.key));

  // Dropdown state for columns settings (gear).
  const [columnsMenuOpen, setColumnsMenuOpen] = useState(false);
  const columnsMenuRef = useRef(null);

  // Hydrate persisted settings.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      const keys = Array.isArray(parsed?.visibleColumnKeys) ? parsed.visibleColumnKeys : null;
      const pf = typeof parsed?.planFactEnabled === 'boolean' ? parsed.planFactEnabled : null;

      const allowed = new Set(allColumns.map((c) => c.key));
      const nextKeys = Array.isArray(keys) ? keys.filter((k) => allowed.has(k)) : null;

      if (nextKeys && nextKeys.length) setVisibleColumnKeys(nextKeys);
      if (pf !== null) setPlanFactEnabled(pf);
    } catch {
      // ignore malformed storage
    }
  }, [allColumns]);

  // Persist settings whenever they change.
  useEffect(() => {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          visibleColumnKeys,
          planFactEnabled,
        }),
      );
    } catch {
      // ignore storage quota / private mode
    }
  }, [visibleColumnKeys, planFactEnabled]);

  // Close menu on outside click / ESC.
  useEffect(() => {
    if (!columnsMenuOpen) return;
    const onDown = (e) => {
      if (e.key === 'Escape') setColumnsMenuOpen(false);
    };
    const onClick = (e) => {
      const root = columnsMenuRef.current;
      if (!root) return;
      if (root.contains(e.target)) return;
      setColumnsMenuOpen(false);
    };
    window.addEventListener('keydown', onDown);
    window.addEventListener('mousedown', onClick);
    return () => {
      window.removeEventListener('keydown', onDown);
      window.removeEventListener('mousedown', onClick);
    };
  }, [columnsMenuOpen]);

  const effectiveVisibleKeys = useMemo(() => {
    const mandatory = new Set(mandatoryKeys);
    const selected = new Set(visibleColumnKeys || []);
    const ordered = [];
    for (const c of allColumns) {
      if (mandatory.has(c.key) || selected.has(c.key)) ordered.push(c.key);
    }
    return ordered;
  }, [allColumns, mandatoryKeys, visibleColumnKeys]);

  const visibleColumns = useMemo(() => {
    const set = new Set(effectiveVisibleKeys);
    return allColumns.filter((c) => set.has(c.key));
  }, [allColumns, effectiveVisibleKeys]);

  const cols = visibleColumns; // keep existing name used by plan-fact block

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

      // Автовычисление "Себес" из "Выручка" и "Доля себеса" в плане:
      // cogs = revenue * (cogs_share / 100)
      if (metricKey === 'revenue' || metricKey === 'cogs_share') {
        const derived = computeCogsFromShare(m.revenue, m.cogs_share);
        if (derived != null) m.cogs = derived;
        else delete m.cogs;
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
      if (monthInputs.cogs == null) {
        const derived = computeCogsFromShare(monthInputs.revenue, monthInputs.cogs_share);
        if (derived != null) monthInputs.cogs = derived;
      }
      next[monthIso] = monthInputs;
    }
    setPlanInputsByMonth(next);
  }

  const totals = useMemo(() => {
    // WB starts from zero (its days are zero-filled). Marketplaces sum the known parts only: a missing day or a
    // component without data is skipped, and a total is unknown only when nothing in the period is known.
    const start = unknownAsNull ? null : 0;
    return (filtered || []).reduce(
      (acc, r) => {
        acc.revenue = addKnown(acc.revenue, toNumber(r.revenue, unknownAsNull));
        acc.commission = addKnown(acc.commission, toNumber(r.commission, unknownAsNull));
        acc.logistics = addKnown(acc.logistics, toNumber(r.logistics, unknownAsNull));
        acc.penalties = addKnown(acc.penalties, toNumber(r.penalties, unknownAsNull));
        acc.storage = addKnown(acc.storage, toNumber(r.storage, unknownAsNull));
        acc.ads = addKnown(acc.ads, toNumber(r.ads_spend, unknownAsNull));
        acc.cogs = addKnown(acc.cogs, toNumber(r.cogs, unknownAsNull));
        acc.tax = addKnown(acc.tax, toNumber(r.tax, unknownAsNull));
        acc.operation_expenses = addKnown(acc.operation_expenses, toNumber(r.operation_expenses, unknownAsNull));
        acc.margin = addKnown(acc.margin, toNumber(r.margin, unknownAsNull));
        acc.knownCostRevenue = addKnown(acc.knownCostRevenue, toNumber(r.known_cost_revenue, unknownAsNull));
        return acc;
      },
      {
        revenue: start,
        commission: start,
        logistics: start,
        penalties: start,
        storage: start,
        ads: start,
        cogs: start,
        tax: start,
        operation_expenses: start,
        margin: start,
        knownCostRevenue: start,
      },
    );
  }, [filtered, unknownAsNull]);

  // Days with no saved data at all (not a partial day): the KPI captions say how many are left out.
  const missingDays = unknownAsNull ? filtered.filter((r) => r.revenue == null).length : 0;

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
  // An unknown day is a gap in the line, not a point at zero.
  const revenueSeries = filtered.map((r) => toNumber(r.revenue, unknownAsNull));

  const marginKnown = isKnown(totals.margin) && isKnown(totals.revenue);
  const marginPercentOfRevenue = marginKnown ? (percentOf(totals.margin, totals.revenue) ?? 0) : 0;
  const marginIsPositive = marginKnown ? totals.margin >= 0 : true;

  const roiKnown = isKnown(totals.cogs) && isKnown(totals.margin);
  const roiPercent = roiKnown && totals.cogs > 0 ? (totals.margin / totals.cogs) * 100 : 0;
  const roiAvailable = roiKnown && totals.cogs > 0;

  const moneyKpi = (value) => (isKnown(value) ? `${formatNum(value)} ₽` : '—');
  // Share of the period's revenue that is covered by SKUs with a known cost (marketplace caption).
  const knownSharePct = isKnown(totals.knownCostRevenue) && totals.revenue > 0
    ? Math.round((totals.knownCostRevenue / totals.revenue) * 100)
    : null;

  return (
    <>
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
          value={moneyKpi(totals.revenue)}
          delta={unknownAsNull && missingDays > 0 ? `без учёта ${missingDays} дн.` : 'за период'}
          valueClassName=""
          bar={{ widthPct: 100, background: '#7c6ff7', opacity: 0.3 }}
        />
        <KpiCard
          label="Маржа"
          value={moneyKpi(totals.margin)}
          delta={unknownAsNull
            ? (marginKnown ? `пришло на баланс − себестоимость − налог${knownSharePct != null ? `; себестоимость известна для ${knownSharePct}% выручки` : ''}; опер. расходы: ${formatNum(totals.operation_expenses || 0)} ₽ внесено` : 'Прибыль пока неизвестна')
            : (marginKnown ? `${
            marginIsPositive ? '▲' : '▼'
          } ${Math.abs(marginPercentOfRevenue).toFixed(1)}% от выручки` : 'Прибыль пока неизвестна')}
          valueClassName=""
          deltaColor={marginKnown ? (marginIsPositive ? 'var(--green)' : 'var(--red)') : undefined}
          valueStyle={marginKnown ? { color: marginIsPositive ? 'var(--green)' : 'var(--red)' } : undefined}
          bar={{
            widthPct: marginKnown && marginPercentOfRevenue > 0 ? marginPercentOfRevenue : 0,
            background: marginIsPositive ? 'var(--green)' : 'var(--red)',
            opacity: 0.5,
          }}
        />
        <KpiCard
          label="ROI"
          value={roiKnown ? (roiAvailable ? `${Math.round(roiPercent)}%` : '0%') : '—'}
          delta={unknownAsNull
            ? (roiKnown ? 'маржа ÷ известная себестоимость' : 'Себестоимость неизвестна')
            : (roiKnown ? (roiAvailable ? 'за период' : 'Данные не внесены') : 'Себестоимость неизвестна')}
          valueStyle={{ color: 'var(--text-tertiary)' }}
          deltaColor="var(--text-tertiary)"
        />
      </div>

      <ChartCard title="Продажи, ₽" badge="сумма за день" labels={labels} data={revenueSeries} borderColor="#14b8a6" />

      <div className={detailsTable.cardClassName}>
        <div className="table-head-row">
          <h3>Детализация по дням</h3>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <TableExpandButton expanded={detailsTable.expanded} onToggle={detailsTable.toggle} />
            <div className="columns-menu" ref={columnsMenuRef}>
              <button
                type="button"
                className="icon-btn"
                aria-label="Настройки таблицы"
                aria-haspopup="menu"
                aria-expanded={columnsMenuOpen ? 'true' : 'false'}
                onClick={() => setColumnsMenuOpen((v) => !v)}
              >
                ⚙︎
              </button>
              {columnsMenuOpen && (
                <div className="columns-menu-pop" role="menu" aria-label="Настройки таблицы">
                  <div className="columns-menu-section">
                    <div className="columns-menu-title">План-факт</div>
                    <label className="columns-menu-item">
                      <input
                        type="checkbox"
                        checked={planFactOn}
                        disabled={!isWb}
                        title={isWb ? undefined : 'План-факт пока доступен только для Wildberries'}
                        onChange={(e) => {
                          const checked = Boolean(e.target.checked);
                          setPlanFactEnabled(checked);
                          setPlanFactEdit(false);
                        }}
                      />
                      <span>План-факт</span>
                    </label>
                  </div>
                  <div className="columns-menu-divider" />
                  <div className="columns-menu-section">
                    <div className="columns-menu-title">Столбцы</div>
                    {optionalColumns.map((c) => (
                      <label key={c.key} className="columns-menu-item">
                        <input
                          type="checkbox"
                          checked={new Set(effectiveVisibleKeys).has(c.key)}
                          onChange={(e) => {
                            const checked = Boolean(e.target.checked);
                            setVisibleColumnKeys((prev) => {
                              const cur = new Set(prev || []);
                              if (checked) cur.add(c.key);
                              else cur.delete(c.key);
                              return Array.from(cur);
                            });
                          }}
                        />
                        <span>{c.label}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>
            {planFactOn &&(
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
                {cols.map((c) => (
                  <th key={c.key}>{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={1 + cols.length} style={{ textAlign: 'center', padding: 16 }}>
                    Нет данных
                  </td>
                </tr>
              ) : (
                (() => {
                  // Требование: суммарная строка "ИТОГО" должна быть над блоком план-факт.
                  // Поэтому при включённом план-факте рисуем:
                  // 1) дневные строки (с разделителями по месяцам),
                  // 2) ИТОГО,
                  // 3) план-факт блоки по месяцам.
                  const dayRows = [];
                  const planRows = [];

                  function showDashForZero(v) {
                    if (v == null) return true;
                    const n = Number(v);
                    if (!Number.isFinite(n)) return false;
                    return n === 0;
                  }

                  for (const section of monthSections) {
                    const rows = section.rows || [];

                    if (planFactOn &&section.month) {
                      dayRows.push(
                        <tr key={`sep-days-${section.month}`} style={{ background: 'var(--bg-secondary)' }}>
                          <td className="left" colSpan={1 + cols.length} style={{ fontWeight: 700 }}>
                            {monthTitle(section.month)}
                          </td>
                        </tr>
                      );
                    }

                    for (const r of rows) {
                      const revenue = toNumber(r.revenue, unknownAsNull);
                      // Orders come from the WB funnel only; other marketplaces have no order sums.
                      const orders = isWb ? (ordersByDate[r.date] || 0) : null;
                      const commission = toNumber(r.commission, unknownAsNull);
                      const logistics = toNumber(r.logistics, unknownAsNull);
                      const penalties = toNumber(r.penalties, unknownAsNull);
                      const ads = toNumber(r.ads_spend, unknownAsNull);
                      const storage = toNumber(r.storage, unknownAsNull);
                      const opExp = toNumber(r.operation_expenses, unknownAsNull);
                      const cogs = toNumber(r.cogs, unknownAsNull);
                      const tax = toNumber(r.tax, unknownAsNull);
                      const margin = toNumber(r.margin, unknownAsNull);

                      const neg = margin != null && margin < 0;
                      const comPct = percentOf(commission, revenue);
                      const logPct = percentOf(logistics, revenue);
                      const reklPct = percentOf(ads, revenue);
                      const storPct = percentOf(storage, revenue);
                      const marginPct = percentOf(margin, revenue);
                      const roi = cogs == null || margin == null ? null : (cogs > 0 ? (margin / cogs) * 100 : 0);

                      const valueByKey = {
                        revenue,
                        orders_sum: orders,
                        commission,
                        commission_pct: comPct,
                        logistics,
                        logistics_pct: logPct,
                        penalties,
                        cogs,
                        cogs_share: percentOf(cogs, revenue),
                        tax,
                        ads_spend: ads,
                        ads_pct: reklPct,
                        storage,
                        storage_pct: storPct,
                        wb_expenses_share: percentOf(sumKnown([storage, commission, ads, logistics, penalties]), revenue),
                        operation_expenses: opExp,
                        margin,
                        margin_pct: marginPct,
                        roi,
                      };

                      dayRows.push(
                        <tr key={r.date}>
                          <td className="left">{formatDate(r.date)}</td>
                          {cols.map((c) => {
                            const v = valueByKey[c.key];
                            if (v == null) {
                              // Unknown for a marketplace; WB values are always numbers here.
                              return <td key={c.key}>—</td>;
                            }
                            if (c.key === 'operation_expenses') {
                              return (
                                <td key={c.key} style={{ color: 'var(--red)', fontWeight: 600 }}>
                                  {formatNum(v)}
                                </td>
                              );
                            }
                            if (c.key === 'tax') {
                              return (
                                <td key={c.key} style={{ color: 'var(--text-secondary)' }}>
                                  {formatNum(v)}
                                </td>
                              );
                            }
                            if (c.key === 'margin' || c.key === 'margin_pct') {
                              return (
                                <td key={c.key} style={{ fontWeight: 500, color: neg ? 'var(--red)' : 'var(--green)' }}>
                                  {c.isPercent ? `${(Number(v) || 0).toFixed(1)}%` : formatNum(v)}
                                </td>
                              );
                            }
                            if (c.isPercent) {
                              return (
                                <td key={c.key}>
                                  {(Number.isFinite(Number(v)) ? Number(v) : 0).toFixed(1)}%
                                </td>
                              );
                            }
                            return <td key={c.key}>{formatNum(v)}</td>;
                          })}
                        </tr>
                      );
                    }

                    if (planFactOn &&section.month) {
                      const monthData = planFactByMonth.get(section.month);
                      const metrics = Array.isArray(monthData?.metrics) ? monthData.metrics : [];
                      const byKey = new Map(metrics.map((x) => [x.metric_key, x]));

                      // Header for plan-fact block per month (below totals)
                      planRows.push(
                        <tr key={`sep-plan-${section.month}`} style={{ background: 'var(--bg-secondary)' }}>
                          <td className="left" colSpan={1 + cols.length} style={{ fontWeight: 700 }}>
                            {monthTitle(section.month)}
                          </td>
                        </tr>
                      );

                      const rowDefs = [
                        { label: 'План', kind: 'plan' },
                        { label: 'Факт', kind: 'fact' },
                        { label: '% выполнения', kind: 'pct_of_plan' },
                        { label: 'Прогноз выполнения', kind: 'forecast' },
                        {
                          label: (
                            <span style={{ display: 'inline-block', lineHeight: 1.1 }}>
                              Прогноз
                              <br />
                              % выполнения
                            </span>
                          ),
                          kind: 'forecast_pct_of_plan',
                        },
                      ];

                      for (const def of rowDefs) {
                        const isFirst = def.kind === 'plan';
                        const isLast = def.kind === 'forecast_pct_of_plan';
                        const blockBg = 'rgba(124, 111, 247, 0.06)'; // brand-ish, subtle
                        const blockBorder = 'rgba(124, 111, 247, 0.22)';
                        planRows.push(
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
                                if (showDashForZero(v)) {
                                  return <td key={c.key} style={{ color: 'var(--text-tertiary)' }}>—</td>;
                                }
                                return <td key={c.key} style={{ fontWeight: 700 }}>{`${Math.round(Number(v) * 100)}%`}</td>;
                              }

                              if (showDashForZero(v)) {
                                return <td key={c.key} style={{ color: 'var(--text-tertiary)' }}>—</td>;
                              }
                              return <td key={c.key}>{formatCellValue(c.key, v)}</td>;
                            })}
                          </tr>
                        );
                      }
                    }
                  }

                  const totalsRow = (
                    <tr key="totals" style={{ fontWeight: 500, background: 'var(--bg-secondary)' }}>
                      <td className="left">ИТОГО</td>
                      {cols.map((c) => {
                        const totalOrders = isWb ? Object.values(ordersByDate).reduce((a, b) => a + (Number(b) || 0), 0) : null;
                        const vByKey = {
                          revenue: totals.revenue,
                          orders_sum: totalOrders,
                          commission: totals.commission,
                          commission_pct: percentOf(totals.commission, totals.revenue),
                          logistics: totals.logistics,
                          logistics_pct: percentOf(totals.logistics, totals.revenue),
                          penalties: totals.penalties,
                          cogs: totals.cogs,
                          cogs_share: percentOf(totals.cogs, totals.revenue),
                          tax: totals.tax,
                          ads_spend: totals.ads,
                          ads_pct: percentOf(totals.ads, totals.revenue),
                          storage: totals.storage,
                          storage_pct: percentOf(totals.storage, totals.revenue),
                          wb_expenses_share: percentOf(
                            sumKnown([totals.storage, totals.commission, totals.ads, totals.logistics, totals.penalties]),
                            totals.revenue,
                          ),
                          operation_expenses: totals.operation_expenses,
                          margin: totals.margin,
                          margin_pct: percentOf(totals.margin, totals.revenue),
                          roi: roiKnown ? (totals.cogs > 0 ? (totals.margin / totals.cogs) * 100 : 0) : null,
                        };
                        const v = vByKey[c.key];
                        if (v == null) {
                          return <td key={c.key}>—</td>;
                        }
                        if (c.key === 'operation_expenses') {
                          return (
                            <td key={c.key} style={{ color: 'var(--red)', fontWeight: 600 }}>
                              {formatNum(v)}
                            </td>
                          );
                        }
                        if (c.key === 'tax') {
                          return (
                            <td key={c.key} style={{ color: 'var(--text-secondary)' }}>
                              {formatNum(v)}
                            </td>
                          );
                        }
                        if (c.key === 'margin' || c.key === 'margin_pct') {
                          return (
                            <td key={c.key} style={{ fontWeight: 500, color: 'var(--green)' }}>
                              {c.isPercent ? `${(Number(v) || 0).toFixed(1)}%` : formatNum(v)}
                            </td>
                          );
                        }
                        if (c.key === 'roi') {
                          return <td key={c.key}>{totals.cogs > 0 ? `${Math.round(roiPercent)}%` : '0%'}</td>;
                        }
                        if (c.isPercent) {
                          return <td key={c.key}>{(Number(v) || 0).toFixed(1)}%</td>;
                        }
                        return <td key={c.key}>{formatNum(v)}</td>;
                      })}
                    </tr>
                  );

                  if (!planFactOn) {
                    // Old behavior: day rows already include everything; planRows is empty.
                    return (
                      <>
                        {dayRows}
                        {totalsRow}
                      </>
                    );
                  }

                  return (
                    <>
                      {dayRows}
                      {totalsRow}
                      {planRows}
                    </>
                  );
                })()
              )}

            </tbody>
          </table>
          {planFactOn &&planFactLoading && (
            <div style={{ padding: 10, color: 'var(--text-tertiary)' }}>Загрузка план-факт...</div>
          )}
          {planFactOn &&planFactError && (
            <div className="alert alert-danger" style={{ marginTop: 10 }}>{planFactError}</div>
          )}
        </div>
      </div>

      </>
      )}
    </>
  );
}

