/* eslint react-hooks/set-state-in-effect: off */
import { Fragment, useEffect, useMemo, useState } from 'react';
import * as api from '../api';

function formatDateShort(iso) {
  if (!iso) return '';
  const parts = iso.split('-');
  return parts.length >= 2 ? `${parts[2]}.${parts[1]}` : iso;
}

function formatNum(n) {
  if (n == null || n === '') return '—';
  return Math.round(Number(n)).toLocaleString('ru');
}

function pct1(value) {
  return (value * 100).toFixed(1) + '%';
}

export default function Funnel({ range, refreshTrigger, cache, updateCache, dashboardState }) {
  const { dateFrom, dateTo } = range || {};

  const [rows, setRows] = useState(() => (cache?.funnel && Array.isArray(cache.funnel) ? cache.funnel : []));
  const [loading, setLoading] = useState(() => !(cache?.funnel?.length));
  const [error, setError] = useState('');

  const [skuRows, setSkuRows] = useState(() => (cache?.sku && Array.isArray(cache.sku) ? cache.sku : []));
  const [loadingSku, setLoadingSku] = useState(() => !(cache?.sku?.length));
  const [errorSku, setErrorSku] = useState('');

  const [articles, setArticles] = useState([]);
  const [errorArticles, setErrorArticles] = useState('');

  const [expanded, setExpanded] = useState(new Set()); // open/close article days
  const [expandedCategories, setExpandedCategories] = useState(new Set()); // open/close category groups (hidden by default)

  useEffect(() => {
    api
      .getArticles()
      .then((data) => setArticles(Array.isArray(data) ? data : []))
      .catch((e) => setErrorArticles(e.message || 'Ошибка загрузки'));
  }, [refreshTrigger]);

  useEffect(() => {
    if (!dateFrom || !dateTo) return;
    setLoading(true);
    setError('');
    setRows([]);
    api
      .getFunnel(dateFrom, dateTo)
      .then((data) => {
        const list = Array.isArray(data) ? data : [];
        setRows(list);
        if (typeof updateCache === 'function') updateCache('funnel', list);
      })
      .catch((e) => setError(e.message || 'Ошибка загрузки'))
      .finally(() => setLoading(false));
  }, [dateFrom, dateTo, refreshTrigger, updateCache]);

  useEffect(() => {
    if (!dateFrom || !dateTo) return;
    setLoadingSku(true);
    setErrorSku('');
    setSkuRows([]);
    api
      .getSku(dateFrom, dateTo)
      .then((data) => {
        const list = Array.isArray(data) ? data : [];
        setSkuRows(list);
        if (typeof updateCache === 'function') updateCache('sku', list);
      })
      .catch((e) => setErrorSku(e.message || 'Ошибка загрузки'))
      .finally(() => setLoadingSku(false));
  }, [dateFrom, dateTo, refreshTrigger, updateCache]);

  const showFullLoader = loading || loadingSku;

  const nmIdToSubject = useMemo(() => {
    const map = {};
    for (const a of articles || []) {
      map[a.nm_id] = a.subject_name || null;
    }
    return map;
  }, [articles]);

  const revenueByNmDate = useMemo(() => {
    const map = new Map();
    for (const r of skuRows || []) {
      if (!r || !r.date || r.nm_id == null) continue;
      map.set(`${r.nm_id}|${r.date}`, Number(r.revenue) || 0);
    }
    return map;
  }, [skuRows]);

  const categories = useMemo(() => {
    const byArticle = new Map();

    for (const r of rows || []) {
      const nmId = r.nm_id;
      if (nmId == null) continue;
      if (!byArticle.has(nmId)) {
        byArticle.set(nmId, {
          nm_id: nmId,
          vendor_code: r.vendor_code || null,
          subject: nmIdToSubject[nmId] || null,
          days: [],
        });
      }
      byArticle.get(nmId).days.push(r);
    }

    const activeArticles = [];

    for (const a of byArticle.values()) {
      const sortedDays = [...a.days].sort((x, y) => String(x.date).localeCompare(String(y.date)));

      const buyoutSum = sortedDays.reduce((acc, d) => acc + (d.buyout_percent != null ? Number(d.buyout_percent) || 0 : 0), 0);
      const buyoutDays = sortedDays.filter((d) => d.buyout_percent != null).length;
      const buyoutAvg = buyoutDays > 0 ? buyoutSum / buyoutDays : 0;

      const computedDays = sortedDays.map((d) => {
          const views = Number(d.open_count) || 0;
          const basket = Number(d.cart_count) || 0;
          const orders = Number(d.order_count) || 0;
          const crB = views > 0 ? basket / views : 0;
          const crO = basket > 0 ? orders / basket : 0;
          const totalCR = crB * crO;
          const sumOrders = Number(d.order_sum) || 0;
          const sumSales = revenueByNmDate.get(`${d.nm_id}|${d.date}`) || 0;
          const buyoutPercent = Number(d.buyout_percent) || 0;
          const buyout = Math.round(buyoutPercent);
          return {
            date: d.date,
            dLabel: formatDateShort(d.date),
            views,
            basket,
            orders,
            buyout,
            crB,
            crO,
            totalCR,
            sumOrders,
            sumSales,
          };
        });

      const totalViews = computedDays.reduce((acc, d) => acc + d.views, 0);
      const totalBasket = computedDays.reduce((acc, d) => acc + d.basket, 0);
      const totalOrders = computedDays.reduce((acc, d) => acc + d.orders, 0);
      const totalSumOrders = computedDays.reduce((acc, d) => acc + d.sumOrders, 0);
      const totalSumSales = computedDays.reduce((acc, d) => acc + d.sumSales, 0);

      // Показываем все даты в выбранном диапазоне, даже если метрики нулевые.
      // Это важно для прозрачной временной шкалы на вкладке "Воронка".
      if (computedDays.length === 0) continue;

      const crBasket = totalViews > 0 ? totalBasket / totalViews : 0;
      const crOrder = totalBasket > 0 ? totalOrders / totalBasket : 0;
      const totalCR = crBasket * crOrder;

      activeArticles.push({
        nm_id: a.nm_id,
        vendor_code: a.vendor_code,
        subject: a.subject || null,
        buyoutAvg,
        days: computedDays,
        views: totalViews,
        basket: totalBasket,
        orders: totalOrders,
        crBasket,
        crOrder,
        totalCR,
        totalSumOrders,
        totalSumSales,
      });
    }

    activeArticles.sort((x, y) => y.totalSumOrders - x.totalSumOrders);

    const bySubject = new Map();
    for (const art of activeArticles) {
      // Основная группировка: subject_name.
      // Если subject_name отсутствует (в БД может быть null) — группируем в одну "—".
      const key = art.subject || '—';
      if (!bySubject.has(key)) bySubject.set(key, []);
      bySubject.get(key).push(art);
    }

    const result = Array.from(bySubject.entries())
      .map(([key, arts]) => {
        const maxOrders = arts[0]?.totalSumOrders || 0;
        return { key, articles: arts, maxOrders };
      })
      .sort((a, b) => b.maxOrders - a.maxOrders);

    return result;
  }, [rows, revenueByNmDate, nmIdToSubject]);

  const categoryPreview = useMemo(() => {
    if (!categories?.length) return '';
    return `(${categories.length} групп)`;
  }, [categories]);

  const toggleArticle = (nmId) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(nmId)) next.delete(nmId);
      else next.add(nmId);
      return next;
    });
  };

  const toggleCategory = (subjectKey) => {
    setExpandedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(subjectKey)) next.delete(subjectKey);
      else next.add(subjectKey);
      return next;
    });
  };

  if (showFullLoader) {
    const fb = dashboardState?.funnel_ytd_backfill;
    const progressText = fb
      ? [
          fb.status ? `Статус: ${fb.status}` : null,
          fb.through_date ? `до ${new Date(fb.through_date + 'T12:00:00').toLocaleDateString('ru')}` : null,
          fb.last_completed_date ? `сейчас: ${new Date(fb.last_completed_date + 'T12:00:00').toLocaleDateString('ru')}` : null,
        ]
          .filter(Boolean)
          .join(' · ')
      : '';
    return (
      <div className="loader-center">
        <div className="loader-spinner" />
        <p style={{ fontWeight: 700 }}>Загружаем воронку…</p>
        <p style={{ color: 'var(--text-tertiary)', fontSize: 12, marginTop: 0 }}>
          Данные догружаются в фоне и могут появляться не сразу.
        </p>
        {progressText ? (
          <p style={{ color: 'var(--text-tertiary)', fontSize: 12, marginTop: 6 }}>{progressText}</p>
        ) : null}
        {fb?.status === 'error' && fb?.error_message ? (
          <div
            style={{
              marginTop: 10,
              padding: 10,
              border: '1px solid var(--red)',
              borderRadius: 10,
              color: 'var(--red)',
              maxWidth: 720,
            }}
          >
            Догрузка воронки остановилась: {fb.error_message}
          </div>
        ) : null}
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 16, border: '1px solid var(--red)', borderRadius: 10, color: 'var(--red)' }}>
        {error}
      </div>
    );
  }

  if (errorSku) {
    return (
      <div style={{ padding: 16, border: '1px solid var(--red)', borderRadius: 10, color: 'var(--red)' }}>
        {errorSku}
      </div>
    );
  }

  if (errorArticles) {
    return (
      <div style={{ padding: 16, border: '1px solid var(--red)', borderRadius: 10, color: 'var(--red)' }}>
        {errorArticles}
      </div>
    );
  }

  return (
    <div className="table-card">
      <div className="table-head-row">
        <h3>Воронка по артикулам</h3>
        <span className="tag tag-gray">категории скрыты, раскройте группу {categoryPreview}</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th className="left">Артикул</th>
              <th>Переходы</th>
              <th>В корзину</th>
              <th>Заказы</th>
              <th>Выкуп %</th>
              <th>CR корзина</th>
              <th>CR заказ</th>
              <th>Общий CR</th>
              <th>Сумма заказов ₽</th>
              <th>Сумма продаж ₽</th>
            </tr>
          </thead>
          <tbody>
            {categories.length === 0 ? (
              <tr>
                <td colSpan={10} style={{ textAlign: 'center', padding: 16 }}>
                  <div style={{ marginBottom: 8 }}>Данные воронки еще не готовы для выбранного периода</div>
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                    Попробуйте обновить данные WB и подождать завершения фоновой синхронизации.
                  </div>
                </td>
              </tr>
            ) : (
              categories.map((cat) => {
                const openCat = expandedCategories.has(cat.key);
                return (
                  <Fragment key={`cat-${cat.key}`}>
                    <tr className="category-row" onClick={() => toggleCategory(cat.key)}>
                      <td colSpan={10} style={{ textAlign: 'left' }}>
                        <span className="category-title">{cat.key}</span>
                        <span className="expand-icon">{openCat ? '▲' : '▼'}</span>
                      </td>
                    </tr>
                    {openCat &&
                      cat.articles.map((a) => {
                        const open = expanded.has(a.nm_id);
                        const art = a.vendor_code || String(a.nm_id);
                        return (
                          <Fragment key={`art-${a.nm_id}`}>
                            <tr className="art-header-row" onClick={() => toggleArticle(a.nm_id)}>
                              <td className="left" style={{ textAlign: 'left' }}>
                                <span style={{ color: 'var(--accent)', fontWeight: 500 }}>{art}</span>
                                <span className="expand-icon">{open ? '▲' : '▼'}</span>
                                <div style={{ fontSize: 10, color: 'var(--text-tertiary)', fontWeight: 400 }}>{a.nm_id}</div>
                              </td>
                              <td>{formatNum(a.views)}</td>
                              <td>{formatNum(a.basket)}</td>
                              <td>{formatNum(a.orders)}</td>
                              <td>{a.buyoutAvg.toFixed(0)}%</td>
                              <td>{pct1(a.crBasket)}</td>
                              <td>{pct1(a.crOrder)}</td>
                              <td style={{ color: 'var(--accent)', fontWeight: 500 }}>{pct1(a.totalCR)}</td>
                              <td>{formatNum(a.totalSumOrders)} ₽</td>
                              <td>{formatNum(a.totalSumSales)} ₽</td>
                            </tr>
                            {a.days.map((d) => {
                              const crT = d.totalCR;
                              return (
                                <tr key={`d-${a.nm_id}-${d.date}`} className={`day-row ${open ? 'open' : ''}`}>
                                  <td className="left" style={{ paddingLeft: 32, color: 'var(--text-tertiary)', fontSize: 11 }}>
                                    {d.dLabel}
                                  </td>
                                  <td style={{ fontSize: 11 }}>{d.views}</td>
                                  <td style={{ fontSize: 11 }}>{d.basket}</td>
                                  <td style={{ fontSize: 11 }}>{d.orders}</td>
                                  <td style={{ fontSize: 11 }}>{d.buyout}%</td>
                                  <td style={{ fontSize: 11 }}>{pct1(d.crB)}</td>
                                  <td style={{ fontSize: 11 }}>{pct1(d.crO)}</td>
                                  <td style={{ fontSize: 11, color: 'var(--accent)' }}>{pct1(crT)}</td>
                                  <td style={{ fontSize: 11 }}>{formatNum(d.sumOrders)} ₽</td>
                                  <td style={{ fontSize: 11 }}>{formatNum(d.sumSales)} ₽</td>
                                </tr>
                              );
                            })}
                          </Fragment>
                        );
                      })}
                  </Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

