import { useEffect, useMemo, useState } from 'react';
import * as api from '../api';
import DataTable from '../components/DataTable';

function statusBadge(status) {
  const s = String(status || '');
  const map = {
    new: { bg: 'rgba(59,130,246,0.10)', color: '#1d4ed8', label: 'Новая' },
    in_progress: { bg: 'rgba(124,58,237,0.10)', color: '#6d28d9', label: 'В работе' },
    completed: { bg: 'rgba(16,172,132,0.12)', color: '#0f766e', label: 'Готово' },
    cancelled: { bg: 'rgba(239,68,68,0.10)', color: '#b91c1c', label: 'Отменено' },
    draft: { bg: 'rgba(59,130,246,0.10)', color: '#1d4ed8', label: 'Черновик' },
    running: { bg: 'rgba(124,58,237,0.10)', color: '#6d28d9', label: 'Идёт' },
    finished: { bg: 'rgba(16,172,132,0.12)', color: '#0f766e', label: 'Готово' },
  };
  const v = map[s] || { bg: 'rgba(0,0,0,0.06)', color: 'var(--text-secondary)', label: s || '—' };
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '4px 10px',
        borderRadius: 999,
        background: v.bg,
        color: v.color,
        border: '1px solid rgba(0,0,0,0.06)',
        fontSize: 12,
        fontWeight: 700,
        whiteSpace: 'nowrap',
      }}
    >
      {v.label}
    </span>
  );
}

function TabButton({ active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="btn"
      style={{
        padding: '8px 12px',
        borderRadius: 10,
        border: active ? '1px solid rgba(124,58,237,0.45)' : '1px solid rgba(0,0,0,0.12)',
        background: active ? 'rgba(124,58,237,0.08)' : 'transparent',
        color: active ? '#6d28d9' : 'var(--text-secondary)',
        fontWeight: 700,
        fontSize: 13,
      }}
    >
      {children}
    </button>
  );
}

