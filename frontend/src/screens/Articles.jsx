/* eslint react-hooks/set-state-in-effect: off */
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import * as api from '../api';
import DataTable from '../components/DataTable';
import { isStaleStoreResponse, useActiveStoreId, useResetOnStoreChange } from '../storeDataGuard';
import { addKnown, isKnown, toNumber } from '../marketplaceValues';

const SUM_FIELDS = ['revenue', 'commission', 'logistics', 'penalties', 'ads_spend', 'cogs', 'tax', 'margin', 'order_sum'];

function formatNum(n) {
  if (n == null || n === '') return '—';
  return Math.round(Number(n)).toLocaleString();
}

function formatDate(iso) {
  if (!iso) return '';
  const parts = iso.split('-');
  return parts.length >= 2 ? `${parts[2]}.${parts[1]}` : iso;
}

/**
 * Percent of revenue as printed in the table. Main rows print a bare "0%" without positive revenue (WB layout);
 * expanded day rows print the fixed-point zero. Unknown values are a dash for marketplaces.
 */
function percentLabel(part, base, digits, { unknownAsNull, bareZero }) {
  if (unknownAsNull && (!isKnown(part) || !isKnown(base))) return '—';
  if (base > 0) return `${((part / base) * 100).toFixed(digits)}%`;
  return bareZero ? '0%' : `${(0).toFixed(digits)}%`;
}

function roiLabel(margin, cogs, unknownAsNull) {
  if (unknownAsNull && (!isKnown(margin) || !isKnown(cogs))) return '—';
  return cogs > 0 ? `${Math.round((margin / cogs) * 100)}%` : '0%';
}

/** Activity for the row filter: an unknown value keeps a marketplace row visible (with dashes); a known zero does not. */
function isActiveValue(value, unknownAsNull) {
  if (!isKnown(value)) return unknownAsNull;
  return Number(value) !== 0;
}

/** Row key: WB article numbers stay as they are; marketplace products are identified by marketplace and id. */
function productKey(row, unknownAsNull) {
  return unknownAsNull ? `${row.marketplace || 'wb'}:${row.nm_id}` : row.nm_id;
}

function sortValue(value) {
  return isKnown(value) ? Number(value) : -Infinity;
}

function aggregateSku(rows, unknownAsNull) {
  const map = {};
  for (const r of rows || []) {
    const key = productKey(r, unknownAsNull);
    if (!map[key]) {
      // WB sums start from zero; marketplaces sum the known days only (see addKnown).
      const start = unknownAsNull ? null : 0;
      map[key] = { key, nm_id: r.nm_id, marketplace: r.marketplace || 'wb', name: null, details: [] };
      for (const field of SUM_FIELDS) map[key][field] = start;
    }
    const item = map[key];
    for (const field of SUM_FIELDS) {
      item[field] = addKnown(item[field], toNumber(r[field], unknownAsNull));
    }
    item.details.push({
      date: r.date,
      revenue: toNumber(r.revenue, unknownAsNull),
      commission: toNumber(r.commission, unknownAsNull),
      logistics: toNumber(r.logistics, unknownAsNull),
      penalties: toNumber(r.penalties, unknownAsNull),
      cogs: toNumber(r.cogs, unknownAsNull),
      ads_spend: toNumber(r.ads_spend, unknownAsNull),
      tax: toNumber(r.tax, unknownAsNull),
      margin: toNumber(r.margin, unknownAsNull),
      order_sum: toNumber(r.order_sum, unknownAsNull),
    });
  }
  return Object.values(map).sort((a, b) => sortValue(b.revenue) - sortValue(a.revenue));
}

function idHeader(marketplace) {
  if (marketplace === 'ozon') return 'SKU Ozon';
  if (marketplace === 'all') return 'Артикул';
  return 'Артикул ВБ';
}

/**
 * The WB articles table. For Ozon and the combined view it shows the same columns from the marketplace
 * endpoints; orders come from the WB funnel only, so they are unknown elsewhere.
 */
