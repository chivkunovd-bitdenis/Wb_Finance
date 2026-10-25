/* eslint react-hooks/set-state-in-effect: off */
import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../api';

const POLL_MS = 5000;

function formatDate(value) {
  if (!value) return '';
  const parts = String(value).split('-');
  return parts.length >= 3 ? `${parts[2]}.${parts[1]}.${parts[0]}` : String(value);
}

function warningText(warning) {
  if (typeof warning === 'string') return warning;
  if (warning && typeof warning === 'object') {
    return warning.message || warning.detail || warning.description || warning.code || 'Есть предупреждение по данным';
  }
  return String(warning ?? 'Есть предупреждение по данным');
}

function isKnownNumber(value) {
  return value !== null && value !== undefined && Number.isFinite(Number(value));
}

/** Share (percent) of the period's revenue whose SKU cost is known: the same subset the dashboard's COGS and margin use. */
function costCoveragePercent(daily) {
  let revenue = 0;
  let withCost = 0;
  for (const row of daily || []) {
    if (!isKnownNumber(row.revenue) || row.revenue <= 0) continue;
    revenue += row.revenue;
    if (isKnownNumber(row.known_cost_revenue)) withCost += row.known_cost_revenue;
  }
  if (revenue <= 0) return null;
  return Math.round((withCost / revenue) * 100);
}

