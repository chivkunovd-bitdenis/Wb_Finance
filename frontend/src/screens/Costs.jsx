/* eslint react-hooks/set-state-in-effect: off */
import { useEffect, useMemo, useState } from 'react';
import * as api from '../api';

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

export default function Costs({ range, refreshTrigger, cache, updateCache, onRefresh, onTaxRateChange }) {
  const [articles, setArticles] = useState(() => (cache?.articles && Array.isArray(cache.articles) ? cache.articles : []));
  const [costs, setCosts] = useState(() => {
    const list = cache?.articles && Array.isArray(cache.articles) ? cache.articles : [];
    const c = {};
    list.forEach((a) => {
      c[a.nm_id] = a.cost_price != null ? String(a.cost_price) : '';
    });
    return c;
  });

  const [loading, setLoading] = useState(() => !(cache?.articles?.length));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [expandedGroups, setExpandedGroups] = useState(new Set());
  const [funnelRows, setFunnelRows] = useState(() => (cache?.funnel && Array.isArray(cache.funnel) ? cache.funnel : []));

  useEffect(() => {
    setLoading(true);
    setError('');
    api
      .getArticles()
      .then((data) => {
        const list = Array.isArray(data) ? data : [];
        setArticles(list);
        if (typeof updateCache === 'function') updateCache('articles', list);

        const c = {};
        list.forEach((a) => {
          c[a.nm_id] = a.cost_price != null ? String(a.cost_price) : '';
        });
        setCosts(c);
      })
      .catch((e) => setError(e.message || 'Ошибка загрузки'))
      .finally(() => setLoading(false));
  }, [refreshTrigger, updateCache]);

  useEffect(() => {
    if (!range?.dateFrom || !range?.dateTo) return;
    api
      .getFunnel(range.dateFrom, range.dateTo)
      .then((data) => {
        const list = Array.isArray(data) ? data : [];
        setFunnelRows(list);
        if (typeof updateCache === 'function') updateCache('funnel', list);
      })
      .catch(() => {});
  }, [range?.dateFrom, range?.dateTo, refreshTrigger, updateCache]);

  const setCost = (nmId, value) => {
    setCosts((prev) => ({ ...prev, [nmId]: value }));
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
        seller_article: sellerArticle || '—',
        product_name: productName,
        group_key: groupKey,
      };
    });
  }, [articles, sellerCodeMap]);

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

  const handleSave = async () => {
    const items = articles.map((a) => ({
      nm_id: a.nm_id,
      cost_price: parseFloat(costs[a.nm_id]) || 0,
    }));
    setSaving(true);
    try {
      await api.saveArticlesCost(items);
      if (range?.dateFrom && range?.dateTo) {
        await api.triggerSyncRecalculate(range.dateFrom, range.dateTo);
      }
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

  return (
    <div className="seb-card">
      <TaxRateBlock onTaxRateChange={onTaxRateChange} />
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
          <button className="btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? '...' : 'Сохранить'}
          </button>
        </div>
      </div>

      <table className="seb-table">
        <thead>
          <tr>
            <th>Товар</th>
            <th>NM ID</th>
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
                <tr key={a.nm_id}>
                  <td style={{ color: 'var(--text-tertiary)' }}>{a.product_name}</td>
                  <td>{a.nm_id}</td>
                  <td>{a.seller_article}</td>
                  <td style={{ textAlign: 'right' }}>
                    <input
                      type="number"
                      className="cost-input"
                      value={costs[a.nm_id] ?? ''}
                      onChange={(e) => setCost(a.nm_id, e.target.value)}
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

