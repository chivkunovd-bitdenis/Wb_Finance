import { useState, useEffect } from 'react';
import * as api from '../api';

export default function ArticlesTab({ range, refreshTrigger, cache, updateCache, onRefresh }) {
  const [articles, setArticles] = useState(() => (cache?.articles && Array.isArray(cache.articles) ? cache.articles : []));
  const [costs, setCosts] = useState(() => {
    const list = cache?.articles && Array.isArray(cache.articles) ? cache.articles : [];
    const c = {};
    list.forEach((a) => { c[a.nm_id] = a.cost_price != null ? String(a.cost_price) : ''; });
    return c;
  });
  const [loading, setLoading] = useState(() => !(cache?.articles?.length));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

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

  const setCost = (nmId, value) => {
    setCosts((prev) => ({ ...prev, [nmId]: value }));
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
    <>
      <div className="dashboard-card d-flex justify-content-between align-items-center mb-3">
        <h5 className="fw-bold mb-0">Себестоимость</h5>
        <button className="btn btn-success btn-sm" onClick={handleSave} disabled={saving}>
          {saving ? '...' : 'Сохранить'}
        </button>
      </div>
      <div className="table-wrapper shadow-sm">
        <table className="custom-table">
          <thead>
            <tr>
              <th>NM ID</th>
              <th>Название</th>
              <th>Себестоимость, ₽</th>
            </tr>
          </thead>
          <tbody>
            {articles.length === 0 ? (
              <tr>
                <td colSpan={3} className="text-center py-4">Нет данных</td>
              </tr>
            ) : (
              articles.map((a) => (
                <tr key={a.nm_id}>
                  <td>{a.nm_id}</td>
                  <td>{a.name || a.vendor_code || '—'}</td>
                  <td>
                    <input
                      type="number"
                      className="form-control form-control-sm"
                      style={{ width: 100 }}
                      value={costs[a.nm_id] ?? ''}
                      onChange={(e) => setCost(a.nm_id, e.target.value)}
                    />
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
