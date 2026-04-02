/* eslint react-hooks/set-state-in-effect: off */
import { Fragment, useEffect, useMemo, useState } from 'react';
import * as api from '../api';
import DataTable from '../components/DataTable';

function formatNum(n) {
  if (n == null || n === '') return '—';
  return Math.round(Number(n)).toLocaleString();
}

function formatDate(iso) {
  if (!iso) return '';
  const parts = iso.split('-');
  return parts.length >= 2 ? `${parts[2]}.${parts[1]}` : iso;
}

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

export default function Articles({ range, refreshTrigger, cache, updateCache }) {
  const { dateFrom, dateTo } = range || {};

  const [sku, setSku] = useState(() => (cache?.sku && Array.isArray(cache.sku) ? cache.sku : []));
  const [articles, setArticles] = useState([]);
  const [loading, setLoading] = useState(() => !(cache?.sku?.length));
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState(new Set());
  const [funnelRows, setFunnelRows] = useState(() => (cache?.funnel && Array.isArray(cache.funnel) ? cache.funnel : []));

  useEffect(() => {
    api
      .getArticles()
      .then((data) => setArticles(Array.isArray(data) ? data : []))
      .catch(() => setArticles([]));
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

  // vendor_code берём из воронки (funnel_daily.vendor_code), потому что в `articles` он часто пустой
  useEffect(() => {
    if (!dateFrom || !dateTo) return;
    api
      .getFunnel(dateFrom, dateTo)
      .then((data) => {
        const list = Array.isArray(data) ? data : [];
        setFunnelRows(list);
        if (typeof updateCache === 'function') updateCache('funnel', list);
      })
      .catch(() => {});
  }, [dateFrom, dateTo, refreshTrigger, updateCache]);

  // Не мигать при смене дат — показывать старые данные, пока новые не пришли
  const showFullLoader = loading && sku.length === 0;

  const articleMap = useMemo(() => {
    const map = {};
    articles.forEach((a) => {
      map[a.nm_id] = {
        vendor_code: a.vendor_code || null,
        name: a.name || null,
      };
    });
    return map;
  }, [articles]);

  const sellerCodeMap = useMemo(() => {
    const map = {};
    for (const r of funnelRows || []) {
      if (!r || r.nm_id == null) continue;
      if (!r.vendor_code) continue;
      map[r.nm_id] = r.vendor_code;
    }
    return map;
  }, [funnelRows]);

  const filtered = useMemo(() => {
    return (sku || []).filter((r) => r.date >= dateFrom && r.date <= dateTo);
  }, [sku, dateFrom, dateTo]);

  const aggregated = useMemo(() => {
    const toActive = (x) => {
      return (
        Number(x.revenue) !== 0 ||
        Number(x.order_sum) !== 0 ||
        Number(x.commission) !== 0 ||
        Number(x.logistics) !== 0 ||
        Number(x.penalties) !== 0 ||
        Number(x.cogs) !== 0 ||
        Number(x.ads_spend) !== 0 ||
        Number(x.margin) !== 0
      );
    };

    const daysActive = (details) =>
      (details || []).filter((dd) =>
        toActive({
          revenue: dd.revenue,
          order_sum: dd.order_sum,
          commission: dd.commission,
          logistics: dd.logistics,
          penalties: dd.penalties,
          cogs: dd.cogs,
          ads_spend: dd.ads_spend,
          margin: dd.margin,
        }),
      );

    return aggregateSku(filtered)
      .map((i) => {
        const details = daysActive(i.details);
        const itemActive = toActive(i);
        if (!itemActive) return null;
        return {
          ...i,
          details,
          vendor_code: articleMap[i.nm_id]?.vendor_code || sellerCodeMap[i.nm_id] || null,
          name: articleMap[i.nm_id]?.name || i.name || '—',
        };
      })
      .filter(Boolean)
      .sort((a, b) => Number(b.margin) - Number(a.margin));
  }, [filtered, articleMap, sellerCodeMap]);

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
      <div className="loader-center">
        <div className="loader-spinner" />
        <p>Загрузка данных...</p>
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

  return (
    <DataTable title="Артикулы" tag={`${aggregated.length} артикулов`}>
      <table>
        <thead>
          <tr>
            <th className="left">Артикул ВБ</th>
            <th className="left">Товар</th>
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
            <th />
          </tr>
        </thead>
        <tbody>
          {aggregated.length === 0 ? (
            <tr>
              <td colSpan={17} style={{ textAlign: 'center', padding: 16 }}>
                Нет данных
              </td>
            </tr>
          ) : (
            aggregated.map((i) => (
              <Fragment key={i.nm_id}>
                <tr>
                  <td className="left art-name" style={{ fontWeight: 500 }}>
                    {i.nm_id}
                  </td>
                  <td className="left" style={{ color: 'var(--text-secondary)' }}>
                    {(() => {
                      const vc = i.vendor_code;
                      const nm = i.name && i.name !== '—' ? i.name : null;
                      if (vc && nm) return <span title={`${vc} - ${nm}`}>{`${vc} - ${nm}`}</span>;
                      if (vc && !nm) return <span title={vc}>{vc}</span>;
                      if (!vc && nm) return <span title={nm}>{nm}</span>;
                      return <span title="—">—</span>;
                    })()}
                  </td>
                  <td>{formatNum(i.revenue)}</td>
                  <td>{formatNum(i.order_sum)}</td>
                  <td>{formatNum(i.commission)}</td>
                  <td>{i.revenue > 0 ? ((i.commission / i.revenue) * 100).toFixed(0) : 0}%</td>
                  <td>{formatNum(i.logistics)}</td>
                  <td>{i.revenue > 0 ? ((i.logistics / i.revenue) * 100).toFixed(0) : 0}%</td>
                  <td>{formatNum(i.penalties)}</td>
                  <td>{formatNum(i.cogs)}</td>
                  <td>{i.revenue > 0 ? ((i.cogs / i.revenue) * 100).toFixed(1) : 0}%</td>
                  <td>{formatNum(i.ads_spend)}</td>
                  <td>{i.revenue > 0 ? ((i.ads_spend / i.revenue) * 100).toFixed(0) : 0}%</td>
                  <td style={{ color: i.margin >= 0 ? 'var(--green)' : 'var(--red)' }}>{formatNum(i.margin)}</td>
                  <td>{i.revenue > 0 ? ((i.margin / i.revenue) * 100).toFixed(0) : 0}%</td>
                  <td>{i.cogs > 0 ? Math.round((i.margin / i.cogs) * 100) : 0}%</td>
                  <td style={{ width: 1, whiteSpace: 'nowrap' }}>
                    {i.details.length > 0 ? (
                      <span className="expand-btn" onClick={() => toggle(i.nm_id)}>
                        {expanded.has(i.nm_id) ? '▲' : '▼'}
                      </span>
                    ) : null}
                  </td>
                </tr>

                {expanded.has(i.nm_id) &&
                  i.details.length > 0 &&
                  i.details.map((dd) => {
                    const comPct = dd.revenue > 0 ? (dd.commission / dd.revenue) * 100 : 0;
                    const logPct = dd.revenue > 0 ? (dd.logistics / dd.revenue) * 100 : 0;
                    const cogsPct = dd.revenue > 0 ? (dd.cogs / dd.revenue) * 100 : 0;
                    const adsPct = dd.revenue > 0 ? (dd.ads_spend / dd.revenue) * 100 : 0;
                    const marginPct = dd.revenue > 0 ? (dd.margin / dd.revenue) * 100 : 0;
                    const roi = dd.cogs > 0 ? Math.round((dd.margin / dd.cogs) * 100) : 0;
                    return (
                      <tr key={dd.date} className="articles-sub-row">
                        <td className="left" style={{ paddingLeft: 28, color: 'var(--text-tertiary)', fontSize: 11 }}>
                          {formatDate(dd.date)}
                        </td>
                        <td style={{ background: 'var(--bg-secondary)' }} />
                        <td style={{ fontSize: 11 }}>{formatNum(dd.revenue)}</td>
                        <td style={{ fontSize: 11 }}>{formatNum(dd.order_sum)}</td>
                        <td style={{ fontSize: 11 }}>{formatNum(dd.commission)}</td>
                        <td style={{ fontSize: 11 }}>{comPct.toFixed(0)}%</td>
                        <td style={{ fontSize: 11 }}>{formatNum(dd.logistics)}</td>
                        <td style={{ fontSize: 11 }}>{logPct.toFixed(0)}%</td>
                        <td style={{ fontSize: 11 }}>{formatNum(dd.penalties)}</td>
                        <td style={{ fontSize: 11 }}>{formatNum(dd.cogs)}</td>
                        <td style={{ fontSize: 11 }}>{cogsPct.toFixed(1)}%</td>
                        <td style={{ fontSize: 11 }}>
                          {formatNum(dd.ads_spend)} ₽
                        </td>
                        <td style={{ fontSize: 11 }}>{adsPct.toFixed(1)}%</td>
                        <td style={{ fontSize: 11, fontWeight: 700, color: dd.margin >= 0 ? 'var(--green)' : 'var(--red)' }}>
                          {formatNum(dd.margin)}
                        </td>
                        <td style={{ fontSize: 11 }}>{marginPct.toFixed(1)}%</td>
                        <td style={{ fontSize: 11 }}>{roi}%</td>
                        <td style={{ fontSize: 11 }} />
                      </tr>
                    );
                  })}
              </Fragment>
            ))
          )}
        </tbody>
      </table>
    </DataTable>
  );
}

