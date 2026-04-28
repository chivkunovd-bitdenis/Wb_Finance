import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from './AuthContext';
import { useCache } from './CacheContext';
import * as api from './api';
import Sidebar from './components/Sidebar';
import Topbar from './components/Topbar';
import Dashboard from './screens/Dashboard';
import Articles from './screens/Articles';
import Funnel from './screens/Funnel';
import Costs from './screens/Costs';
import OperationalExpenses from './screens/OperationalExpenses';
import Billing from './screens/Billing';
import Settings from './screens/Settings';
import { useStore } from './StoreContext';

export default function Layout() {
  const { logout: authLogout } = useAuth();
  const { cache, updateCache, clearCache } = useCache();
  const { activeStoreOwnerId, activeStoreLabel } = useStore();
  const logout = useCallback(() => {
    clearCache();
    authLogout();
  }, [clearCache, authLogout]);

  const location = useLocation();
  const navigate = useNavigate();

  function toLocalIsoDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  function getDefaultRange() {
    const today = new Date();
    const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    // Если сегодня 1-е, "вчера" ещё в прошлом месяце — чтобы не получить пустой диапазон, зажимаем dateTo к monthStart.
    const to = yesterday < monthStart ? monthStart : yesterday;
    return { dateFrom: toLocalIsoDate(monthStart), dateTo: toLocalIsoDate(to) };
  }

  // Применённые даты: именно они используются для запросов к API (табами)
  const [dateFrom, setDateFrom] = useState(() => {
    return getDefaultRange().dateFrom;
  });
  const [dateTo, setDateTo] = useState(() => getDefaultRange().dateTo);

  // Черновые даты для инпутов: меняем их сколько угодно, но запросы обновятся только после нажатия "Показать"
  const [dateFromDraft, setDateFromDraft] = useState(() => {
    return getDefaultRange().dateFrom;
  });
  const [dateToDraft, setDateToDraft] = useState(() => getDefaultRange().dateTo);
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [updating, setUpdating] = useState(false);
  const [updateSyncing, setUpdateSyncing] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [initialTriggered, setInitialTriggered] = useState(false);
  const [waitForFunnelAfterInitial, setWaitForFunnelAfterInitial] = useState(false);
  const [initialError, setInitialError] = useState('');
  const [dashboardState, setDashboardState] = useState(null);
  const [backfill2026Syncing, setBackfill2026Syncing] = useState(false);
  const [backfill2026TriggeredOnce, setBackfill2026TriggeredOnce] = useState(false);
  const [backfill2025Syncing, setBackfill2025Syncing] = useState(false);
  const [backfill2025TriggeredOnce, setBackfill2025TriggeredOnce] = useState(false);
  const [funnelYtdLaunchPending, setFunnelYtdLaunchPending] = useState(false);
  const funnelYtdBootstrappedRef = useRef(false);
  const [billingStatus, setBillingStatus] = useState(null);

  const range = useMemo(() => ({ dateFrom, dateTo }), [dateFrom, dateTo]);
  const funnelYtdStatus = dashboardState?.funnel_ytd_backfill?.status || 'idle';
  const financeBackfill2026 = dashboardState?.finance_backfill || null;
  const financeBackfill2025 = dashboardState?.finance_backfill_2025 || null;
  const financeMissingSync = dashboardState?.finance_missing_sync || null;
  const funnelTailSync = dashboardState?.funnel_tail_sync || null;
  const financeStatus2026 = financeBackfill2026?.status || 'idle';
  const financeStatus2025 = financeBackfill2025?.status || 'idle';
  const financeMissingActive = Boolean(
    financeMissingSync &&
      (['queued', 'running'].includes(financeMissingSync.status) ||
        (financeMissingSync.status === 'idle' && financeMissingSync.next_run_at)),
  );
  const funnelTailActive = Boolean(
    funnelTailSync?.pending || ['queued', 'scheduled', 'running', 'cooldown'].includes(funnelTailSync?.status),
  );
  const financeMissingMinutesLeft = useMemo(() => {
    const nextRunAt = financeMissingSync?.next_run_at;
    if (!nextRunAt) return null;
    const ms = new Date(nextRunAt).getTime() - Date.now();
    if (!Number.isFinite(ms)) return null;
    const m = Math.ceil(ms / 60000);
    return m > 0 ? m : 0;
  }, [financeMissingSync?.next_run_at]);
  const loadBillingStatus = useCallback(async () => {
    try {
      const data = await api.getBillingStatus();
      setBillingStatus(data);
    } catch (e) {
      if (e?.message === 'unauthorized') {
        clearCache();
        authLogout();
      }
    }
  }, [authLogout, clearCache]);

  // When store context changes, reset dashboard-specific state (but keep cache; it's store-scoped with LRU).
  useEffect(() => {
    setDashboardState(null);
    setInitialLoading(true);
    setInitialTriggered(false);
    setWaitForFunnelAfterInitial(false);
    setInitialError('');
    setBackfill2026Syncing(false);
    setBackfill2026TriggeredOnce(false);
    setBackfill2025Syncing(false);
    setBackfill2025TriggeredOnce(false);
    setFunnelYtdLaunchPending(false);
    funnelYtdBootstrappedRef.current = false;
    setRefreshTrigger((t) => t + 1);
  }, [activeStoreOwnerId]);

  const onUpdateWb = useCallback(async () => {
    if (!dateFrom || !dateTo) {
      alert('Укажите период для обновления данных');
      return;
    }
    setUpdating(true);
    setUpdateSyncing(true);
    try {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

      // Подпись “до”: чтобы понять, что витрина реально обновилась
      const signatureOf = (rows) => {
        const list = Array.isArray(rows) ? rows : [];
        const totalRevenue = list.reduce((acc, r) => acc + (Number(r.revenue) || 0), 0);
        const last = list[list.length - 1] || {};
        return JSON.stringify({
          len: list.length,
          totalRevenue: Math.round(totalRevenue * 100) / 100,
          lastDate: last.date || null,
          lastRev: Math.round((Number(last.revenue) || 0) * 100) / 100,
        });
      };

      const beforeRows = await api.getPnl(dateFrom, dateTo);
      const beforeSig = signatureOf(beforeRows);

      await api.triggerSyncSales(dateFrom, dateTo);
      await api.triggerSyncAds(dateFrom, dateTo);
      // Контракт: воронка синкается только в rolling окне 7 дней (сервер сам выбирает окно).
      await api.triggerSyncFunnel();

      // sync_sales/sync_ads уже ставят recalculate_* после записи raw данных,
      // но это асинхронно. Поэтому ждём появления изменений на витрине.
      const startedAt = Date.now();
      const timeoutMs = 180000; // 3 минуты
      while (Date.now() - startedAt < timeoutMs) {
        await sleep(5000);
        const afterRows = await api.getPnl(dateFrom, dateTo);
        if (signatureOf(afterRows) !== beforeSig) {
          setRefreshTrigger((t) => t + 1);
          return;
        }
      }

      setRefreshTrigger((t) => t + 1);
      alert('Обновление запущено, но витрина не успела измениться за 3 минуты. Проверь логи воркера.');
    } catch (err) {
      alert('Сбой обновления: ' + (err.message || err));
    } finally {
      setUpdating(false);
      setUpdateSyncing(false);
    }
  }, [dateFrom, dateTo]);

  // Инициализация как в GAS: проверяем состояние данных и при необходимости запускаем первую синхронизацию
  useEffect(() => {
    let cancelled = false;
    let attempts = 0;

    async function checkAndMaybeInit() {
      try {
        const state = await api.getDashboardState();
        if (cancelled) return;
        setDashboardState(state);
        attempts += 1;

        if (!state.has_data && !initialTriggered) {
          // Новый пользователь — запускаем первую синхронизацию за последние 30 дней
          setInitialTriggered(true);
          setWaitForFunnelAfterInitial(false);
          try {
            await api.triggerInitialSync();
          } catch (e) {
            console.warn('Ошибка запуска первой синхронизации', e);
            setInitialError(
              e?.message ||
                'Не удалось запустить синхронизацию. Проверь WB API ключ; если ключ есть — возможно, не запущен celery_worker/redis на сервере.',
            );
            setInitialLoading(false);
            return;
          }
          // продолжаем опрашивать состояние ниже
        }

        const hasRequiredInitialData = state.has_data;
        if (hasRequiredInitialData) {
          setInitialLoading(false);
          setRefreshTrigger((t) => t + 1);
        } else {
          // Защита от вечного лоадера: ~3 минуты ожидания (36 попыток по 5 секунд)
          if (attempts >= 36) {
            setInitialError(
              'Синхронизация не завершилась за 3 минуты. Проверь WB API ключ и логи celery_worker (очередь Redis).',
            );
            setInitialLoading(false);
            return;
          }
          // данных ещё нет — подождём и перепроверим
          setTimeout(() => {
            if (!cancelled) {
              checkAndMaybeInit();
            }
          }, 5000);
        }
      } catch (e) {
        console.warn('Ошибка получения состояния дашборда', e);
        // Если токен протух/невалидный — корректно выведем пользователя на логин.
        if (e?.message === 'unauthorized') {
          clearCache();
          authLogout();
          return;
        }
        // При ошибке всё равно пробуем запустить первую синхронизацию и показываем лоадер
        if (!initialTriggered) {
          setInitialTriggered(true);
          try {
            await api.triggerInitialSync();
          } catch (e2) {
            console.warn('Ошибка запуска первой синхронизации', e2);
            if (e2?.message === 'unauthorized') {
              clearCache();
              authLogout();
              return;
            }
            setInitialError(
              e2?.message ||
                'Не удалось запустить синхронизацию. Проверь WB API ключ; если ключ есть — возможно, не запущен celery_worker/redis на сервере.',
            );
          }
        }
        setInitialLoading(false);
      }
    }

    checkAndMaybeInit();

    return () => {
      cancelled = true;
    };
  }, [initialTriggered, waitForFunnelAfterInitial, authLogout, clearCache, activeStoreOwnerId]);

  // Если данных 2026 ещё нет — запускаем фоновую догрузку 2026 (как в GAS loader-2026)
  useEffect(() => {
    if (initialLoading) return;
    if (!dashboardState || !dashboardState.has_data) return;
    if (dashboardState.has_2026) return;
    if (backfill2026TriggeredOnce) return;
    setBackfill2026TriggeredOnce(true);
    setBackfill2026Syncing(true);
    console.log('[WB FINANCE] Догрузка 2026 /sync/backfill/2026');
    api.triggerBackfill2026()
      .catch((e) => {
        console.warn('Ошибка догрузки 2026', e);
      })
      .finally(() => {
        setBackfill2026Syncing(false);
        // Перечитаем state, чтобы понять, появился ли 2026, и обновим табы
        api.getDashboardState()
          .then((s) => setDashboardState(s))
          .catch(() => {})
          .finally(() => setRefreshTrigger((t) => t + 1));
      });
  }, [initialLoading, dashboardState, backfill2026TriggeredOnce]);

  useEffect(() => {
    if (funnelYtdStatus !== 'running' && !funnelYtdLaunchPending) return;
    const id = setInterval(() => {
      api
        .getDashboardState()
        .then((s) => {
          setDashboardState(s);
          const st = s.funnel_ytd_backfill?.status;
          if (st === 'running') {
            setFunnelYtdLaunchPending(false);
            return;
          }
          if (st) {
            setFunnelYtdLaunchPending(false);
            setRefreshTrigger((t) => t + 1);
          }
        })
        .catch(() => {});
    }, 5000);
    return () => clearInterval(id);
  }, [funnelYtdStatus, funnelYtdLaunchPending]);

  useEffect(() => {
    if (!financeMissingActive && !funnelTailActive) return;
    const id = setInterval(() => {
      api
        .getDashboardState()
        .then((s) => {
          setDashboardState(s);
          setRefreshTrigger((t) => t + 1);
        })
        .catch(() => {});
    }, 5000);
    return () => clearInterval(id);
  }, [financeMissingActive, funnelTailActive]);

  // Архив 2025: если есть данные, но 2025 ещё нет — запускаем фоновую догрузку (неблокирующая жёлтая плашка, как в GAS)
  useEffect(() => {
    if (initialLoading) return;
    if (!dashboardState?.has_data) return;
    if (dashboardState.has_2025) return;
    // 2025 стартуем только после завершения догрузки 2026 (иначе это "съест" лимиты и будет выглядеть как регрессия).
    if (financeStatus2026 !== 'complete') return;
    if (backfill2025TriggeredOnce) return;
    setBackfill2025TriggeredOnce(true);
    setBackfill2025Syncing(true);
    console.log('[WB FINANCE] Догрузка архива 2025 /sync/backfill/2025');
    api.triggerBackfill2025()
      .catch((e) => {
        console.warn('Ошибка догрузки архива 2025', e);
        setBackfill2025Syncing(false);
      });
  }, [initialLoading, dashboardState?.has_data, dashboardState?.has_2025, backfill2025TriggeredOnce, financeStatus2026]);

  // Пока идёт догрузка 2025 — опрашиваем state; когда has_2025 станет true, гасим плашку
  useEffect(() => {
    if (!backfill2025Syncing) return;
    const id = setInterval(() => {
      api.getDashboardState()
        .then((s) => {
          setDashboardState(s);
          if (s.has_2025) setBackfill2025Syncing(false);
        })
        .catch(() => setBackfill2025Syncing(false));
    }, 15000);
    return () => clearInterval(id);
  }, [backfill2025Syncing]);

  // Снять плашку 2025, когда state обновился и has_2025 стал true (напр. после ручного обновления)
  useEffect(() => {
    if (dashboardState?.has_2025 && backfill2025Syncing) setBackfill2025Syncing(false);
  }, [dashboardState?.has_2025, backfill2025Syncing]);

  const screenTitle = useMemo(() => {
    const p = location.pathname || '';
    if (p.startsWith('/articles')) return 'Артикулы';
    if (p.startsWith('/funnel')) return 'Воронка';
    if (p.startsWith('/costs')) return 'Себестоимость';
    if (p.startsWith('/operational-expenses')) return 'Опер. расходы';
    if (p.startsWith('/billing')) return 'Подписка';
    return 'Дашборд';
  }, [location.pathname]);

  /** Ошибка первичной синхронизации не должна блокировать «Подписку» и оплату. */
  const isBillingPath = (location.pathname || '').startsWith('/billing');
  const blockContentForSyncError = Boolean(initialError) && !isBillingPath;

  useEffect(() => {
    loadBillingStatus();
  }, [loadBillingStatus]);

  return (
    <div className="layout">
      <Sidebar onLogout={logout} />

      <div className="main">
        <Topbar
          title={screenTitle}
          activeStoreLabel={activeStoreLabel}
          dateFromDraft={dateFromDraft}
          dateToDraft={dateToDraft}
          setDateFromDraft={setDateFromDraft}
          setDateToDraft={setDateToDraft}
          onApply={() => {
            const df = dateFromDraft;
            const dt = dateToDraft;
            setDateFrom(df);
            setDateTo(dt);
            setRefreshTrigger((t) => t + 1);
          }}
          onUpdateWb={onUpdateWb}
          onOpenBilling={() => navigate('/billing')}
          updating={updating}
          updateSyncing={updateSyncing}
        />

        <div className="content">
          {initialLoading ? (
            <div className="loader-center">
              <div className="loader-spinner" />
              <p style={{ fontWeight: 700 }}>Первая синхронизация данных с Wildberries...</p>
              <p style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>
                Мы загружаем последние 30 дней. Это может занять несколько минут, в зависимости от объёма данных.
              </p>
            </div>
          ) : blockContentForSyncError ? (
            <div style={{ padding: 16, border: '1px solid rgba(0,0,0,0.12)', borderRadius: 12, maxWidth: 720, margin: '0 auto' }}>
              <div style={{ fontWeight: 700, marginBottom: 6 }}>Синхронизация не стартовала или зависла</div>
              <div>{initialError}</div>
              <div style={{ marginTop: 10, color: 'var(--text-tertiary)', fontSize: 12 }}>
                Быстрый чек: открой <code>{`${window.location.origin}/docs`}</code> и попробуй <code>POST /sync/initial</code>.
              </div>
              <div style={{ marginTop: 12, fontSize: 13 }}>
                Раздел <strong>Подписка</strong> в боковом меню открывается даже при этой ошибке — можно оплатить доступ.
              </div>
            </div>
          ) : (
            <>
              {initialError && isBillingPath && (
                <div
                  role="alert"
                  style={{
                    marginBottom: 16,
                    padding: '12px 14px',
                    borderRadius: 8,
                    background: 'var(--red-light)',
                    color: 'var(--red)',
                    border: '0.5px solid #fca5a5',
                    fontSize: 13,
                    lineHeight: 1.45,
                  }}
                >
                  <strong>Загрузка данных WB:</strong> {initialError}
                  <div style={{ marginTop: 6, fontSize: 12, opacity: 0.9 }}>
                    Оплата и раздел «Подписка» ниже работают. Дашборд станет доступен после успешной синхронизации.
                  </div>
                </div>
              )}
              {backfill2026Syncing && (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    background: '#cce5ff',
                    color: '#004085',
                    padding: '10px 15px',
                    borderRadius: 8,
                    border: '1px solid #b8daff',
                    width: 'fit-content',
                  }}
                >
                  🔄 Запускаем догрузку финансов за 2026 год (продажи+реклама → P&L)…
                  <span className="loader-spinner-sm" style={{ marginLeft: 4 }} />
                </div>
              )}
              {(financeStatus2026 === 'running' || financeStatus2026 === 'idle') && financeBackfill2026?.through_date && (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    background: '#cce5ff',
                    color: '#004085',
                    padding: '10px 15px',
                    borderRadius: 8,
                    border: '1px solid #b8daff',
                    width: 'fit-content',
                  }}
                >
                  🔄 Догружаем финансы за {financeBackfill2026.year} год (продажи+реклама → P&L) до{' '}
                  {new Date(financeBackfill2026.through_date + 'T12:00:00').toLocaleDateString('ru')}
                  {financeBackfill2026.last_completed_date
                    ? ` (сейчас: ${new Date(financeBackfill2026.last_completed_date + 'T12:00:00').toLocaleDateString('ru')})`
                    : ''}
                  <span className="loader-spinner-sm" style={{ marginLeft: 4 }} />
                </div>
              )}
              {(financeMissingSync?.status === 'queued'
                || financeMissingSync?.status === 'running'
                || (financeMissingSync?.status === 'idle' && financeMissingSync?.next_run_at)
                || financeMissingSync?.status === 'error'
                || funnelTailActive) && (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    background: '#e7f3ff',
                    color: '#004085',
                    padding: '10px 15px',
                    borderRadius: 8,
                    border: '1px solid #b8daff',
                    width: 'fit-content',
                    flexWrap: 'wrap',
                  }}
                >
                  <span className="loader-spinner-sm" aria-hidden />
                  <span>
                    🔄 Догружаем финансы и воронку по пропущенным дням
                    {financeMissingSync ? ` (${financeMissingSync.date_from}–${financeMissingSync.date_to})` : ''}
                    {financeMissingSync?.next_run_at ? (
                      financeMissingMinutesLeft != null
                        ? `; следующая попытка через ~${financeMissingMinutesLeft} мин (${new Date(financeMissingSync.next_run_at).toLocaleString('ru')})`
                        : `; следующая попытка: ${new Date(financeMissingSync.next_run_at).toLocaleString('ru')}`
                    ) : ''}
                    {financeMissingSync?.status === 'error' && financeMissingSync.error_message
                      ? `; ошибка: ${financeMissingSync.error_message}`
                      : ''}
                  </span>
                </div>
              )}
              {(financeStatus2025 === 'running' || backfill2025Syncing) && financeBackfill2025?.through_date && (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    background: '#fff3cd',
                    color: '#856404',
                    padding: '10px 15px',
                    borderRadius: 8,
                    border: '1px solid #ffeeba',
                    width: 'fit-content',
                  }}
                >
                  🔄 Догружаем архивные финансы за {financeBackfill2025.year} год (продажи+реклама → P&L) до{' '}
                  {new Date(financeBackfill2025.through_date + 'T12:00:00').toLocaleDateString('ru')}
                  {financeBackfill2025.last_completed_date
                    ? ` (сейчас: ${new Date(financeBackfill2025.last_completed_date + 'T12:00:00').toLocaleDateString('ru')})`
                    : ''}
                  <span className="loader-spinner-sm" style={{ marginLeft: 4 }} />
                </div>
              )}
              {(funnelYtdStatus === 'running' || funnelYtdLaunchPending) && dashboardState?.funnel_ytd_backfill && (
                <div
                  data-testid="funnel-ytd-banner"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    flexWrap: 'wrap',
                    background: '#e8f4ec',
                    color: '#155724',
                    padding: '10px 15px',
                    borderRadius: 8,
                    border: '1px solid #c3e6cb',
                    maxWidth: '100%',
                  }}
                >
                  <span className="loader-spinner-sm" aria-hidden />
                  <span>
                    {funnelYtdStatus === 'running' ? 'Догружаем воронку' : 'Запускаем догрузку воронки'} за {dashboardState.funnel_ytd_backfill.year} год до{' '}
                    {dashboardState.funnel_ytd_backfill.through_date
                      ? new Date(dashboardState.funnel_ytd_backfill.through_date + 'T12:00:00').toLocaleDateString('ru')
                      : '…'}
                    {dashboardState.funnel_ytd_backfill.last_completed_date
                      ? ` (сейчас: ${new Date(dashboardState.funnel_ytd_backfill.last_completed_date + 'T12:00:00').toLocaleDateString('ru')})`
                      : ''}
                    . Можно пользоваться дашбордом — процесс в фоне.
                  </span>
                </div>
              )}
              {dashboardState?.funnel_ytd_backfill?.status === 'error' &&
                dashboardState.funnel_ytd_backfill.error_message && (
                  <div className="alert alert-danger" style={{ marginBottom: 0 }} data-testid="funnel-ytd-error">
                    Догрузка воронки остановилась: {dashboardState.funnel_ytd_backfill.error_message}
                  </div>
                )}

              {billingStatus?.subscription_status !== 'lifetime' && billingStatus?.days_left <= 3 && (
                <div className="alert alert-danger" style={{ marginBottom: 12 }}>
                  Срок подписки/демо скоро закончится. Осталось дней: {billingStatus.days_left}. Перейди в раздел "Подписка".
                </div>
              )}
              <Routes>
                {billingStatus?.is_access_blocked ? (
                  <>
                    <Route
                      path="/billing"
                      element={<Billing billingStatus={billingStatus} onRefreshStatus={loadBillingStatus} />}
                    />
                    <Route path="*" element={<Navigate to="/billing" replace />} />
                  </>
                ) : (
                  <>
                <Route
                  path="/dashboard"
                  element={<Dashboard range={range} refreshTrigger={refreshTrigger} cache={cache} updateCache={updateCache} />}
                />
                <Route
                  path="/articles"
                  element={<Articles range={range} refreshTrigger={refreshTrigger} cache={cache} updateCache={updateCache} />}
                />
                <Route
                  path="/funnel"
                  element={<Funnel range={range} refreshTrigger={refreshTrigger} cache={cache} updateCache={updateCache} dashboardState={dashboardState} />}
                />
                <Route
                  path="/costs"
                  element={
                    <Costs
                      range={range}
                      refreshTrigger={refreshTrigger}
                      cache={cache}
                      updateCache={updateCache}
                      onRefresh={() => setRefreshTrigger((t) => t + 1)}
                    />
                  }
                />
                <Route
                  path="/operational-expenses"
                  element={
                    <OperationalExpenses
                      range={range}
                      refreshTrigger={refreshTrigger}
                      cache={cache}
                      updateCache={updateCache}
                      onRefresh={() => setRefreshTrigger((t) => t + 1)}
                    />
                  }
                />
                <Route
                  path="/billing"
                  element={<Billing billingStatus={billingStatus} onRefreshStatus={loadBillingStatus} />}
                />
                <Route path="/settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/dashboard" replace />} />
                  </>
                )}
              </Routes>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