export default function Articles({ range, refreshTrigger, cache, updateCache, marketplace = 'wb' }) {
  const { dateFrom, dateTo } = range || {};
  const storeId = useActiveStoreId();
  const isWb = marketplace === 'wb';
  const unknownAsNull = !isWb;

  const [sku, setSku] = useState(() => (cache?.sku && Array.isArray(cache.sku) ? cache.sku : []));
  const [articles, setArticles] = useState([]);
  const [loading, setLoading] = useState(() => !(cache?.sku?.length));
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState(new Set());
  const [funnelRows, setFunnelRows] = useState(() => (cache?.funnel && Array.isArray(cache.funnel) ? cache.funnel : []));

  const resetForStore = useCallback(() => {
    setSku([]);
    setArticles([]);
    setFunnelRows([]);
    setExpanded(new Set());
    setError('');
    setLoading(true);
  }, []);

  useResetOnStoreChange(storeId, resetForStore);

  useEffect(() => {
    const reqStore = storeId;
    const request = isWb ? api.getArticles() : api.getMarketplaceArticles(marketplace);
    request
      .then((data) => {
        if (isStaleStoreResponse(reqStore, storeId)) return;
        setArticles(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        if (!isStaleStoreResponse(reqStore, storeId)) setArticles([]);
      });
  }, [refreshTrigger, storeId, isWb, marketplace]);

  useEffect(() => {
    if (!dateFrom || !dateTo) return;
    const reqStore = storeId;
    setLoading(true);
    setError('');
    const request = isWb ? api.getSku(dateFrom, dateTo) : api.getMarketplaceSku(marketplace, dateFrom, dateTo);
    request
      .then((data) => {
        if (isStaleStoreResponse(reqStore, storeId)) return;
        const list = Array.isArray(data) ? data : [];
        setSku(list);
        if (isWb && typeof updateCache === 'function') updateCache('sku', list, reqStore);
      })
      .catch((e) => {
        if (!isStaleStoreResponse(reqStore, storeId)) setError(e.message || 'Ошибка загрузки');
      })
      .finally(() => {
        if (!isStaleStoreResponse(reqStore, storeId)) setLoading(false);
      });
  }, [dateFrom, dateTo, refreshTrigger, updateCache, storeId, isWb, marketplace]);

  // vendor_code берём из воронки (funnel_daily.vendor_code), потому что в `articles` он часто пустой
  useEffect(() => {
    if (!isWb) {
      setFunnelRows([]);
      return;
    }
    if (!dateFrom || !dateTo) return;
    const reqStore = storeId;
    api
      .getFunnel(dateFrom, dateTo)
      .then((data) => {
        if (isStaleStoreResponse(reqStore, storeId)) return;
        const list = Array.isArray(data) ? data : [];
        setFunnelRows(list);
        if (typeof updateCache === 'function') updateCache('funnel', list, reqStore);
      })
      .catch(() => {});
  }, [dateFrom, dateTo, refreshTrigger, updateCache, storeId, isWb]);

  // Не мигать при смене дат — показывать старые данные, пока новые не пришли
  const showFullLoader = loading && sku.length === 0;

  const articleMap = useMemo(() => {
    const map = {};
    articles.forEach((a) => {
      map[productKey(a, unknownAsNull)] = {
        vendor_code: a.vendor_code || null,
        name: a.name || null,
      };
    });
    return map;
  }, [articles, unknownAsNull]);

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
    // WB keeps its original activity rule; marketplaces do not count orders, which are unknown there.
    const activeFields = isWb
      ? ['revenue', 'order_sum', 'commission', 'logistics', 'penalties', 'cogs', 'ads_spend', 'margin']
      : ['revenue', 'commission', 'logistics', 'penalties', 'cogs', 'ads_spend', 'margin'];
    const toActive = (x) => activeFields.some((field) => isActiveValue(x[field], unknownAsNull));

    const daysActive = (details) => (details || []).filter((dd) => toActive(dd));

    return aggregateSku(filtered, unknownAsNull)
      .map((i) => {
        const details = daysActive(i.details);
        const itemActive = toActive(i);
        if (!itemActive) return null;
        const info = articleMap[i.key] || {};
        return {
          ...i,
          details,
          vendor_code: info.vendor_code || (isWb ? sellerCodeMap[i.nm_id] : null) || null,
          name: info.name || i.name || '—',
        };
      })
      .filter(Boolean)
      .sort((a, b) => sortValue(b.margin) - sortValue(a.margin));
  }, [filtered, articleMap, sellerCodeMap, isWb, unknownAsNull]);

  const toggle = (rowKey) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(rowKey)) next.delete(rowKey);
      else next.add(rowKey);
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

  const showMarketplaceTag = marketplace === 'all';
  const marginColor = (value) => {
    if (!isKnown(value)) return undefined;
    return value >= 0 ? 'var(--green)' : 'var(--red)';
  };

  return (
    <DataTable title="Артикулы" tag={`${aggregated.length} артикулов`}>
      <table>
        <thead>
          <tr>
            <th className="left">{idHeader(marketplace)}</th>
            <th className="left">Артикул</th>
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
            <th>Налог</th>
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
              <td colSpan={19} style={{ textAlign: 'center', padding: 16 }}>
                Нет данных
              </td>
            </tr>
          ) : (
            aggregated.map((i) => (
              <Fragment key={i.key}>
                <tr>
                  <td className="left art-name" style={{ fontWeight: 500 }}>
                    {showMarketplaceTag ? `${i.marketplace === 'ozon' ? 'Ozon' : 'WB'} · ${i.nm_id}` : i.nm_id}
                  </td>
                  <td className="left" style={{ color: 'var(--text-secondary)' }}>
                    {(() => {
                      const vc = i.vendor_code;
                      if (vc) return <span title={vc}>{vc}</span>;
                      return <span title="—">—</span>;
                    })()}
                  </td>
                  <td className="left" style={{ color: 'var(--text-secondary)' }}>
                    {i.name && i.name !== '—' ? <span title={i.name}>{i.name}</span> : <span title="—">—</span>}
                  </td>
                  <td>{formatNum(i.revenue)}</td>
                  <td>{isWb ? formatNum(i.order_sum) : '—'}</td>
                  <td>{formatNum(i.commission)}</td>
                  <td>{percentLabel(i.commission, i.revenue, 0, { unknownAsNull, bareZero: true })}</td>
                  <td>{formatNum(i.logistics)}</td>
                  <td>{percentLabel(i.logistics, i.revenue, 0, { unknownAsNull, bareZero: true })}</td>
                  <td>{formatNum(i.penalties)}</td>
                  <td>{formatNum(i.cogs)}</td>
                  <td>{percentLabel(i.cogs, i.revenue, 1, { unknownAsNull, bareZero: true })}</td>
                  <td style={{ color: 'var(--text-secondary)' }}>{formatNum(i.tax)}</td>
                  <td>{formatNum(i.ads_spend)}</td>
                  <td>{percentLabel(i.ads_spend, i.revenue, 0, { unknownAsNull, bareZero: true })}</td>
                  <td style={{ color: marginColor(i.margin) }}>{formatNum(i.margin)}</td>
                  <td>{percentLabel(i.margin, i.revenue, 0, { unknownAsNull, bareZero: true })}</td>
                  <td>{roiLabel(i.margin, i.cogs, unknownAsNull)}</td>
                  <td style={{ width: 1, whiteSpace: 'nowrap' }}>
                    {i.details.length > 0 ? (
                      <span className="expand-btn" onClick={() => toggle(i.key)}>
                        {expanded.has(i.key) ? '▲' : '▼'}
                      </span>
                    ) : null}
                  </td>
                </tr>

                {expanded.has(i.key) &&
                  i.details.length > 0 &&
                  i.details.map((dd) => {
                    const detailKey = `${i.key}|${dd.date}`;
                    return (
                      <tr key={detailKey} className="articles-sub-row">
                        <td className="left" style={{ paddingLeft: 28, color: 'var(--text-tertiary)', fontSize: 11 }}>
                          {formatDate(dd.date)}
                        </td>
                        <td style={{ background: 'var(--bg-secondary)' }} />
                        <td style={{ background: 'var(--bg-secondary)' }} />
                        <td style={{ fontSize: 11 }}>{formatNum(dd.revenue)}</td>
                        <td style={{ fontSize: 11 }}>{isWb ? formatNum(dd.order_sum) : '—'}</td>
                        <td style={{ fontSize: 11 }}>{formatNum(dd.commission)}</td>
                        <td style={{ fontSize: 11 }}>{percentLabel(dd.commission, dd.revenue, 0, { unknownAsNull, bareZero: false })}</td>
                        <td style={{ fontSize: 11 }}>{formatNum(dd.logistics)}</td>
                        <td style={{ fontSize: 11 }}>{percentLabel(dd.logistics, dd.revenue, 0, { unknownAsNull, bareZero: false })}</td>
                        <td style={{ fontSize: 11 }}>{formatNum(dd.penalties)}</td>
                        <td style={{ fontSize: 11 }}>{formatNum(dd.cogs)}</td>
                        <td style={{ fontSize: 11 }}>{percentLabel(dd.cogs, dd.revenue, 1, { unknownAsNull, bareZero: false })}</td>
                        <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{formatNum(dd.tax)}</td>
                        <td style={{ fontSize: 11 }}>
                          {formatNum(dd.ads_spend)} {isKnown(dd.ads_spend) ? '₽' : ''}
                        </td>
                        <td style={{ fontSize: 11 }}>{percentLabel(dd.ads_spend, dd.revenue, 1, { unknownAsNull, bareZero: false })}</td>
                        <td style={{ fontSize: 11, fontWeight: 700, color: marginColor(dd.margin) }}>
                          {formatNum(dd.margin)}
                        </td>
                        <td style={{ fontSize: 11 }}>{percentLabel(dd.margin, dd.revenue, 1, { unknownAsNull, bareZero: false })}</td>
                        <td style={{ fontSize: 11 }}>{roiLabel(dd.margin, dd.cogs, unknownAsNull)}</td>
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
