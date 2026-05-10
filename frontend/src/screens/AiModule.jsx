import { useCallback, useEffect, useMemo, useState } from 'react';
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
  const [period, setPeriod] = useState('week');
  const [reportStatus, setReportStatus] = useState(null);
  const [reportDetail, setReportDetail] = useState(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [credsStatus, setCredsStatus] = useState(null);
  const [reportError, setReportError] = useState('');
  const [credsForm, setCredsForm] = useState({ wb_login: '', wb_password: '' });
  const [savingCreds, setSavingCreds] = useState(false);
  const [requesting, setRequesting] = useState(false);

  const loadReport = useCallback(async () => {
    setReportError('');
    try {
      const st = await api.getAiCompetitorReportStatus(period);
      setReportStatus(st);
      // If report changed, reset detail cache
      const rid = String(st?.report_id || '');
      const currentRid = String(reportDetail?.report?.id || '');
      if (rid && currentRid && rid !== currentRid) {
        setReportDetail(null);
        setDetailError('');
        setDetailOpen(false);
      }
    } catch (e) {
      setReportError(e?.message || 'Ошибка загрузки статуса отчёта');
    }
  }, [period, reportDetail]);

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

  const saveCreds = async () => {
    setSavingCreds(true);
    setReportError('');
    try {
      const st = await api.upsertAiWbCredentials(credsForm);
      setCredsStatus(st);
      setCredsForm({ wb_login: '', wb_password: '' });
    } catch (e) {
      setReportError(e?.message || 'Ошибка сохранения учётки WB');
    } finally {
      setSavingCreds(false);
    }
  };

  const requestRefresh = async () => {
    setRequesting(true);
    setReportError('');
    try {
      await api.requestAiCompetitorReportRefresh(period);
    } catch (e) {
      setReportError(e?.message || 'Ошибка создания задачи');
    } finally {
      setRequesting(false);
    }
  };

  const loadDetail = async () => {
    const rid = String(reportStatus?.report_id || '').trim();
    if (!rid) return;
    setDetailLoading(true);
    setDetailError('');
    try {
      const data = await api.getAiCompetitorReportDetail(rid);
      setReportDetail(data);
      setDetailOpen(true);
    } catch (e) {
      setDetailError(e?.message || 'Ошибка загрузки отчёта');
      setDetailOpen(true);
    } finally {
      setDetailLoading(false);
    }
  };

  const metricsSorted = useMemo(() => {
    const list = Array.isArray(reportDetail?.metrics) ? reportDetail.metrics.slice() : [];
    list.sort((a, b) => {
      const an = Number(a?.nm_id || 0);
      const bn = Number(b?.nm_id || 0);
      if (an !== bn) return an - bn;
      return String(a?.metric_code || '').localeCompare(String(b?.metric_code || ''));
    });
    return list;
  }, [reportDetail]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <DataTable title="Отчёт конкурентов WB" tag="ИИ модуль">
        {reportError && <div className="alert alert-danger" style={{ margin: 12 }}>{reportError}</div>}
        <div style={{ padding: 12, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <div style={{ fontWeight: 700 }}>Период:</div>
            <select className="form-select form-select-sm" style={{ width: 160 }} value={period} onChange={(e) => setPeriod(e.target.value)}>
              <option value="week">Неделя</option>
              <option value="month">Месяц</option>
              <option value="quarter">Квартал</option>
            </select>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <div style={{ fontWeight: 700 }}>Статус:</div>
            <span style={{ color: 'var(--text-tertiary)' }}>{reportStatus?.status || '—'}</span>
            {reportStatus?.valid_until && (
              <span style={{ color: 'var(--text-tertiary)' }}>до {String(reportStatus.valid_until)}</span>
            )}
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={loadReport}>
              Обновить статус
            </button>
            <button
              type="button"
              className="btn btn-outline-primary btn-sm"
              onClick={loadDetail}
              disabled={!reportStatus?.report_id || detailLoading}
              title={!reportStatus?.report_id ? 'Нет актуального report_id' : ''}
            >
              {detailLoading ? 'Загрузка…' : 'Показать метрики'}
            </button>
            <button type="button" className="btn btn-warning btn-sm" onClick={requestRefresh} disabled={requesting}>
              Создать задачу на обновление (подтверждение)
            </button>
          </div>
        </div>

        {detailOpen && (
          <div style={{ padding: 12, borderTop: '1px solid rgba(0,0,0,0.06)' }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <div style={{ fontWeight: 800 }}>Метрики отчёта</div>
              <div style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>
                report_id: {reportStatus?.report_id || '—'}
              </div>
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => setDetailOpen(false)}>
                  Скрыть
                </button>
              </div>
            </div>

            {detailError ? (
              <div className="alert alert-danger" style={{ marginTop: 10 }}>{detailError}</div>
            ) : !reportDetail ? (
              <div style={{ paddingTop: 10, color: 'var(--text-tertiary)' }}>Нет данных</div>
            ) : metricsSorted.length === 0 ? (
              <div style={{ paddingTop: 10, color: 'var(--text-tertiary)' }}>Метрики пустые</div>
            ) : (
              <div className="table-wrapper" style={{ marginTop: 10 }}>
                <table className="custom-table">
                  <thead>
                    <tr>
                      <th>Артикул</th>
                      <th>Метрика</th>
                      <th>Наша</th>
                      <th>Медиана конкурентов</th>
                      <th>Δ%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {metricsSorted.map((m) => {
                      const our = m?.our_value;
                      const med = m?.competitor_median_value;
                      let delta = null;
                      const ourN = Number(our);
                      const medN = Number(med);
                      if (Number.isFinite(ourN) && Number.isFinite(medN) && medN !== 0) {
                        delta = ((ourN - medN) / medN) * 100;
                      }
                      const deltaTxt = delta === null ? '—' : `${delta.toFixed(1)}%`;
                      const deltaColor = delta === null ? 'var(--text-tertiary)' : (delta >= 0 ? '#0f766e' : '#b91c1c');
                      return (
                        <tr key={m.id}>
                          <td>{m.nm_id ?? '—'}</td>
                          <td style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>{m.metric_code || '—'}</td>
                          <td>{our ?? '—'}{m.unit ? ` ${m.unit}` : ''}</td>
                          <td>{med ?? '—'}{m.unit ? ` ${m.unit}` : ''}</td>
                          <td style={{ color: deltaColor, fontWeight: 800 }}>{deltaTxt}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        <div style={{ padding: 12, borderTop: '1px solid rgba(0,0,0,0.06)' }}>
          <div style={{ fontWeight: 800, marginBottom: 6 }}>Учётка WB для Playwright</div>
          <div style={{ color: 'var(--text-tertiary)', fontSize: 12, marginBottom: 10 }}>
            Пароль хранится зашифрованно. Операция обновления отчёта может быть платной/лимитной — запуск только по подтверждению.
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <input
              className="form-control form-control-sm"
              style={{ width: 240 }}
              value={credsForm.wb_login}
              placeholder="WB логин"
              onChange={(e) => setCredsForm((m) => ({ ...m, wb_login: e.target.value }))}
            />
            <input
              className="form-control form-control-sm"
              style={{ width: 240 }}
              value={credsForm.wb_password}
              placeholder="WB пароль"
              type="password"
              onChange={(e) => setCredsForm((m) => ({ ...m, wb_password: e.target.value }))}
            />
            <button type="button" className="btn btn-primary btn-sm" onClick={saveCreds} disabled={savingCreds}>
              Сохранить
            </button>
            <span style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>
              status: {credsStatus?.status || 'missing'}
              {credsStatus?.last_error ? `; error: ${credsStatus.last_error}` : ''}
            </span>
          </div>
        </div>
      </DataTable>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <TabButton active={tab === 'tasks'} onClick={() => setTab('tasks')}>Задачи</TabButton>
        <TabButton active={tab === 'hypotheses'} onClick={() => setTab('hypotheses')}>Гипотезы</TabButton>
      </div>
      {tab === 'tasks' ? <TasksTab /> : <HypothesesTab />}
    </div>
  );
}

