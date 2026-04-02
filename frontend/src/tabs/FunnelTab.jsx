/* eslint react-hooks/set-state-in-effect: off */
import { useState, useEffect } from 'react';
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

export default function FunnelTab({ range, refreshTrigger, cache, updateCache }) {
  const [rows, setRows] = useState(() => (cache?.funnel && Array.isArray(cache.funnel) ? cache.funnel : []));
  const [loading, setLoading] = useState(() => !(cache?.funnel?.length));
  const [error, setError] = useState('');

  const { dateFrom, dateTo } = range || {};

  useEffect(() => {
    setLoading(true);
    setError('');
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

  // При смене дат не мигать — показывать старые данные до прихода новых (как в GAS)
  const showFullLoader = loading && rows.length === 0;

  const filtered = (rows || []).filter((r) => {
    if (dateFrom && r.date < dateFrom) return false;
    if (dateTo && r.date > dateTo) return false;
    return true;
  });

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
            <th>Дата</th>
            <th>NM ID</th>
            <th>Артикул</th>
            <th>Переходы</th>
            <th>В корзину</th>
            <th>Заказы</th>
            <th>Сумма заказов</th>
            <th>Выкуп %</th>
            <th>CR в корзину</th>
            <th>CR в заказ</th>
          </tr>
        </thead>
        <tbody>
          {filtered.length === 0 ? (
            <tr>
              <td colSpan={10} className="text-center py-4">Нет данных</td>
            </tr>
          ) : (
            filtered.map((r, i) => (
              <tr key={`${r.date}-${r.nm_id}-${i}`}>
                <td>{formatDate(r.date)}</td>
                <td>{r.nm_id}</td>
                <td>{r.vendor_code || '—'}</td>
                <td>{formatNum(r.open_count)}</td>
                <td>{formatNum(r.cart_count)}</td>
                <td>{formatNum(r.order_count)}</td>
                <td>{formatNum(r.order_sum)}</td>
                <td>{r.buyout_percent != null ? Number(r.buyout_percent).toFixed(1) : '—'}%</td>
                <td>{r.cr_to_cart != null ? Number(r.cr_to_cart).toFixed(2) : '—'}</td>
                <td>{r.cr_to_order != null ? Number(r.cr_to_order).toFixed(2) : '—'}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
