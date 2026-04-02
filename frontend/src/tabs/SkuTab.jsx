/* eslint react-hooks/set-state-in-effect: off */
import { useState, useEffect, Fragment } from 'react';
import * as api from '../api';

function formatNum(n) {
  if (n == null || n === '') return '—';
  return Math.round(Number(n)).toLocaleString();
}

function formatDate(iso) {
  if (!iso) return '';
  const parts = iso.split('-');
  return parts.length >= 2 ? `${parts[2]}.${parts[1]}` : iso;
}

// Агрегация sku по nm_id за период (как в GAS processSkuData)
function aggregateSku(rows) {
  const map = {};
  for (const r of rows || []) {
    const nm = r.nm_id;
    if (!map[nm]) {
      map[nm] = {
        nm_id: nm,
        name: null,
        revenue: 0,
        commission: 0,
        logistics: 0,
        penalties: 0,
        ads_spend: 0,
        cogs: 0,
        margin: 0,
        order_sum: 0,
        details: [],
      };
    }
    const rev = Number(r.revenue) || 0;
    map[nm].revenue += rev;
    map[nm].commission += Number(r.commission) || 0;
    map[nm].logistics += Number(r.logistics) || 0;
    map[nm].penalties += Number(r.penalties) || 0;
    map[nm].ads_spend += Number(r.ads_spend) || 0;
    map[nm].cogs += Number(r.cogs) || 0;
    map[nm].margin += Number(r.margin) || 0;
    map[nm].order_sum += Number(r.order_sum) || 0;
    map[nm].details.push({
      date: r.date,
      revenue: rev,
      commission: Number(r.commission) || 0,
      logistics: Number(r.logistics) || 0,
      penalties: Number(r.penalties) || 0,
      cogs: Number(r.cogs) || 0,
      ads_spend: Number(r.ads_spend) || 0,
      margin: Number(r.margin) || 0,
      order_sum: Number(r.order_sum) || 0,
    });
  }
  return Object.values(map).sort((a, b) => b.revenue - a.revenue);
}

