/* eslint react-hooks/set-state-in-effect: off */
import { useEffect, useMemo, useState } from 'react';
import * as api from '../api';

function formatNum(n) {
  if (n == null || n === '') return '—';
  return Math.round(Number(n)).toLocaleString('ru');
}

function formatDate(iso) {
  if (!iso) return '';
  const parts = iso.split('-');
  return parts.length >= 2 ? `${parts[2]}.${parts[1]}` : iso;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForPnlOperationExpense(dateIso, expectedAmount, { timeoutMs = 60000, intervalMs = 2000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  const expectedRounded = Math.round(Number(expectedAmount) * 100) / 100;

  while (Date.now() < deadline) {
    try {
      const pnl = await api.getPnl(dateIso, dateIso);
      const list = Array.isArray(pnl) ? pnl : [];
      const row = list.find((x) => x.date === dateIso) || list[0];
      const current = row?.operation_expenses;
      const currentNum = Number(current);
      if (Number.isFinite(currentNum) && Math.abs(currentNum - expectedRounded) < 0.01) {
        return true;
      }
    } catch (e) {
      // Unauthorized нужно показать пользователю сразу (иначе можно долго ждать зря).
      if (e && e.message === 'unauthorized') throw e;
      // Иначе продолжаем polling до таймаута.
    }

    await sleep(intervalMs);
  }

  return false;
}

export default function OperationalExpenses({ range, refreshTrigger, cache, updateCache, onRefresh }) {
  const { dateFrom, dateTo } = range || {};

  const [items, setItems] = useState(() => {
    const c = cache?.operational_expenses;
    return Array.isArray(c) ? c : [];
  });
  const [loading, setLoading] = useState(() => !(cache?.operational_expenses && Array.isArray(cache.operational_expenses) && cache.operational_expenses.length));
  const [error, setError] = useState('');

  const [formOpen, setFormOpen] = useState(false);
  const [mode, setMode] = useState('create'); // create | edit
  const [saving, setSaving] = useState(false);

  const defaultDate = useMemo(() => dateFrom || new Date().toISOString().split('T')[0], [dateFrom]);
  const [form, setForm] = useState(() => ({
    id: null,
    date: defaultDate,
    amount: '',
    comment: '',
  }));

  useEffect(() => {
    // Если дат на экране ещё нет — не трогаем форму/загрузку.
    if (!dateFrom || !dateTo) return;

    setLoading(true);
    setError('');
    api
      .getOperationalExpenses(dateFrom, dateTo)
      .then((data) => {
        const list = Array.isArray(data) ? data : [];
        setItems(list);
        if (typeof updateCache === 'function') updateCache('operational_expenses', list);
      })
      .catch((e) => setError(e.message || 'Ошибка загрузки'))
      .finally(() => setLoading(false));
  }, [dateFrom, dateTo, refreshTrigger, updateCache]);

  useEffect(() => {
    // Обновляем дефолт в форме только если форма сейчас не редактирует конкретную запись.
    if (!formOpen && mode === 'create') {
      setForm((prev) => ({ ...prev, date: defaultDate }));
    }
  }, [defaultDate, formOpen, mode]);

  const openCreate = () => {
    setMode('create');
    setForm({
      id: null,
      date: defaultDate,
      amount: '',
      comment: '',
    });
    setFormOpen(true);
  };

  const openEdit = (item) => {
    setMode('edit');
    setForm({
      id: item.id,
      date: item.date,
      amount: item.amount != null ? String(item.amount) : '',
      comment: item.comment != null ? item.comment : '',
    });
    setFormOpen(true);
  };

  const closeForm = () => {
    if (saving) return;
    setMode('create');
    setForm({
      id: null,
      date: defaultDate,
      amount: '',
      comment: '',
    });
    setFormOpen(false);
  };

  const handleSubmit = async () => {
    setError('');
    const amount = Number(form.amount);
    if (!Number.isFinite(amount) || amount < 0) {
      setError('Сумма должна быть числом (>= 0)');
      return;
    }
    if (!form.date) {
      setError('Укажите дату');
      return;
    }

    const savedDate = form.date;
    const expectedAmount = Math.round(amount * 100) / 100;
    const shouldPollPnl =
      dateFrom &&
      dateTo &&
      savedDate >= dateFrom &&
      savedDate <= dateTo;

    setSaving(true);
    try {
      if (mode === 'create') {
        await api.createOperationalExpense({
          date: form.date,
          amount,
          comment: form.comment?.trim() ? form.comment : null,
        });
      } else {
        await api.updateOperationalExpense(form.id, {
          date: form.date,
          amount,
          comment: form.comment?.trim() ? form.comment : null,
        });
      }

      if (dateFrom && dateTo) {
        await api.triggerSyncRecalculate(dateFrom, dateTo);
        if (shouldPollPnl) {
          await waitForPnlOperationExpense(savedDate, expectedAmount);
        }
      }

      setFormOpen(false);
      if (typeof onRefresh === 'function') onRefresh();
    } catch (e) {
      setError(e.message || 'Ошибка сохранения')
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
      <div className="seb-toolbar">
        <h3>Операционные расходы</h3>
        <button className="btn-primary" onClick={openCreate} disabled={saving}>
          Внести
        </button>
      </div>

      {formOpen && (
        <div style={{ padding: 16, borderBottom: '0.5px solid var(--border-light)' }}>
          <div className="dashboard-card" style={{ padding: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <div style={{ fontWeight: 700 }}>{mode === 'create' ? 'Новая запись' : 'Редактирование'}</div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div>
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 6 }}>Дата</div>
                <input
                  type="date"
                  value={form.date}
                  onChange={(e) => setForm((prev) => ({ ...prev, date: e.target.value }))}
                  style={{
                    background: 'var(--bg-secondary)',
                    border: '0.5px solid var(--border-light)',
                    borderRadius: 'var(--radius-md)',
                    padding: '7px 10px',
                    fontSize: 12,
                    color: 'var(--text-primary)',
                    outline: 'none',
                    width: '100%',
                  }}
                />
              </div>
              <div>
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 6 }}>Сумма, ₽</div>
                <input
                  type="number"
                  step="0.01"
                  value={form.amount}
                  onChange={(e) => setForm((prev) => ({ ...prev, amount: e.target.value }))}
                  placeholder="0.00"
                  style={{
                    background: 'var(--bg-secondary)',
                    border: '0.5px solid var(--border-light)',
                    borderRadius: 'var(--radius-md)',
                    padding: '7px 10px',
                    fontSize: 12,
                    color: 'var(--text-primary)',
                    outline: 'none',
                    width: '100%',
                  }}
                />
              </div>
            </div>

            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 6 }}>Комментарий</div>
              <textarea
                value={form.comment}
                onChange={(e) => setForm((prev) => ({ ...prev, comment: e.target.value }))}
                rows={3}
                placeholder="Например: оплата за отгрузку/фулфилмент..."
                style={{
                  background: 'var(--bg-secondary)',
                  border: '0.5px solid var(--border-light)',
                  borderRadius: 'var(--radius-md)',
                  padding: '7px 10px',
                  fontSize: 12,
                  color: 'var(--text-primary)',
                  outline: 'none',
                  width: '100%',
                  resize: 'vertical',
                }}
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 14 }}>
              <button className="btn-primary" onClick={handleSubmit} disabled={saving}>
                {saving ? '...' : 'Сохранить'}
              </button>
              <button className="btn-outline" onClick={closeForm} disabled={saving}>
                Отмена
              </button>
            </div>
          </div>
        </div>
      )}

      <table className="seb-table">
        <thead>
          <tr>
            <th>Дата</th>
            <th style={{ textAlign: 'right' }}>Сумма, ₽</th>
            <th>Комментарий</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr>
              <td colSpan={4} style={{ textAlign: 'center', padding: 16 }}>
                Нет данных
              </td>
            </tr>
          ) : (
            items.map((it) => (
              <tr key={it.id}>
                <td>{formatDate(it.date)}</td>
                <td style={{ textAlign: 'right', color: 'var(--red)', fontWeight: 600 }}>{formatNum(it.amount)}</td>
                <td style={{ maxWidth: 420, whiteSpace: 'normal' }}>{it.comment || '—'}</td>
                <td style={{ textAlign: 'right' }}>
                  <button
                    className="btn-outline"
                    style={{ padding: '6px 10px', fontSize: 12 }}
                    onClick={() => openEdit(it)}
                    disabled={saving}
                    title="Редактировать"
                  >
                    ✏️
                  </button>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