function sentenceCase(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

/**
 * One compact notice above the Ozon screens, in the WB notice style. The headline is one sentence about the most
 * important state; everything else (every warning, missing days, sync and profit basis) sits in «Подробнее».
 */
export default function MarketplaceNotices({ marketplace, range, refreshTrigger, onProgress }) {
  const [connection, setConnection] = useState(null);
  const [overview, setOverview] = useState(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const progressKeyRef = useRef('');
  const onProgressRef = useRef(onProgress);
  useEffect(() => {
    onProgressRef.current = onProgress;
  }, [onProgress]);

  const dateFrom = range?.dateFrom;
  const dateTo = range?.dateTo;

  const loadConnection = useCallback(() => api.getMarketplaceConnection().then((data) => {
    setConnection(data);
    return data;
  }), []);

  const loadOverview = useCallback(() => {
    if (!dateFrom || !dateTo) return Promise.resolve(null);
    return api.getMarketplaceFinance(marketplace, dateFrom, dateTo).then((data) => {
      setOverview(data);
      setError('');
      return data;
    });
  }, [marketplace, dateFrom, dateTo]);

  const refreshAll = useCallback(() => {
    setRefreshing(true);
    Promise.all([loadConnection(), loadOverview()])
      .catch((e) => setError(e?.message || 'Не удалось загрузить статус Ozon.'))
      .finally(() => setRefreshing(false));
  }, [loadConnection, loadOverview]);

  useEffect(() => {
    refreshAll();
  }, [refreshAll, refreshTrigger]);

  const backfill = connection?.backfill || null;
  const sync = connection?.sync || null;
  const syncActive = ['queued', 'pending', 'running'].includes(String(sync?.status || '').toLowerCase());
  const backfillActive = backfill?.status === 'running';
  const active = syncActive || backfillActive;

  // Poll only while data is moving. Each time a chunk lands, the shared screens reload through onProgress.
  useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(() => {
      loadConnection()
        .then((data) => {
          const next = JSON.stringify([data?.sync?.status, data?.sync?.progress, data?.backfill?.last_completed_date, data?.backfill?.status]);
          if (next !== progressKeyRef.current) {
            progressKeyRef.current = next;
            loadOverview().catch(() => {});
            if (typeof onProgressRef.current === 'function') onProgressRef.current();
          }
        })
        .catch(() => {});
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [active, loadConnection, loadOverview]);

  if (!dateFrom || !dateTo) return null;

  const configured = connection?.configured === true;
  const missingDates = Array.isArray(overview?.coverage?.missing_dates) ? overview.coverage.missing_dates : [];
  const warnings = Array.isArray(overview?.warnings) ? overview.warnings : [];
  const costPercent = costCoveragePercent(overview?.daily);

  // Headline: the single most important state, then the period's coverage facts.
  let headline = '';
  if (connection && !configured) {
    headline = marketplace === 'all'
      ? 'кабинет Ozon не подключён, поэтому общий итог неполный.'
      : 'кабинет Ozon не подключён: добавьте Client ID и API-ключ в разделе «Настройки».';
  } else if (connection?.status === 'error' && connection?.error) {
    headline = `ошибка подключения Ozon: ${connection.error}.`;
  } else if (backfill?.status === 'failed') {
    headline = `догрузка Ozon остановилась: ${backfill.error || 'причина не сохранена'}.`;
  } else if (backfillActive) {
    headline = `догружаем данные Ozon, готово до ${formatDate(backfill.last_completed_date || backfill.date_from)}.`;
  } else if (syncActive) {
    headline = `синхронизация Ozon выполняется${sync?.total_days ? `: ${sync.progress || 0} из ${sync.total_days} дней` : ''}.`;
  } else {
    const parts = [];
    if (missingDates.length > 0) parts.push(`без учёта ${missingDates.length} дн. без данных Ozon`);
    if (costPercent != null) parts.push(`себестоимость известна для ${costPercent}% выручки`);
    headline = parts.length ? `${parts.join('; ')}.` : '';
  }
  const incomplete = missingDates.length > 0 || !configured || connection?.status === 'error' || backfill?.status === 'failed';
  const label = incomplete ? 'Ozon — данные неполные.' : 'Ozon — расчётная оценка.';

  return (
    <div className="marketplace-state-stack" aria-live="polite" data-testid="marketplace-notices">
      {error && (
        <div className="marketplace-notice marketplace-notice-error" role="alert" data-testid="marketplace-load-error">
          {error}
        </div>
      )}
      {(headline || overview) && (
        <div className="marketplace-notice marketplace-notice-warning" data-testid="marketplace-summary-notice">
          <strong>{label}</strong> {sentenceCase(headline)}
          <details className="marketplace-warning-overflow">
            <summary>Подробнее</summary>
            {connection?.status === 'error' && connection?.error && (
              <div data-testid="marketplace-connection-error">Ошибка подключения Ozon: {connection.error}</div>
            )}
            {backfillActive && (
              <div data-testid="marketplace-backfill-running">
                Автодогрузка с {formatDate(backfill.date_from)} по {formatDate(backfill.through_date)}
                {backfill.last_completed_date ? `; готово до ${formatDate(backfill.last_completed_date)}` : ''}.
              </div>
            )}
            {syncActive && (
              <div data-testid="marketplace-sync-running">
                Синхронизация Ozon выполняется{sync?.total_days ? `: ${sync.progress || 0} из ${sync.total_days} дней` : ''}.
              </div>
            )}
            {missingDates.length > 0 && (
              <div className="marketplace-missing-dates" data-testid="marketplace-partial-coverage">
                Пропущенные дни: {missingDates.slice(0, 8).map(formatDate).join(', ')}{missingDates.length > 8 ? ` и ещё ${missingDates.length - 8}` : ''}.
                Их суммы показаны прочерком; остальные дни учтены.
              </div>
            )}
            {costPercent != null && (
              <div>Себестоимость известна для {costPercent}% выручки периода; по остальным товарам себестоимость не внесена.</div>
            )}
            {warnings.length > 0 && (
              <div data-testid="marketplace-warnings">
                <strong>Замечания к данным ({warnings.length})</strong>
                <ul>{warnings.map((warning, index) => <li key={`${warningText(warning)}-${index}`}>{warningText(warning)}</li>)}</ul>
              </div>
            )}
            {overview?.accounting_basis && (
              <div>
                Расчётная прибыль — управленческая оценка, а не бухгалтерская сверка или налоговая декларация. Налог оценён от выручки по ставке из настроек WB.
              </div>
            )}
            <button className="btn-outline marketplace-notice-action" onClick={refreshAll} disabled={refreshing} data-testid="marketplace-refresh-button">
              {refreshing ? 'Обновляем…' : 'Перечитать статус'}
            </button>
          </details>
        </div>
      )}
    </div>
  );
}

export function WbOnlyNotice({ feature }) {
  return (
    <div className="marketplace-screen" data-testid="wb-only-notice">
      <div className="marketplace-notice marketplace-notice-info">
        <strong>{feature}</strong> сейчас доступна только для Wildberries. Выберите WB в переключателе маркетплейса, чтобы открыть эти данные.
      </div>
    </div>
  );
}