export default function SkuTab({ range, refreshTrigger, cache, updateCache }) {
  const [sku, setSku] = useState(() => (cache?.sku && Array.isArray(cache.sku) ? cache.sku : []));
  const [articles, setArticles] = useState([]);
  const [loading, setLoading] = useState(() => !(cache?.sku?.length));
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState(new Set());

  const { dateFrom, dateTo } = range || {};

  useEffect(() => {
    api.getArticles().then((data) => setArticles(Array.isArray(data) ? data : [])).catch(() => setArticles([]));
  }, [refreshTrigger]);

  useEffect(() => {
    if (!dateFrom || !dateTo) return;
    setLoading(true);
    setError('');
    api
      .getSku(dateFrom, dateTo)
      .then((data) => {
        const list = Array.isArray(data) ? data : [];
        setSku(list);
        if (typeof updateCache === 'function') updateCache('sku', list);
      })
      .catch((e) => setError(e.message || 'Ошибка загрузки'))
      .finally(() => setLoading(false));
  }, [dateFrom, dateTo, refreshTrigger, updateCache]);

  // При смене дат не мигать — показывать старые данные до прихода новых (как в GAS)
  const showFullLoader = loading && sku.length === 0;

  const articleMap = {};
  articles.forEach((a) => { articleMap[a.nm_id] = a.name || a.vendor_code || ''; });

  const filtered = (sku || []).filter((r) => r.date >= dateFrom && r.date <= dateTo);
  const aggregated = aggregateSku(filtered).map((i) => ({ ...i, name: articleMap[i.nm_id] || i.name || '—' }));

  const toggle = (nmId) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(nmId)) next.delete(nmId);
      else next.add(nmId);
      return next;
    });
  };

  if (showFullLoader) {
    return (
      <div className="text-center py-5">
        <div className="spinner-border text-primary" />
        <p className="mt-2">Загрузка данных...</p>
      </div>
    );
  }

  if (error) {
    return <div className="alert alert-danger">{error}</div>;
  }

  return (
    <div className="table-wrapper shadow-sm">
      <table className="custom-table">
        <thead>
          <tr>
            <th>Артикул ВБ</th>
            <th>Товар</th>
            <th>Выручка</th>
            <th>Заказы ₽</th>
            <th>Ком</th>
            <th>% комиссии</th>
            <th>Лог</th>
            <th>% логистики</th>
            <th>Штрафы</th>
            <th>Себес</th>
            <th>% себеса</th>
            <th>Рекл</th>
            <th>% рекламы</th>
            <th>Маржа</th>
            <th>% маржи</th>
            <th>ROI</th>
            <th>Доп</th>
          </tr>
        </thead>
        <tbody>
          {aggregated.length === 0 ? (
            <tr>
              <td colSpan={17} className="text-center py-4">Нет данных</td>
            </tr>
          ) : (
            aggregated.map((i) => (
              <Fragment key={i.nm_id}>
                <tr>
                  <td>{i.nm_id}</td>
                  <td className="sku-name-cell">
                    <span title={i.name || ''}>{i.name}</span>
                  </td>
                  <td className="fw-bold">{formatNum(i.revenue)}</td>
                  <td>{formatNum(i.order_sum)}</td>
                  <td>{formatNum(i.commission)}</td>
                  <td>{i.revenue > 0 ? (i.commission / i.revenue * 100).toFixed(0) : 0}%</td>
                  <td>{formatNum(i.logistics)}</td>
                  <td>{i.revenue > 0 ? (i.logistics / i.revenue * 100).toFixed(0) : 0}%</td>
                  <td>{formatNum(i.penalties)}</td>
                  <td>{formatNum(i.cogs)}</td>
                  <td>{i.revenue > 0 ? (i.cogs / i.revenue * 100).toFixed(1) : 0}%</td>
                  <td className="text-primary fw-bold">{formatNum(i.ads_spend)}</td>
                  <td>{i.revenue > 0 ? (i.ads_spend / i.revenue * 100).toFixed(0) : 0}%</td>
                  <td style={{ color: i.margin >= 0 ? '#10ac84' : '#ee5253' }}>{formatNum(i.margin)}</td>
                  <td>{i.revenue > 0 ? (i.margin / i.revenue * 100).toFixed(0) : 0}%</td>
                  <td>{i.cogs > 0 ? Math.round(i.margin / i.cogs * 100) : 0}%</td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-sm btn-light"
                      onClick={() => toggle(i.nm_id)}
                    >
                      {expanded.has(i.nm_id) ? '▲' : '▼'}
                    </button>
                  </td>
                </tr>
                {expanded.has(i.nm_id) && (
                  <tr key={`${i.nm_id}-detail`} className="bg-light">
                    <td colSpan={17} className="p-3">
                      <table className="table table-sm text-center table-bordered" style={{ fontSize: '0.75rem', background: '#fff' }}>
                        <thead>
                          <tr className="table-secondary">
                            <th>Дата</th>
                            <th>Выручка</th>
                            <th>Заказы ₽</th>
                            <th>% комиссии</th>
                            <th>Логистика</th>
                            <th>% логистики</th>
                            <th>Штрафы</th>
                            <th>Себес</th>
                            <th>Реклама</th>
                            <th>% рекламы</th>
                            <th>Маржа</th>
                            <th>% маржи</th>
                            <th>ROI</th>
                          </tr>
                        </thead>
                        <tbody>
                          {i.details.map((dd) => (
                            <tr key={dd.date}>
                              <td>{formatDate(dd.date)}</td>
                              <td>{formatNum(dd.revenue)}</td>
                              <td>{formatNum(dd.order_sum)}</td>
                              <td>{dd.revenue > 0 ? (dd.commission / dd.revenue * 100).toFixed(0) : 0}%</td>
                              <td>{formatNum(dd.logistics)}</td>
                              <td>{dd.revenue > 0 ? (dd.logistics / dd.revenue * 100).toFixed(0) : 0}%</td>
                              <td>{formatNum(dd.penalties)}</td>
                              <td>{formatNum(dd.cogs)}</td>
                              <td>{formatNum(dd.ads_spend)} ₽</td>
                              <td className="text-primary fw-bold">{dd.revenue > 0 ? (dd.ads_spend / dd.revenue * 100).toFixed(1) : 0}%</td>
                              <td style={{ fontWeight: 700, color: dd.margin >= 0 ? 'green' : 'red' }}>{formatNum(dd.margin)}</td>
                              <td>{dd.revenue > 0 ? (dd.margin / dd.revenue * 100).toFixed(1) : 0}%</td>
                              <td>{dd.cogs > 0 ? Math.round(dd.margin / dd.cogs * 100) : 0}%</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
