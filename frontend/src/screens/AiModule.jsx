import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import * as api from '../api';
import DataTable from '../components/DataTable';

const LS_SELECTED_NM_ID = 'ai_module_selected_nm_id';
const LS_HIDE_COMPARISON_CALLOUT = 'ai_module_hide_comparison_callout';
const LS_ONBOARDING_CONFIRMED = 'ai_module_onboarding_confirmed_v1';

function lsGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function lsSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}

function softCardStyle() {
  return {
    border: '1px solid rgba(2,6,23,0.08)',
    borderRadius: 12,
    background: '#fff',
  };
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

function InfoRow({ label, children }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: 12, padding: '8px 0', borderBottom: '1px solid rgba(2,6,23,0.06)' }}>
      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', fontWeight: 800, letterSpacing: '0.02em', textTransform: 'uppercase' }}>
        {label}
      </div>
      <div style={{ color: 'var(--text-secondary)', fontSize: 13, whiteSpace: 'pre-wrap' }}>
        {children}
      </div>
    </div>
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

function FirstRunBanner({
  step,
  selectedNmId,
  needsWbAccess,
  onPickProduct,
  onConfirmProduct,
  onGrantAccess,
  busy,
  errorText,
}) {
  if (!step) return null;
  const step1 = step === 1;
  const step2 = step === 2;

  return (
    <div
      style={{
        ...softCardStyle(),
        borderColor: 'rgba(124,58,237,0.22)',
        background: 'rgba(124,58,237,0.06)',
        padding: 14,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 14,
        flexWrap: 'wrap',
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 260 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              padding: '3px 10px',
              borderRadius: 999,
              background: 'rgba(91,79,212,0.12)',
              color: '#4c42b8',
              border: '1px solid rgba(91,79,212,0.20)',
              fontSize: 12,
              fontWeight: 900,
              whiteSpace: 'nowrap',
            }}
          >
            Шаг {step}
          </span>
          <div style={{ fontWeight: 900 }}>
            {step1 ? 'Выберите товар' : 'Дайте доступ к кабинету WB'}
          </div>
        </div>
        <div style={{ color: 'var(--text-secondary)', fontSize: 13, maxWidth: 860 }}>
          {step1
            ? 'Выберите товар, с которым хотите работать, и нажмите OK.'
            : 'Нажмите “Выдать доступ”. После успешной авторизации плашка исчезнет.'}
        </div>
        {errorText && (
          <div className="alert alert-danger" style={{ margin: '6px 0 0 0' }}>
            {errorText}
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        {step1 && (
          <>
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={onPickProduct} disabled={busy}>
              {selectedNmId ? `Сменить (сейчас ${selectedNmId})` : 'Выбрать товар'}
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={onConfirmProduct}
              disabled={!selectedNmId || busy}
            >
              OK
            </button>
          </>
        )}
        {step2 && (
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={onGrantAccess}
            disabled={busy || !needsWbAccess}
            title={!needsWbAccess ? 'Доступ уже выдан' : undefined}
          >
            Выдать доступ
          </button>
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
  const [remoteSessionEnsured, setRemoteSessionEnsured] = useState(false);
  const [checkedOnce, setCheckedOnce] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError('');
    setSaving(false);
    setFile(null);
    setUploading(false);
    setRemoteOpen(false);
    setRemoteSessionEnsured(false);
    setCheckedOnce(false);
    setRemoteBusy(false);
    setRemoteIframeNonce(0);
  }, [open]);

  const ensureRemote = async () => {
    setRemoteBusy(true);
    setError('');
    try {
      const st = await api.getAiWbRemoteAuthStatus();
      if (st?.active) {
        setRemoteOpen(true);
        setRemoteIframeNonce((x) => x + 1);
        return;
      }
      await api.startAiWbRemoteAuth({ force: false });
      setRemoteOpen(true);
      setRemoteSessionEnsured(true);
      setRemoteIframeNonce((x) => x + 1);
    } catch (e) {
      // During rolling deploys the backend may not have /remote/status yet (404).
      // In that case, fall back to "start" without surfacing a scary error.
      const msg = String(e?.message || '');
      const looksLikeNotFound = msg.toLowerCase().includes('not found') || msg.includes('404');
      if (looksLikeNotFound) {
        try {
          await api.startAiWbRemoteAuth({ force: false });
          setRemoteOpen(true);
          setRemoteSessionEnsured(true);
          setRemoteIframeNonce((x) => x + 1);
          return;
        } catch (e2) {
          setError(e2?.message || 'Не удалось открыть окно авторизации');
          return;
        }
      }
      setError(msg || 'Не удалось открыть окно авторизации');
    } finally {
      setRemoteBusy(false);
      setCheckedOnce(true);
    }
  };

  const restartRemote = async () => {
    setRemoteBusy(true);
    setError('');
    try {
      await api.startAiWbRemoteAuth({ force: true });
      setRemoteOpen(true);
      setRemoteSessionEnsured(true);
      setRemoteIframeNonce((x) => x + 1);
    } catch (e) {
      setError(e?.message || 'Не удалось открыть окно авторизации');
    } finally {
      setRemoteBusy(false);
      setCheckedOnce(true);
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

  useEffect(() => {
    if (!open) return;
    if (showUpload) return;
    if (checkedOnce) return;
    ensureRemote();
  }, [open, showUpload, checkedOnce]);

  return (
    <ModalShell
      open={open}
      title="Выдать доступ к кабинету WB"
      onClose={onClose}
      footer={(
        <>
          <button type="button" className="btn btn-outline-secondary" onClick={onClose} disabled={saving}>Отмена</button>
          <button type="button" className="btn btn-outline-primary" onClick={restartRemote} disabled={saving || uploading || remoteBusy}>
            {remoteBusy ? 'Открываю…' : remoteSessionEnsured ? 'Переоткрыть окно' : 'Открыть окно'}
          </button>
          <button type="button" className="btn btn-primary" onClick={finishRemote} disabled={!remoteOpen || saving || uploading || remoteBusy}>
            {remoteBusy ? 'Сохраняю…' : 'Я вошёл'}
          </button>
        </>
      )}
    >
      <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 12 }}>
        Если сессия уже открыта, окно появится сразу. Если сессии нет — мы запустим её автоматически. Если окно “залипло”, нажмите “Открыть окно” для перезапуска.
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

function aiDetailsForTask(t) {
  const type = String(t?.task_type || '');
  const title = String(t?.title || 'Задача');
  const base = {
    title,
    what: t?.description || 'Задача от AI-модуля для улучшения карточки товара.',
    why: 'Помогает системно улучшать карточку и проверять шаги, которые повышают продажи.',
    business: 'Рост продаж за счёт улучшения качества карточки и конкурентности.',
    hypothesis: 'Если выполнить задачу, метрики карточки улучшатся.',
    userAction: 'Выполните задачу и нажмите “Готово” (или “Отменить”, если задача неактуальна).',
    result: 'Статус задачи изменится, и вы сможете видеть прогресс по списку.',
  };

  if (type === 'wb_access_grant') {
    return {
      ...base,
      title: 'Дать доступ к кабинету WB',
      what: 'Нужно выдать доступ к кабинету WB для получения отчётов и данных сравнения.',
      userAction: 'Нажмите “Выдать доступ” в шаге 2 и авторизуйтесь.',
      result: 'После успешной авторизации onboarding-плашка исчезнет.',
    };
  }

  if (type === 'competitor_report_refresh') {
    return {
      ...base,
      title: title || 'Обновить отчёт сравнения',
      what: 'Нужно обновить отчёт сравнения карточек с конкурентами.',
      userAction: 'Создайте/обновите сравнение в кабинете WB (ваш товар + 4 конкурента), затем нажмите “Я создал сравнение”.',
      result: 'Статус отчёта станет актуальным, AI сможет создавать корректные задачи/гипотезы.',
    };
  }

  return base;
}

function aiDetailsForHypothesis(h) {
  const title = String(h?.title || 'Гипотеза');
  const trigger = String(h?.trigger_reason || '').trim();
  return {
    title,
    what: 'Гипотеза — это проверка идеи, которая потенциально улучшит метрики карточки.',
    why: 'Фокусирует усилия на проверяемых действиях вместо случайных изменений.',
    business: 'Быстрее найти работающие улучшения и масштабировать их.',
    hypothesis: trigger || 'Если выполнить действия по гипотезе, метрики карточки улучшатся.',
    userAction: 'Запустите гипотезу, выполняйте действия, фиксируйте результат и завершите её.',
    result: 'После завершения гипотеза попадёт в “Готово” и сохранится итог.',
  };
}

function AiItemDetailsModal({ openItem, onClose, onPrimaryAction, primaryActionLabel, busy }) {
  if (!openItem) return null;
  const isTask = openItem.kind === 'task';
  const isHyp = openItem.kind === 'hypothesis';
  const details = isTask ? aiDetailsForTask(openItem.data) : aiDetailsForHypothesis(openItem.data);
  const status = openItem?.data?.status;
  const taskType = String(openItem?.data?.task_type || '');
  const isWbAccessTask = isTask && taskType === 'wb_access_grant';
  return (
    <ModalShell
      open={Boolean(openItem)}
      title={details.title}
      onClose={onClose}
      footer={(
        <>
          <button type="button" className="btn btn-outline-secondary" onClick={onClose} disabled={busy}>Закрыть</button>
          {primaryActionLabel && (
            <button type="button" className="btn btn-primary" onClick={onPrimaryAction} disabled={busy}>
              {busy ? '...' : primaryActionLabel}
            </button>
          )}
          {(isTask && !isWbAccessTask && (status === 'new' || status === 'in_progress')) && (
            <>
              <button type="button" className="btn" style={{ background: 'rgba(22,163,74,0.12)', border: '1px solid rgba(22,163,74,0.18)', color: '#166534', fontWeight: 800 }} onClick={() => openItem.onSetStatus?.('completed')} disabled={busy}>
                Готово
              </button>
              <button type="button" className="btn" style={{ background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.18)', color: '#991b1b', fontWeight: 800 }} onClick={() => openItem.onSetStatus?.('cancelled')} disabled={busy}>
                Отменить
              </button>
            </>
          )}
          {isHyp && openItem.actions?.length ? openItem.actions.map((a) => (
            <button key={a.key} type="button" className={a.className} onClick={a.onClick} disabled={busy || a.disabled}>
              {a.label}
            </button>
          )) : null}
        </>
      )}
    >
      <div style={{ display: 'grid', gap: 10 }}>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
            Статус: {statusBadge(status)}
          </div>
          {openItem?.data?.nm_id != null && (
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
              Артикул: <span style={{ fontWeight: 800, color: 'var(--text-secondary)' }}>{openItem.data.nm_id}</span>
            </div>
          )}
        </div>
        <div style={{ ...softCardStyle(), padding: 12 }}>
          <InfoRow label="Что это">{details.what}</InfoRow>
          <InfoRow label="Зачем">{details.why}</InfoRow>
          <InfoRow label="Бизнес-смысл">{details.business}</InfoRow>
          <InfoRow label="Гипотеза">{details.hypothesis}</InfoRow>
          <InfoRow label="Что сделать">{details.userAction}</InfoRow>
          <div style={{ paddingTop: 8 }}>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', fontWeight: 800, letterSpacing: '0.02em', textTransform: 'uppercase' }}>
              Как понять результат
            </div>
            <div style={{ color: 'var(--text-secondary)', fontSize: 13, whiteSpace: 'pre-wrap', marginTop: 6 }}>
              {details.result}
            </div>
          </div>
        </div>
      </div>
    </ModalShell>
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

function ActionsLogModal({ open, onClose }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.getAiCompetitorReportActions(50);
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch (e) {
      setItems([]);
      setError(e?.message || 'Не удалось загрузить журнал');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    load();
  }, [open, load]);

  const rows = Array.isArray(items) ? items : [];

  return (
    <ModalShell
      open={open}
      title="Журнал обновлений отчёта"
      onClose={onClose}
      footer={(
        <>
          <button type="button" className="btn btn-outline-secondary" onClick={onClose}>Закрыть</button>
          <button type="button" className="btn btn-outline-secondary" onClick={load} disabled={loading}>
            {loading ? 'Обновляю…' : 'Обновить'}
          </button>
        </>
      )}
    >
      <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 10 }}>
        Здесь видно результат последней попытки обновить отчёт сравнения (ok/error) и текст ошибки, если она была.
      </div>
      {error && <div className="alert alert-danger" style={{ marginTop: 0 }}>{error}</div>}
      {loading ? (
        <div style={{ color: 'var(--text-tertiary)' }}>Загрузка…</div>
      ) : rows.length === 0 ? (
        <div style={{ color: 'var(--text-tertiary)' }}>Пока нет записей</div>
      ) : (
        <div className="table-wrapper" style={{ marginTop: 0 }}>
          <table className="custom-table">
            <thead>
              <tr>
                <th style={{ width: 200 }}>Время</th>
                <th style={{ width: 120 }}>Действие</th>
                <th style={{ width: 120 }}>Результат</th>
                <th>Сообщение</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((x) => {
                const res = String(x?.result || '');
                const isOk = res === 'ok';
                const isErr = res === 'error';
                return (
                  <tr key={x.id}>
                    <td style={{ fontSize: 12, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                      {x.requested_at ? String(x.requested_at).replace('T', ' ').slice(0, 19) : '—'}
                    </td>
                    <td style={{ fontWeight: 800 }}>{x.action || '—'}</td>
                    <td>
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          padding: '3px 8px',
                          borderRadius: 999,
                          fontSize: 12,
                          fontWeight: 800,
                          background: isOk ? 'rgba(16,185,129,0.12)' : isErr ? 'rgba(239,68,68,0.10)' : 'rgba(0,0,0,0.06)',
                          color: isOk ? '#047857' : isErr ? '#b91c1c' : 'var(--text-secondary)',
                          border: '1px solid rgba(0,0,0,0.06)',
                        }}
                      >
                        {res || '—'}
                      </span>
                    </td>
                    <td style={{ fontSize: 13, color: isErr ? '#b91c1c' : 'var(--text-secondary)' }}>
                      {x.error_message || '—'}
                    </td>
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

function TasksTab({ selectedNmId, onGrantAccess }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');
  const [openItem, setOpenItem] = useState(null);
  const [archiveOpen, setArchiveOpen] = useState(false);

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

  const { openItems, archivedItems } = useMemo(() => {
    const list = Array.isArray(sorted) ? sorted : [];
    const open = [];
    const archived = [];
    for (const t of list) {
      const st = String(t?.status || '').toLowerCase();
      if (st === 'completed' || st === 'cancelled') archived.push(t);
      else open.push(t);
    }
    return { openItems: open, archivedItems: archived };
  }, [sorted]);

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
    <DataTable title="Задачи" tag="ИИ модуль">
      {loading ? (
        <div style={{ padding: 12, color: 'var(--text-tertiary)' }}>Загрузка…</div>
      ) : error ? (
        <div className="alert alert-danger" style={{ margin: 12 }}>{error}</div>
      ) : openItems.length === 0 && archivedItems.length === 0 ? (
        <div style={{ padding: 12, color: 'var(--text-tertiary)' }}>Пока нет задач</div>
      ) : (
        <div style={{ display: 'grid', gap: 10, padding: 12 }}>
          {archivedItems.length > 0 && (
            <div style={{ ...softCardStyle(), padding: 12 }}>
              <button
                type="button"
                className="btn btn-sm btn-outline-secondary"
                onClick={() => setArchiveOpen((v) => !v)}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}
              >
                <span style={{ fontWeight: 900 }}>Архив</span>
                <span style={{ color: 'var(--text-tertiary)', fontWeight: 800 }}>({archivedItems.length})</span>
                <span style={{ marginLeft: 6, color: 'var(--text-tertiary)' }}>
                  {archiveOpen ? 'Свернуть' : 'Развернуть'}
                </span>
              </button>

              {archiveOpen && (
                <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
                  {archivedItems.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      className="btn"
                      onClick={() => setOpenItem({
                        kind: 'task',
                        data: t,
                        onSetStatus: (st) => setStatus(t.id, st),
                      })}
                      style={{
                        ...softCardStyle(),
                        padding: 12,
                        textAlign: 'left',
                        display: 'grid',
                        gap: 6,
                        cursor: 'pointer',
                        opacity: 0.92,
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                        <div style={{ fontWeight: 900, fontSize: 13, color: 'var(--text-primary)' }}>{t.title}</div>
                        <div style={{ marginLeft: 'auto' }}>{statusBadge(t.status)}</div>
                      </div>
                      {t.description && (
                        <div style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
                          {t.description}
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {openItems.map((t) => (
            <button
              key={t.id}
              type="button"
              className="btn"
              onClick={() => setOpenItem({
                kind: 'task',
                data: t,
                onSetStatus: (st) => setStatus(t.id, st),
              })}
              style={{
                ...softCardStyle(),
                padding: 12,
                textAlign: 'left',
                display: 'grid',
                gap: 6,
                cursor: 'pointer',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <div style={{ fontWeight: 900, fontSize: 13, color: 'var(--text-primary)' }}>{t.title}</div>
                <div style={{ marginLeft: 'auto' }}>{statusBadge(t.status)}</div>
              </div>
              {t.description && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
                  {t.description}
                </div>
              )}
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 2 }}>
                {(t.status === 'new' || t.status === 'in_progress') && (
                  <>
                    <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Действия:</span>
                    <span style={{ fontSize: 12, color: '#166534', fontWeight: 800 }}>Готово</span>
                    <span style={{ fontSize: 12, color: '#991b1b', fontWeight: 800 }}>Отменить</span>
                  </>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
      <AiItemDetailsModal
        openItem={openItem}
        onClose={() => setOpenItem(null)}
        primaryActionLabel={openItem?.kind === 'task' && String(openItem?.data?.task_type || '') === 'wb_access_grant' ? 'Выдать доступ' : ''}
        onPrimaryAction={() => {
          if (openItem?.kind === 'task' && String(openItem?.data?.task_type || '') === 'wb_access_grant') {
            onGrantAccess?.();
          }
        }}
        busy={Boolean(busyId)}
      />
    </DataTable>
  );
}

function HypothesesTab({ selectedNmId }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');
  const [resultSummary, setResultSummary] = useState({});
  const [openItem, setOpenItem] = useState(null);

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
    } catch (e) {
      setError(e?.message || 'Ошибка');
    } finally {
      setBusyId('');
    }
  };

  return (
    <DataTable title="Гипотезы" tag="ИИ модуль">
      {loading ? (
        <div style={{ padding: 12, color: 'var(--text-tertiary)' }}>Загрузка…</div>
      ) : error ? (
        <div className="alert alert-danger" style={{ margin: 12 }}>{error}</div>
      ) : sorted.length === 0 ? (
        <div style={{ padding: 12, color: 'var(--text-tertiary)' }}>Пока нет гипотез</div>
      ) : (
        <div style={{ display: 'grid', gap: 10, padding: 12 }}>
          {sorted.map((h) => {
            const actions = [];
            if (h.status === 'draft') {
              actions.push({
                key: 'start',
                label: 'Запустить',
                className: 'btn btn-primary',
                onClick: () => start(h.id),
                disabled: false,
              });
            }
            if (h.status === 'running') {
              actions.push({
                key: 'finish',
                label: 'Завершить',
                className: 'btn btn-primary',
                onClick: () => finish(h.id),
                disabled: false,
              });
            }

            return (
              <button
                key={h.id}
                type="button"
                className="btn"
                onClick={() => setOpenItem({
                  kind: 'hypothesis',
                  data: h,
                  actions,
                })}
                style={{
                  ...softCardStyle(),
                  padding: 12,
                  textAlign: 'left',
                  display: 'grid',
                  gap: 6,
                  cursor: 'pointer',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <div style={{ fontWeight: 900, fontSize: 13, color: 'var(--text-primary)' }}>{h.title}</div>
                  <div style={{ marginLeft: 'auto' }}>{statusBadge(h.status)}</div>
                </div>
                {h.trigger_reason && (
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
                    {h.trigger_reason}
                  </div>
                )}
                {h.status === 'running' && (
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', marginTop: 2 }}>
                    <input
                      className="form-control form-control-sm"
                      style={{ width: 260 }}
                      value={resultSummary[h.id] || ''}
                      placeholder="Итог (коротко)"
                      onChange={(e) => setResultSummary((m) => ({ ...m, [h.id]: e.target.value }))}
                      onClick={(e) => e.stopPropagation()}
                    />
                    <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                      Откройте карточку, чтобы завершить
                    </span>
                  </div>
                )}
              </button>
            );
          })}
        </div>
      )}
      <AiItemDetailsModal
        openItem={openItem}
        onClose={() => setOpenItem(null)}
        busy={Boolean(busyId)}
      />
    </DataTable>
  );
}

export default function AiModule() {
  const [selectedNmId, setSelectedNmId] = useState(() => {
    const v = (lsGet(LS_SELECTED_NM_ID) || '').trim();
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : null;
  });
  const [pickerOpen, setPickerOpen] = useState(false);
  const [wbModalOpen, setWbModalOpen] = useState(false);
  const [onboardingConfirmed, setOnboardingConfirmed] = useState(() => (lsGet(LS_ONBOARDING_CONFIRMED) || '') === '1');

  const [_credsStatus, setCredsStatus] = useState(null);
  const [remoteStatus, setRemoteStatus] = useState(null);
  const [accessStatus, setAccessStatus] = useState(null);
  const [reportStatus, setReportStatus] = useState(null);
  const [comparisonBusy, setComparisonBusy] = useState(false);
  const [comparisonError, setComparisonError] = useState('');
  const [actionsOpen, setActionsOpen] = useState(false);

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

  const loadRemoteStatus = useCallback(async () => {
    try {
      const st = await api.getAiWbRemoteAuthStatus();
      setRemoteStatus(st);
    } catch {
      // ignore; fallback to creds status only
    }
  }, []);

  const loadAccessStatus = useCallback(async () => {
    try {
      const st = await api.getAiWbAccessStatus();
      setAccessStatus(st);
    } catch {
      // ignore; screen still works
    }
  }, []);

  useEffect(() => {
    loadReport();
    loadCreds();
    loadRemoteStatus();
    loadAccessStatus();
  }, [loadReport, loadCreds, loadRemoteStatus, loadAccessStatus]);

  useEffect(() => {
    // If product is missing, onboarding can't be confirmed.
    if (!selectedNmId && onboardingConfirmed) {
      setOnboardingConfirmed(false);
      lsSet(LS_ONBOARDING_CONFIRMED, '');
    }
  }, [selectedNmId, onboardingConfirmed]);

  const calloutHidden = useMemo(() => (lsGet(LS_HIDE_COMPARISON_CALLOUT) || '') === '1', []);
  const showComparisonCallout = useMemo(() => {
    if (!selectedNmId) return false;
    if (calloutHidden) return false;
    const st = (reportStatus?.status || '').toLowerCase();
    return st === 'missing' || st === 'stale';
  }, [selectedNmId, reportStatus, calloutHidden]);

  const remoteSessionActive = useMemo(() => Boolean(remoteStatus?.active), [remoteStatus]);
  const hasSavedAccess = useMemo(() => Boolean(accessStatus?.has_storage_state), [accessStatus]);
  const needsWbAccess = useMemo(() => {
    // Blocking rule: only block when access is not saved and remote session is not active.
    // Credentials presence is not a reliable signal (storage_state is).
    if (hasSavedAccess) return false;
    if (remoteSessionActive) return false;
    return true;
  }, [remoteSessionActive, hasSavedAccess]);
  const onboardingStep = useMemo(() => {
    if (!onboardingConfirmed) return 1;
    if (needsWbAccess) return 2;
    return 0;
  }, [onboardingConfirmed, needsWbAccess]);
  const onboardingDone = onboardingStep === 0;

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
    } catch (e) {
      setComparisonError(e?.message || 'Не удалось создать задачу');
    } finally {
      setComparisonBusy(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <FirstRunBanner
        step={onboardingStep || null}
        selectedNmId={selectedNmId}
        needsWbAccess={needsWbAccess}
        onPickProduct={() => setPickerOpen(true)}
        onConfirmProduct={() => {
          setOnboardingConfirmed(true);
          lsSet(LS_ONBOARDING_CONFIRMED, '1');
          setComparisonError('');
        }}
        onGrantAccess={() => setWbModalOpen(true)}
        busy={comparisonBusy}
        errorText={comparisonError}
      />

      {onboardingDone && (
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            <div style={{ fontWeight: 900, fontSize: 16 }}>Задачи и гипотезы</div>
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => setPickerOpen(true)}>
              Сменить товар
            </button>
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => setActionsOpen(true)} style={{ marginLeft: 'auto' }}>
              Журнал обновлений
            </button>
          </div>

          <ComparisonCallout
            visible={showComparisonCallout}
            onConfirmCreated={onConfirmCreated}
            onLater={onLater}
            onCreateTechnicalTask={onCreateTechnicalTask}
            busy={comparisonBusy}
            errorText={comparisonError}
          />

          <div style={{ display: 'grid', gap: 12 }}>
            <TasksTab selectedNmId={selectedNmId} onGrantAccess={() => setWbModalOpen(true)} />
            <HypothesesTab selectedNmId={selectedNmId} />
          </div>
        </>
      )}

      <ProductPickerModal
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onSelectNmId={(nm) => {
          setSelectedNmId(nm);
          lsSet(LS_SELECTED_NM_ID, String(nm));
          lsSet(LS_HIDE_COMPARISON_CALLOUT, '');
          setOnboardingConfirmed(false);
          lsSet(LS_ONBOARDING_CONFIRMED, '');
          setComparisonError('');
        }}
      />
      <WbAccessModal
        open={wbModalOpen}
        onClose={() => setWbModalOpen(false)}
        onGranted={() => {
          // Optimistic: hide onboarding immediately after successful save/upload,
          // then refresh status from server.
          setCredsStatus({ status: 'ok' });
          setAccessStatus({ status: 'ok', has_storage_state: true });
          loadCreds();
          loadRemoteStatus();
          loadAccessStatus();
          setComparisonError('');
        }}
      />
      <ActionsLogModal
        open={actionsOpen}
        onClose={() => setActionsOpen(false)}
      />
    </div>
  );
}

