import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import * as api from '../api';
import DataTable from '../components/DataTable';

const LS_SELECTED_NM_ID = 'ai_module_selected_nm_id';
const LS_HIDE_COMPARISON_CALLOUT = 'ai_module_hide_comparison_callout';

function lsGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function lsSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}

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

function ModalShell({ open, title, onClose, children, footer }) {
  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(2,6,23,0.55)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 16,
      }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose?.();
      }}
    >
      <div
        style={{
          width: 'min(860px, 100%)',
          background: '#fff',
          borderRadius: 12,
          border: '1px solid rgba(2,6,23,0.08)',
          boxShadow: '0 20px 60px rgba(2,6,23,0.25)',
          overflow: 'hidden',
        }}
      >
        <div style={{ padding: 14, borderBottom: '1px solid rgba(2,6,23,0.08)', display: 'flex', gap: 10, alignItems: 'center' }}>
          <div style={{ fontWeight: 900 }}>{title}</div>
          <button type="button" className="btn btn-sm btn-outline-secondary" onClick={onClose} style={{ marginLeft: 'auto' }}>
            Закрыть
          </button>
        </div>
        <div style={{ padding: 14 }}>
          {children}
        </div>
        {footer && (
          <div style={{ padding: 14, borderTop: '1px solid rgba(2,6,23,0.08)', display: 'flex', gap: 10, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

function ProductPickerModal({ open, onClose, onSelectNmId }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [selected, setSelected] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.getArticles();
      setItems(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e?.message || 'Не удалось загрузить товары');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setSelected(null);
    setQ('');
    load();
  }, [open, load]);

  const filtered = useMemo(() => {
    const query = (q || '').trim().toLowerCase();
    const list = Array.isArray(items) ? items : [];
    if (!query) return list.slice(0, 200);
    return list
      .filter((x) => {
        const nm = String(x?.nm_id ?? '').toLowerCase();
        const name = String(x?.name ?? '').toLowerCase();
        const vendor = String(x?.vendor_code ?? '').toLowerCase();
        return nm.includes(query) || name.includes(query) || vendor.includes(query);
      })
      .slice(0, 200);
  }, [items, q]);

  return (
    <ModalShell
      open={open}
      title="Выбор товара"
      onClose={onClose}
      footer={(
        <>
          <button type="button" className="btn btn-outline-secondary" onClick={onClose}>Отмена</button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!selected}
            onClick={() => {
              if (!selected) return;
              onSelectNmId?.(Number(selected));
              onClose?.();
            }}
          >
            ОК / Выбрать
          </button>
        </>
      )}
    >
      {error && <div className="alert alert-danger" style={{ marginTop: 0 }}>{error}</div>}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        <input
          className="form-control"
          value={q}
          placeholder="Поиск по артикулу или названию"
          onChange={(e) => setQ(e.target.value)}
          style={{ flex: '1 1 320px' }}
        />
        <button type="button" className="btn btn-outline-secondary" onClick={load} disabled={loading}>
          {loading ? 'Загрузка…' : 'Обновить'}
        </button>
      </div>

      {loading ? (
        <div style={{ color: 'var(--text-tertiary)' }}>Загрузка…</div>
      ) : filtered.length === 0 ? (
        <div style={{ color: 'var(--text-tertiary)' }}>Товары не найдены</div>
      ) : (
        <div className="table-wrapper" style={{ marginTop: 0 }}>
          <table className="custom-table">
            <thead>
              <tr>
                <th />
                <th>Артикул</th>
                <th>Название</th>
                <th style={{ width: 220 }}>Код</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((x) => {
                const nm = Number(x?.nm_id);
                const isSel = selected === nm;
                return (
                  <tr
                    key={String(x?.nm_id)}
                    onClick={() => setSelected(nm)}
                    style={{ cursor: 'pointer', background: isSel ? 'rgba(124,58,237,0.06)' : undefined }}
                  >
                    <td style={{ width: 1 }}>
                      <input type="radio" checked={isSel} onChange={() => setSelected(nm)} />
                    </td>
                    <td style={{ fontWeight: 800 }}>{x?.nm_id ?? '—'}</td>
                    <td style={{ color: 'var(--text-secondary)' }}>{x?.name || '—'}</td>
                    <td style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>{x?.vendor_code || '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </ModalShell>
  );
}

function WbAccessModal({ open, onClose, onGranted }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [remoteOpen, setRemoteOpen] = useState(false);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [remoteIframeNonce, setRemoteIframeNonce] = useState(0);

  useEffect(() => {
    if (!open) return;
    setError('');
    setSaving(false);
    setFile(null);
    setUploading(false);
    setRemoteOpen(false);
    setRemoteBusy(false);
    setRemoteIframeNonce(0);
  }, [open]);

  const startRemote = async () => {
    setRemoteBusy(true);
    setError('');
    try {
      await api.startAiWbRemoteAuth();
      setRemoteOpen(true);
      // Force iframe/noVNC client reconnect to show fresh remote session.
      setRemoteIframeNonce((x) => x + 1);
    } catch (e) {
      setError(e?.message || 'Не удалось открыть окно авторизации');
    } finally {
      setRemoteBusy(false);
    }
  };

  const finishRemote = async () => {
    setRemoteBusy(true);
    setError('');
    try {
      await api.saveAiWbRemoteAuth();
      onGranted?.();
      onClose?.();
    } catch (e) {
      setError(e?.message || 'Не удалось сохранить доступ');
    } finally {
      setRemoteBusy(false);
    }
  };

  const upload = async () => {
    if (!file) return;
    setUploading(true);
    setError('');
    try {
      await api.uploadAiWbAccessFile(file);
      onGranted?.();
      onClose?.();
    } catch (e) {
      setError(e?.message || 'Не удалось загрузить файл доступа');
    } finally {
      setUploading(false);
    }
  };

  const showUpload = String(error || '').toLowerCase().includes('no display') || String(error || '').toLowerCase().includes('storage_state');

  return (
    <ModalShell
      open={open}
      title="Выдать доступ к кабинету WB"
      onClose={onClose}
      footer={(
        <>
          <button type="button" className="btn btn-outline-secondary" onClick={onClose} disabled={saving}>Отмена</button>
          <button type="button" className="btn btn-outline-primary" onClick={startRemote} disabled={saving || uploading || remoteBusy}>
            {remoteBusy ? 'Открываю…' : 'Открыть окно'}
          </button>
          <button type="button" className="btn btn-primary" onClick={finishRemote} disabled={!remoteOpen || saving || uploading || remoteBusy}>
            {remoteBusy ? 'Сохраняю…' : 'Я вошёл'}
          </button>
        </>
      )}
    >
      <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 12 }}>
        Нажмите “Открыть окно” — откроется встроенное окно кабинета WB. Введите логин/пароль (и код, если попросит WB).
        После успешного входа нажмите “Я вошёл”, чтобы сохранить доступ.
      </div>
      {error && <div className="alert alert-danger" style={{ marginTop: 0 }}>{error}</div>}

      {remoteOpen && (
        <div style={{ border: '1px solid rgba(2,6,23,0.10)', borderRadius: 12, overflow: 'hidden', height: 520 }}>
          <iframe
            title="WB remote login"
            key={`wb-remote-${remoteIframeNonce}`}
            src="/wb-auth/vnc.html?autoconnect=1&resize=scale"
            style={{ width: '100%', height: '100%', border: 0 }}
          />
        </div>
      )}

      {showUpload && (
        <div style={{ marginTop: 10, display: 'grid', gap: 10 }}>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            В локальном Docker окно браузера открыть нельзя. Загрузите “файл доступа” (JSON), который создаётся после входа в кабинет WB.
          </div>
          <input
            type="file"
            accept=".json,application/json"
            className="form-control"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
          <div>
            <button type="button" className="btn btn-outline-primary" disabled={!file || uploading || saving} onClick={upload}>
              {uploading ? 'Загружаю…' : 'Загрузить файл доступа'}
            </button>
          </div>
        </div>
      )}
    </ModalShell>
  );
}

function SelectedProductCard({ nmId, onChange }) {
  return (
    <div
      style={{
        border: '1px solid rgba(2,6,23,0.08)',
        borderRadius: 12,
        background: '#fff',
        padding: 12,
        display: 'flex',
        gap: 12,
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>Сейчас вы работаете с карточкой:</div>
        <div style={{ fontWeight: 900, fontSize: 16 }}>Артикул {nmId}</div>
      </div>
      <button type="button" className="btn btn-outline-secondary btn-sm" onClick={onChange}>
        Сменить товар
      </button>
    </div>
  );
}

function EmptyState({ onPick }) {
  return (
    <div
      style={{
        border: '1px solid rgba(2,6,23,0.08)',
        borderRadius: 12,
        background: '#fff',
        padding: 18,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        alignItems: 'flex-start',
      }}
    >
      <div style={{ fontWeight: 900, fontSize: 18 }}>AI-модуль развития карточек</div>
      <div style={{ color: 'var(--text-secondary)', maxWidth: 760 }}>
        <div>Выберите товар, с которым хотите работать.</div>
        <div>Система будет анализировать карточку, сравнивать её с конкурентами и создавать задачи и гипотезы для роста продаж.</div>
      </div>
      <button type="button" className="btn btn-primary" onClick={onPick}>
        Выбрать товар
      </button>
    </div>
  );
}

function ComparisonCallout({ visible, onConfirmCreated, onLater, onCreateTechnicalTask, busy, errorText }) {
  if (!visible) return null;
  return (
    <div
      style={{
        border: '1px solid rgba(124,58,237,0.22)',
        borderRadius: 12,
        background: 'rgba(124,58,237,0.06)',
        padding: 14,
      }}
    >
      <div style={{ fontWeight: 900, marginBottom: 6 }}>Чтобы начать анализ, создайте сравнение с конкурентами</div>
      <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 10, maxWidth: 880 }}>
        Для работы AI-модуля нужно сравнить вашу карточку с четырьмя конкурентами.
        Откройте сравнение карточек в кабинете WB, добавьте ваш товар и 4 товара конкурентов, затем нажмите “Готово”.
      </div>
      {errorText && (
        <div className="alert alert-danger" style={{ margin: '0 0 10px 0' }}>
          {errorText}
        </div>
      )}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <button type="button" className="btn btn-primary btn-sm" onClick={onConfirmCreated} disabled={busy}>
          {busy ? 'Проверяю…' : 'Я создал сравнение'}
        </button>
        <button type="button" className="btn btn-outline-secondary btn-sm" onClick={onLater} disabled={busy}>
          Позже
        </button>
        <button type="button" className="btn btn-warning btn-sm" onClick={onCreateTechnicalTask} disabled={busy}>
          Запросить обновление отчёта (требует подтверждения)
        </button>
      </div>
    </div>
  );
}

function TasksTab({ selectedNmId, onGrantWbAccess }) {
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

  const visibleItems = useMemo(() => {
    const list = Array.isArray(items) ? items : [];
    const sel = selectedNmId == null ? null : Number(selectedNmId);
    return list.filter((t) => {
      const nm = t?.nm_id == null ? null : Number(t.nm_id);
      if (sel == null) return nm == null; // until product selected show only global tasks
      return nm == null || nm === sel;
    });
  }, [items, selectedNmId]);

  const sorted = useMemo(() => {
    const list = Array.isArray(visibleItems) ? visibleItems.slice() : [];
    list.sort((a, b) => String(b?.created_at || '').localeCompare(String(a?.created_at || '')));
    return list;
  }, [visibleItems]);

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

  const execute = async (taskId) => {
    setBusyId(taskId);
    setError('');
    try {
      await api.executeAiTask(taskId);
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
                <th>Задача</th>
                <th>Статус</th>
                <th style={{ width: 260 }}>Действия</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((t) => (
                <tr key={t.id}>
                  <td>
                    <div style={{ fontWeight: 700 }}>{t.title}</div>
                    {t.description && <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{t.description}</div>}
                  </td>
                  <td>{statusBadge(t.status)}</td>
                  <td>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      {t.task_type === 'wb_access_grant' && t.status !== 'completed' && (
                        <button type="button" className="btn btn-sm btn-primary" disabled={busyId === t.id} onClick={() => onGrantWbAccess?.()}>
                          Выдать доступ
                        </button>
                      )}
                      {t.task_type === 'competitor_report_refresh' && t.status === 'new' && (
                        <button type="button" className="btn btn-sm btn-warning" disabled={busyId === t.id} onClick={() => execute(t.id)}>
                          Подтвердить и обновить
                        </button>
                      )}
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

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function HypothesesTab({ selectedNmId }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');
  const [resultSummary, setResultSummary] = useState({});
  const [logOpenId, setLogOpenId] = useState(null);
  const [logItems, setLogItems] = useState({});
  const [logLoadingId, setLogLoadingId] = useState(null);
  const [logError, setLogError] = useState('');
  const [logForm, setLogForm] = useState(() => ({ day: todayIso(), happened: '', changed: '', unchanged: '' }));

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

  const visibleItems = useMemo(() => {
    const list = Array.isArray(items) ? items : [];
    const sel = selectedNmId == null ? null : Number(selectedNmId);
    if (sel == null) return [];
    return list.filter((h) => Number(h?.nm_id) === sel);
  }, [items, selectedNmId]);

  const sorted = useMemo(() => {
    const list = Array.isArray(visibleItems) ? visibleItems.slice() : [];
    list.sort((a, b) => String(b?.created_at || '').localeCompare(String(a?.created_at || '')));
    return list;
  }, [visibleItems]);

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
      if (logOpenId === id) {
        await fetchDailyLog(id);
      }
    } catch (e) {
      setError(e?.message || 'Ошибка');
    } finally {
      setBusyId('');
    }
  };

  const fetchDailyLog = async (hypothesisId) => {
    setLogLoadingId(hypothesisId);
    setLogError('');
    try {
      const data = await api.getAiHypothesisDailyLog(hypothesisId);
      const rows = Array.isArray(data?.items) ? data.items : [];
      setLogItems((m) => ({ ...m, [hypothesisId]: rows }));
    } catch (e) {
      setLogError(e?.message || 'Ошибка загрузки дневного лога');
      setLogItems((m) => ({ ...m, [hypothesisId]: [] }));
    } finally {
      setLogLoadingId(null);
    }
  };

  const toggleDailyLog = async (h) => {
    const id = h.id;
    if (logOpenId === id) {
      setLogOpenId(null);
      setLogError('');
      return;
    }
    setLogOpenId(id);
    setLogError('');
    setLogForm({ day: todayIso(), happened: '', changed: '', unchanged: '' });
    await fetchDailyLog(id);
  };

  const saveDailyLog = async (hypothesisId) => {
    setBusyId(hypothesisId);
    setLogError('');
    try {
      await api.upsertAiHypothesisDailyLog(hypothesisId, logForm);
      await fetchDailyLog(hypothesisId);
      setLogForm((f) => ({ ...f, happened: '', changed: '', unchanged: '' }));
    } catch (e) {
      setLogError(e?.message || 'Не удалось сохранить запись');
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
                <th>Гипотеза</th>
                <th>Статус</th>
                <th style={{ width: 360 }}>Действия</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((h) => (
                <Fragment key={h.id}>
                  <tr>
                    <td>
                      <div style={{ fontWeight: 700 }}>{h.title}</div>
                      {h.trigger_reason && <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{h.trigger_reason}</div>}
                    </td>
                    <td>{statusBadge(h.status)}</td>
                    <td>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                        <button
                          type="button"
                          className="btn btn-sm btn-outline-secondary"
                          disabled={logLoadingId === h.id}
                          onClick={() => toggleDailyLog(h)}
                        >
                          {logOpenId === h.id ? 'Скрыть дневной лог' : 'Дневной лог'}
                        </button>
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
                  {logOpenId === h.id && (
                    <tr>
                      <td colSpan={3} style={{ background: 'rgba(124,58,237,0.04)', verticalAlign: 'top' }}>
                        {logError && <div className="alert alert-danger" style={{ marginBottom: 8 }}>{logError}</div>}
                        {logLoadingId === h.id ? (
                          <div style={{ color: 'var(--text-tertiary)', padding: 8 }}>Загрузка дневного лога…</div>
                        ) : (
                          <>
                            <div style={{ fontWeight: 800, marginBottom: 8 }}>Записи по дням</div>
                            {((logItems[h.id] || []).length === 0) ? (
                              <div style={{ color: 'var(--text-tertiary)', fontSize: 13, marginBottom: 10 }}>
                                {h.status === 'draft'
                                  ? 'После запуска гипотезы здесь можно вести дневные заметки.'
                                  : 'Пока нет записей.'}
                              </div>
                            ) : (
                              <div className="table-wrapper" style={{ marginBottom: 12 }}>
                                <table className="custom-table">
                                  <thead>
                                    <tr>
                                      <th>День</th>
                                      <th>Произошло</th>
                                      <th>Изменили</th>
                                      <th>Не меняли</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {(logItems[h.id] || []).map((row) => (
                                      <tr key={row.day}>
                                        <td style={{ whiteSpace: 'nowrap' }}>{row.day ?? '—'}</td>
                                        <td style={{ fontSize: 13 }}>{row.happened || '—'}</td>
                                        <td style={{ fontSize: 13 }}>{row.changed || '—'}</td>
                                        <td style={{ fontSize: 13 }}>{row.unchanged || '—'}</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            )}
                            {h.status === 'running' && (
                              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 720 }}>
                                <div style={{ fontWeight: 700, fontSize: 13 }}>Добавить / обновить день</div>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
                                  <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                                    Дата
                                    <input
                                      type="date"
                                      className="form-control form-control-sm"
                                      style={{ width: 160, marginLeft: 6 }}
                                      value={logForm.day}
                                      onChange={(e) => setLogForm((f) => ({ ...f, day: e.target.value }))}
                                    />
                                  </label>
                                </div>
                                <textarea
                                  className="form-control form-control-sm"
                                  rows={2}
                                  placeholder="Что произошло за день"
                                  value={logForm.happened}
                                  onChange={(e) => setLogForm((f) => ({ ...f, happened: e.target.value }))}
                                />
                                <textarea
                                  className="form-control form-control-sm"
                                  rows={2}
                                  placeholder="Что изменили"
                                  value={logForm.changed}
                                  onChange={(e) => setLogForm((f) => ({ ...f, changed: e.target.value }))}
                                />
                                <textarea
                                  className="form-control form-control-sm"
                                  rows={2}
                                  placeholder="Что сознательно не трогали"
                                  value={logForm.unchanged}
                                  onChange={(e) => setLogForm((f) => ({ ...f, unchanged: e.target.value }))}
                                />
                                <div>
                                  <button
                                    type="button"
                                    className="btn btn-primary btn-sm"
                                    disabled={busyId === h.id || !logForm.day}
                                    onClick={() => saveDailyLog(h.id)}
                                  >
                                    Сохранить день
                                  </button>
                                </div>
                              </div>
                            )}
                          </>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
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
  const [selectedNmId, setSelectedNmId] = useState(() => {
    const v = (lsGet(LS_SELECTED_NM_ID) || '').trim();
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : null;
  });
  const [pickerOpen, setPickerOpen] = useState(false);
  const [wbModalOpen, setWbModalOpen] = useState(false);

  const [credsStatus, setCredsStatus] = useState(null);
  const [reportStatus, setReportStatus] = useState(null);
  const [comparisonBusy, setComparisonBusy] = useState(false);
  const [comparisonError, setComparisonError] = useState('');

  const loadReport = useCallback(async () => {
    setComparisonError('');
    try {
      const st = await api.getAiCompetitorReportStatus('week');
      setReportStatus(st);
    } catch (e) {
      setComparisonError(e?.message || 'Ошибка загрузки статуса');
    }
  }, []);

  const loadCreds = useCallback(async () => {
    try {
      const st = await api.getAiWbCredentialsStatus();
      setCredsStatus(st);
    } catch {
      // ignore; screen still works
    }
  }, []);

  useEffect(() => {
    loadReport();
    loadCreds();
  }, [loadReport, loadCreds]);

  const calloutHidden = useMemo(() => (lsGet(LS_HIDE_COMPARISON_CALLOUT) || '') === '1', []);
  const showComparisonCallout = useMemo(() => {
    if (!selectedNmId) return false;
    if (calloutHidden) return false;
    const st = (reportStatus?.status || '').toLowerCase();
    return st === 'missing' || st === 'stale';
  }, [selectedNmId, reportStatus, calloutHidden]);

  const onConfirmCreated = async () => {
    setComparisonBusy(true);
    setComparisonError('');
    try {
      const st = await api.getAiCompetitorReportStatus('week');
      setReportStatus(st);
      const statusTxt = (st?.status || '').toLowerCase();
      if (statusTxt === 'missing') {
        setComparisonError('Отчёт пока не найден. Проверьте, что вы добавили ваш товар и 4 конкурента в сравнение, затем попробуйте ещё раз.');
      }
    } catch (e) {
      setComparisonError(e?.message || 'Не удалось проверить отчёт');
    } finally {
      setComparisonBusy(false);
    }
  };

  const onLater = () => {
    lsSet(LS_HIDE_COMPARISON_CALLOUT, '1');
    setComparisonError('');
    // force rerender for memoized flag
    setReportStatus((x) => ({ ...(x || {}) }));
  };

  const onCreateTechnicalTask = async () => {
    setComparisonBusy(true);
    setComparisonError('');
    try {
      await api.requestAiCompetitorReportRefresh('week');
      await loadReport();
      setTab('tasks');
    } catch (e) {
      setComparisonError(e?.message || 'Не удалось создать задачу');
    } finally {
      setComparisonBusy(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {!selectedNmId ? (
        <EmptyState onPick={() => setPickerOpen(true)} />
      ) : (
        <>
          <SelectedProductCard nmId={selectedNmId} onChange={() => setPickerOpen(true)} />

          {(credsStatus?.status || '').toLowerCase() === 'missing' && (
            <div
              style={{
                border: '1px solid rgba(2,6,23,0.08)',
                borderRadius: 12,
                background: '#fff',
                padding: 14,
                display: 'flex',
                gap: 12,
                alignItems: 'center',
                justifyContent: 'space-between',
                flexWrap: 'wrap',
              }}
            >
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <div style={{ fontWeight: 900 }}>Нужно дать доступ к кабинету WB</div>
                <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
                  Это требуется, чтобы система могла получать отчёт сравнения с конкурентами.
                </div>
              </div>
              <button type="button" className="btn btn-primary btn-sm" onClick={() => setWbModalOpen(true)}>
                Выдать доступ
              </button>
            </div>
          )}

          <ComparisonCallout
            visible={showComparisonCallout}
            onConfirmCreated={onConfirmCreated}
            onLater={onLater}
            onCreateTechnicalTask={onCreateTechnicalTask}
            busy={comparisonBusy}
            errorText={comparisonError}
          />
        </>
      )}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <TabButton active={tab === 'tasks'} onClick={() => setTab('tasks')}>Задачи</TabButton>
        <TabButton active={tab === 'hypotheses'} onClick={() => setTab('hypotheses')}>Гипотезы</TabButton>
      </div>
      {tab === 'tasks'
        ? <TasksTab selectedNmId={selectedNmId} onGrantWbAccess={() => setWbModalOpen(true)} />
        : <HypothesesTab selectedNmId={selectedNmId} />}

      <ProductPickerModal
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onSelectNmId={(nm) => {
          setSelectedNmId(nm);
          lsSet(LS_SELECTED_NM_ID, String(nm));
          lsSet(LS_HIDE_COMPARISON_CALLOUT, '');
          setComparisonError('');
        }}
      />
      <WbAccessModal
        open={wbModalOpen}
        onClose={() => setWbModalOpen(false)}
        onGranted={() => {
          loadCreds();
        }}
      />
    </div>
  );
}