function TasksTab() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');

  const reload = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.getAiTasks();
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch (e) {
      setError(e?.message || 'Ошибка загрузки');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
  }, []);

  const sorted = useMemo(() => {
    const list = Array.isArray(items) ? items.slice() : [];
    list.sort((a, b) => String(b?.created_at || '').localeCompare(String(a?.created_at || '')));
    return list;
  }, [items]);

  const setStatus = async (taskId, status) => {
    setBusyId(taskId);
    try {
      await api.updateAiTaskStatus(taskId, status);
      await reload();
    } catch (e) {
      setError(e?.message || 'Ошибка');
    } finally {
      setBusyId('');
    }
  };

  return (
    <DataTable title="Задачи" tag="ИИ модуль" actions={
      <button type="button" className="btn btn-outline-secondary btn-sm" onClick={reload} disabled={loading}>
        Обновить
      </button>
    }>
      {loading ? (
        <div style={{ padding: 12, color: 'var(--text-tertiary)' }}>Загрузка…</div>
      ) : error ? (
        <div className="alert alert-danger" style={{ margin: 12 }}>{error}</div>
      ) : sorted.length === 0 ? (
        <div style={{ padding: 12, color: 'var(--text-tertiary)' }}>Пока нет задач</div>
      ) : (
        <div className="table-wrapper" style={{ marginTop: 0 }}>
          <table className="custom-table">
            <thead>
              <tr>
                <th>Артикул</th>
                <th>Тип</th>
                <th>Заголовок</th>
                <th>Статус</th>
                <th style={{ width: 260 }}>Действия</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((t) => (
                <tr key={t.id}>
                  <td>{t.nm_id ?? '—'}</td>
                  <td style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>{t.task_type || '—'}</td>
                  <td>
                    <div style={{ fontWeight: 700 }}>{t.title}</div>
                    {t.reason && <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{t.reason}</div>}
                  </td>
                  <td>{statusBadge(t.status)}</td>
                  <td>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      {t.status === 'new' && (
                        <button type="button" className="btn btn-sm btn-outline-primary" disabled={busyId === t.id} onClick={() => setStatus(t.id, 'in_progress')}>
                          В работу
                        </button>
                      )}
                      {(t.status === 'new' || t.status === 'in_progress') && (
                        <button type="button" className="btn btn-sm btn-success" disabled={busyId === t.id} onClick={() => setStatus(t.id, 'completed')}>
                          Готово
                        </button>
                      )}
                      {(t.status === 'new' || t.status === 'in_progress') && (
                        <button type="button" className="btn btn-sm btn-outline-danger" disabled={busyId === t.id} onClick={() => setStatus(t.id, 'cancelled')}>
                          Отменить
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </DataTable>
  );
}

function HypothesesTab() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');
  const [resultSummary, setResultSummary] = useState({});

  const reload = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.getAiHypotheses();
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch (e) {
      setError(e?.message || 'Ошибка загрузки');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
  }, []);

  const sorted = useMemo(() => {
    const list = Array.isArray(items) ? items.slice() : [];
    list.sort((a, b) => String(b?.created_at || '').localeCompare(String(a?.created_at || '')));
    return list;
  }, [items]);

  const start = async (id) => {
    setBusyId(id);
    try {
      await api.startAiHypothesis(id);
      await reload();
    } catch (e) {
      setError(e?.message || 'Ошибка');
    } finally {
      setBusyId('');
    }
  };

  const finish = async (id) => {
    setBusyId(id);
    try {
      await api.finishAiHypothesis(id, resultSummary[id] || null);
      await reload();
    } catch (e) {
      setError(e?.message || 'Ошибка');
    } finally {
      setBusyId('');
    }
  };

  return (
    <DataTable title="Гипотезы" tag="ИИ модуль" actions={
      <button type="button" className="btn btn-outline-secondary btn-sm" onClick={reload} disabled={loading}>
        Обновить
      </button>
    }>
      {loading ? (
        <div style={{ padding: 12, color: 'var(--text-tertiary)' }}>Загрузка…</div>
      ) : error ? (
        <div className="alert alert-danger" style={{ margin: 12 }}>{error}</div>
      ) : sorted.length === 0 ? (
        <div style={{ padding: 12, color: 'var(--text-tertiary)' }}>Пока нет гипотез</div>
      ) : (
        <div className="table-wrapper" style={{ marginTop: 0 }}>
          <table className="custom-table">
            <thead>
              <tr>
                <th>Артикул</th>
                <th>Тип</th>
                <th>Гипотеза</th>
                <th>Статус</th>
                <th style={{ width: 360 }}>Действия</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((h) => (
                <tr key={h.id}>
                  <td>{h.nm_id ?? '—'}</td>
                  <td style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>{h.hypothesis_type || '—'}</td>
                  <td>
                    <div style={{ fontWeight: 700 }}>{h.title}</div>
                    {h.trigger_reason && <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{h.trigger_reason}</div>}
                  </td>
                  <td>{statusBadge(h.status)}</td>
                  <td>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                      {h.status === 'draft' && (
                        <button type="button" className="btn btn-sm btn-primary" disabled={busyId === h.id} onClick={() => start(h.id)}>
                          Запустить
                        </button>
                      )}
                      {h.status === 'running' && (
                        <>
                          <input
                            className="form-control form-control-sm"
                            style={{ width: 220 }}
                            value={resultSummary[h.id] || ''}
                            placeholder="Итог (коротко)"
                            onChange={(e) => setResultSummary((m) => ({ ...m, [h.id]: e.target.value }))}
                          />
                          <button type="button" className="btn btn-sm btn-success" disabled={busyId === h.id} onClick={() => finish(h.id)}>
                            Завершить
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </DataTable>
  );
}

export default function AiModule() {
  const [tab, setTab] = useState('tasks');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <TabButton active={tab === 'tasks'} onClick={() => setTab('tasks')}>Задачи</TabButton>
        <TabButton active={tab === 'hypotheses'} onClick={() => setTab('hypotheses')}>Гипотезы</TabButton>
      </div>
      {tab === 'tasks' ? <TasksTab /> : <HypothesesTab />}
    </div>
  );
}

