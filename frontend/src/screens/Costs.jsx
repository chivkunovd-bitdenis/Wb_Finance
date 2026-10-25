/* eslint react-hooks/set-state-in-effect: off */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as api from '../api';
import { useStore } from '../StoreContext';
import { isStaleStoreResponse, useActiveStoreId, useResetOnStoreChange } from '../storeDataGuard';

function TaxRateBlock({ onTaxRateChange }) {
  const [taxPct, setTaxPct] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    api
      .getTaxRate()
      .then((d) => {
        const pct = Math.round(Number(d.tax_rate) * 10000) / 100;
        setTaxPct(String(pct));
      })
      .catch(() => setLoadError('Не удалось загрузить ставку'));
  }, []);

  const handleSave = async () => {
    const val = parseFloat(String(taxPct).replace(',', '.'));
    if (Number.isNaN(val) || val < 0 || val > 100) {
      alert('Введите корректное значение от 0 до 100');
      return;
    }
    setSaving(true);
    setSaved(false);
    try {
      await api.saveTaxRate(val / 100);
      setSaved(true);
      if (typeof onTaxRateChange === 'function') onTaxRateChange(val / 100);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      alert('Ошибка сохранения: ' + (e.message || e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '12px 16px',
        background: 'var(--bg-secondary)',
        border: '0.5px solid var(--border-light)',
        borderRadius: 'var(--radius-md)',
        marginBottom: 16,
      }}
    >
      <span style={{ fontSize: 13, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
        Налоговая ставка
      </span>
      <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
        <input
          type="number"
          min="0"
          max="100"
          step="0.01"
          value={taxPct}
          onChange={(e) => { setTaxPct(e.target.value); setSaved(false); }}
          style={{
            background: 'var(--bg-primary)',
            border: '0.5px solid var(--border-light)',
            borderRadius: 'var(--radius-md)',
            padding: '6px 28px 6px 10px',
            fontSize: 13,
            color: 'var(--text-primary)',
            width: 80,
            outline: 'none',
            textAlign: 'right',
          }}
          placeholder="6"
        />
        <span
          style={{
            position: 'absolute',
            right: 8,
            fontSize: 13,
            color: 'var(--text-tertiary)',
            pointerEvents: 'none',
          }}
        >
          %
        </span>
      </div>
      <button className="btn-primary" onClick={handleSave} disabled={saving} style={{ minWidth: 90 }}>
        {saving ? '...' : saved ? '✓ Сохранено' : 'Сохранить'}
      </button>
      {loadError && (
        <span style={{ fontSize: 12, color: 'var(--red)' }}>{loadError}</span>
      )}
      <span style={{ fontSize: 12, color: 'var(--text-tertiary)', marginLeft: 4 }}>
        % от суммы продаж. Пересчёт подхватится при следующем автообновлении данных.
      </span>
    </div>
  );
}

/** Row identity: WB article numbers as they are; marketplace products by marketplace and id. */
function rowKeyOf(article, isWb) {
  return isWb ? String(article.nm_id) : `${article.marketplace || 'wb'}:${article.nm_id}`;
}

function idHeader(marketplace) {
  if (marketplace === 'ozon') return 'SKU Ozon';
  if (marketplace === 'all') return 'ID';
  return 'NM ID';
}

/**
 * The WB cost screen. For Ozon it saves each edited product through the marketplace endpoint, where an empty
 * field means "not set" (unknown profit) rather than the WB default of zero; the combined view is read-only.
 */
export default function Costs({ range, refreshTrigger, cache, updateCache, onRefresh, onTaxRateChange, marketplace = 'wb' }) {
  const storeId = useActiveStoreId();
  const { isOwnerStore } = useStore();
  const isWb = marketplace === 'wb';
  const editable = isWb || (marketplace === 'ozon' && isOwnerStore);
  const [articles, setArticles] = useState(() => (cache?.articles && Array.isArray(cache.articles) ? cache.articles : []));
  const [costs, setCosts] = useState(() => {
    const list = cache?.articles && Array.isArray(cache.articles) ? cache.articles : [];
    const c = {};
    list.forEach((a) => {
      c[rowKeyOf(a, isWb)] = a.cost_price != null ? String(a.cost_price) : '';
    });
    return c;
  });

  const [loading, setLoading] = useState(() => !(cache?.articles?.length));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [expandedGroups, setExpandedGroups] = useState(new Set());
  const [funnelRows, setFunnelRows] = useState(() => (cache?.funnel && Array.isArray(cache.funnel) ? cache.funnel : []));
  // Rows the user edits by hand: a background reload must not overwrite them.
  const dirtyCostsRef = useRef(new Set());

  const resetForStore = useCallback(() => {
    setArticles([]);
    setCosts({});
    dirtyCostsRef.current = new Set();
    setFunnelRows([]);
    setExpandedGroups(new Set());
    setError('');
    setLoading(true);
  }, []);

  useResetOnStoreChange(storeId, resetForStore);

  useEffect(() => {
    const reqStore = storeId;
    // Лоадер не включаем заново: он стоит с первого рендера (без кэша) и при смене магазина.
    // При фоновой перечитке (refreshTrigger во время досинхронизации) карточка остаётся на месте,
    // иначе блок налоговой ставки размонтируется и введённое значение пропадает.
    setError('');
    const request = isWb ? api.getArticles() : api.getMarketplaceArticles(marketplace);
    request
      .then((data) => {
        if (isStaleStoreResponse(reqStore, storeId)) return;
        const list = Array.isArray(data) ? data : [];
        setArticles(list);
        if (isWb && typeof updateCache === 'function') updateCache('articles', list, reqStore);

        const dirty = dirtyCostsRef.current;
        setCosts((prev) => {
          const c = {};
          list.forEach((a) => {
            const key = rowKeyOf(a, isWb);
            c[key] = dirty.has(key)
              ? (prev[key] ?? '')
              : a.cost_price != null ? String(a.cost_price) : '';
          });
          return c;
        });
      })
      .catch((e) => {
        if (!isStaleStoreResponse(reqStore, storeId)) setError(e.message || 'Ошибка загрузки');
      })
      .finally(() => {
        if (!isStaleStoreResponse(reqStore, storeId)) setLoading(false);
      });
  }, [refreshTrigger, updateCache, storeId, isWb, marketplace]);

  useEffect(() => {
    if (!isWb) {
      setFunnelRows([]);
      return;
    }
    if (!range?.dateFrom || !range?.dateTo) return;
    const reqStore = storeId;
    api
      .getFunnel(range.dateFrom, range.dateTo)
      .then((data) => {
        if (isStaleStoreResponse(reqStore, storeId)) return;
        const list = Array.isArray(data) ? data : [];
        setFunnelRows(list);
        if (typeof updateCache === 'function') updateCache('funnel', list, reqStore);
      })
      .catch(() => {});
  }, [range?.dateFrom, range?.dateTo, refreshTrigger, updateCache, storeId, isWb]);

  const setCost = (key, value) => {
    dirtyCostsRef.current.add(key);
    setCosts((prev) => ({ ...prev, [key]: value }));
  };

  const sellerCodeMap = useMemo(() => {
    const map = {};
    for (const r of funnelRows || []) {
      if (!r || r.nm_id == null || !r.vendor_code) continue;
      map[r.nm_id] = r.vendor_code;
    }
    return map;
  }, [funnelRows]);

  const normalizedArticles = useMemo(() => {
    return (articles || []).map((a) => {
      const sellerArticle = a.vendor_code || sellerCodeMap[a.nm_id] || '';
      const productName = a.name || `Товар ${a.nm_id}`;
      const groupKey = String(a.subject_name || 'Без категории');
      return {
        ...a,
        rowKey: rowKeyOf(a, isWb),
        seller_article: sellerArticle || '—',
        product_name: productName,
        group_key: groupKey,
      };
    });
  }, [articles, sellerCodeMap, isWb]);

  const filteredArticles = useMemo(() => {
    const s = String(search || '').trim();
    if (!s) return normalizedArticles;
    const needle = s.toLowerCase();
    return normalizedArticles.filter((a) => {
      const nm = String(a.nm_id || '');
      const sellerArticle = String(a.seller_article || '');
      const name = String(a.product_name || '');
      const subject = String(a.group_key || '');
      return (
        nm.includes(s) ||
        sellerArticle.toLowerCase().includes(needle) ||
        name.toLowerCase().includes(needle) ||
        subject.toLowerCase().includes(needle)
      );
    });
  }, [normalizedArticles, search]);

  const groupedByProduct = useMemo(() => {
    const groups = new Map();
    for (const a of filteredArticles) {
      const groupTitle = String(a.group_key || 'Без артикула продавца');
      if (!groups.has(groupTitle)) {
        groups.set(groupTitle, { groupTitle, items: [] });
      }
      groups.get(groupTitle).items.push(a);
    }
    return Array.from(groups.values())
      .map((g) => ({
        ...g,
        items: g.items.sort((x, y) => Number(x.nm_id) - Number(y.nm_id)),
      }))
      .sort((x, y) => x.groupTitle.localeCompare(y.groupTitle, 'ru'));
  }, [filteredArticles]);

  const toggleGroup = (groupTitle) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupTitle)) next.delete(groupTitle);
      else next.add(groupTitle);
      return next;
    });
  };

  const handleSaveWb = async () => {
    const items = articles.map((a) => ({
      nm_id: a.nm_id,
      cost_price: parseFloat(costs[rowKeyOf(a, true)]) || 0,
    }));
    await api.saveArticlesCost(items);
    dirtyCostsRef.current = new Set();
    if (range?.dateFrom && range?.dateTo) {
      await api.triggerSyncRecalculate(range.dateFrom, range.dateTo);
    }
  };

  const handleSaveMarketplace = async () => {
    // Only rows the owner changed are sent; an empty field clears the cost, so profit becomes unknown again.
    const byKey = new Map(articles.map((a) => [rowKeyOf(a, false), a]));
    for (const key of Array.from(dirtyCostsRef.current)) {
      const article = byKey.get(key);
      if (!article) {
        dirtyCostsRef.current.delete(key);
        continue;
      }
      const raw = String(costs[key] ?? '').trim().replace(',', '.');
      let value = null;
      if (raw !== '') {
        value = Number(raw);
        if (!Number.isFinite(value) || value < 0) {
          throw new Error(`Себестоимость товара ${article.nm_id} должна быть неотрицательным числом`);
        }
      }
      await api.saveMarketplaceProductCost(article.nm_id, value);
      dirtyCostsRef.current.delete(key);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      if (isWb) await handleSaveWb();
      else await handleSaveMarketplace();
      if (typeof onRefresh === 'function') onRefresh();
    } catch (e) {
      alert('Ошибка сохранения: ' + (e.message || e));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
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

  return (
    <div className="seb-card">
      <TaxRateBlock onTaxRateChange={onTaxRateChange} />
      {!isWb && !editable && (
        <div className="marketplace-notice marketplace-notice-info" data-testid="marketplace-cost-viewer-readonly">
          Себестоимость доступна для просмотра. Изменять её может владелец магазина.
        </div>
      )}
      {marketplace === 'all' && (
        <div className="marketplace-notice marketplace-notice-info">
          В общем режиме себестоимость показана для просмотра. Чтобы изменить цену товара Ozon, выберите Ozon отдельно.
        </div>
      )}
      <div className="seb-toolbar">
        <h3>Себестоимость</h3>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            style={{
              background: 'var(--bg-secondary)',
              border: '0.5px solid var(--border-light)',
              borderRadius: 'var(--radius-md)',
              padding: '6px 10px',
              fontSize: 12,
              color: 'var(--text-primary)',
              width: 200,
              outline: 'none',
            }}
            placeholder="Поиск: NM ID / артикул / товар"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {marketplace !== 'all' && (
            <button className="btn-primary" onClick={handleSave} disabled={saving || !editable}>
              {saving ? '...' : 'Сохранить'}
            </button>
          )}
        </div>
      </div>

      <table className="seb-table">
        <thead>
          <tr>
            <th>Товар</th>
            <th>{idHeader(marketplace)}</th>
            <th>Артикул продавца</th>
            <th style={{ textAlign: 'right' }}>Себестоимость, ₽</th>
          </tr>
        </thead>
        <tbody>
          {groupedByProduct.length === 0 ? (
            <tr>
              <td colSpan={4} style={{ textAlign: 'center', padding: 16 }}>
                Нет данных
              </td>
            </tr>
          ) : (
            groupedByProduct.flatMap((group) => {
              const isOpen = expandedGroups.has(group.groupTitle);
              const groupHeader = (
                <tr
                  key={`group-${group.groupTitle}`}
                  style={{ background: 'var(--bg-secondary)', cursor: 'pointer' }}
                  onClick={() => toggleGroup(group.groupTitle)}
                >
                  <td
                    style={{
                      textAlign: 'left',
                      fontWeight: 700,
                      color: 'var(--text-primary)',
                      borderTop: '1px solid var(--border-light)',
                    }}
                  >
                    {isOpen ? '▲' : '▼'} {group.groupTitle} ({group.items.length})
                  </td>
                  <td style={{ borderTop: '1px solid var(--border-light)' }} />
                  <td style={{ borderTop: '1px solid var(--border-light)' }} />
                  <td style={{ borderTop: '1px solid var(--border-light)' }} />
                </tr>
              );
              const groupItems = !isOpen
                ? []
                : group.items.map((a) => (
                <tr key={a.rowKey}>
                  <td style={{ color: 'var(--text-tertiary)' }}>{a.product_name}</td>
                  <td>{showMarketplaceTag ? `${a.marketplace === 'ozon' ? 'Ozon' : 'WB'} · ${a.nm_id}` : a.nm_id}</td>
                  <td>{a.seller_article}</td>
                  <td style={{ textAlign: 'right' }}>
                    <input
                      type="number"
                      className="cost-input"
                      value={costs[a.rowKey] ?? ''}
                      disabled={!isWb && (!editable || marketplace === 'all')}
                      onChange={(e) => setCost(a.rowKey, e.target.value)}
                    />
                  </td>
                </tr>
                  ));
              return [groupHeader, ...groupItems];
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
