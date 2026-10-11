/* eslint react-hooks/set-state-in-effect: off -- marketplace state resets when owner, selection, or returned products change. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as api from '../api';
import DataTable from '../components/DataTable';
import KpiCard from '../components/KpiCard';
import { useStore } from '../StoreContext';

const MONEY_FIELDS = [
  ['revenue', 'Выручка'],
  ['commission', 'Комиссия'],
  ['logistics', 'Логистика'],
  ['penalties', 'Штрафы'],
  ['storage', 'Хранение'],
  ['ads_spend', 'Реклама'],
  ['other_expenses', 'Прочие начисления'],
  ['compensation', 'Компенсации'],
  ['cogs', 'Себестоимость'],
  ['tax', 'Налог'],
  ['operation_expenses', 'Операционные расходы'],
  ['margin', 'Прибыль'],
  ['marketplace_net', 'Итого начислено'],
  ['unallocated_amount', 'Без привязки к товару'],
  ['reconciliation_difference', 'Расхождение сверки'],
];

const DAILY_COLUMNS = [
  ['revenue', 'Выручка'],
  ['commission', 'Комиссия'],
  ['logistics', 'Логистика'],
  ['penalties', 'Штрафы'],
  ['storage', 'Хранение'],
  ['ads_spend', 'Реклама'],
  ['other_expenses', 'Прочие'],
  ['compensation', 'Компенсации'],
  ['cogs', 'Себестоимость'],
  ['tax', 'Налог'],
  ['operation_expenses', 'Опер. расходы'],
  ['margin', 'Прибыль'],
  ['marketplace_net', 'Итого начислено'],
  ['unallocated_amount', 'Без товара'],
  ['reconciliation_difference', 'Расхождение'],
];

function isAmount(value) {
  return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
}

function aggregateRows(rows, incomplete, emptyAsZero = true) {
  const list = Array.isArray(rows) ? rows : [];
  const totals = {};
  for (const [key] of MONEY_FIELDS) {
    if (incomplete || (!list.length && !emptyAsZero) || list.some((row) => !isAmount(row?.[key]))) {
      totals[key] = null;
    } else {
      totals[key] = list.reduce((sum, row) => sum + Number(row[key]), 0);
    }
  }
  return totals;
}

function formatMoney(value, currency = 'RUB') {
  if (!isAmount(value)) return '—';
  try {
    return new Intl.NumberFormat('ru-RU', {
      style: 'currency',
      currency: currency || 'RUB',
      maximumFractionDigits: 2,
    }).format(Number(value));
  } catch {
    return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(Number(value))} ${currency || ''}`.trim();
  }
}

function formatPercent(value) {
  if (!Number.isFinite(value)) return '—';
  return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value)}%`;
}

function formatDate(value) {
  if (!value) return '—';
  const parsed = new Date(`${value}T12:00:00`);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleDateString('ru-RU');
}

function marketplaceName(value) {
  return value === 'ozon' ? 'Ozon' : value === 'wb' ? 'Wildberries' : String(value || 'Маркетплейс');
}

function warningText(warning) {
  if (typeof warning === 'string') return warning;
  if (warning && typeof warning === 'object') {
    return warning.message || warning.detail || warning.description || warning.code || 'Есть предупреждение по данным';
  }
  return String(warning ?? 'Есть предупреждение по данным');
}

function unknownReason(key, { incomplete = false, warnings = [] } = {}) {
  if (incomplete) return 'Период загружен не полностью.';
  if (key === 'cogs' || key === 'margin') return 'Нет подтверждённого количества проданных единиц для расчёта себестоимости и прибыли.';
  if (key === 'other_expenses' || key === 'unallocated_amount') return 'Некоторые начисления Ozon пока не сопоставлены со справочником видов начислений.';
  if (warnings.length) return warningText(warnings[0]);
  return 'Источник данных не предоставил это значение.';
}

function showPartialReason(data) {
  const missing = Array.isArray(data?.coverage?.missing_dates) ? data.coverage.missing_dates : [];
  return data?.coverage?.complete === false || missing.length > 0;
}

function MarketplaceState({ connection, coverage, warnings, sync, onRefresh, refreshing, marketplace }) {
  const missingDates = Array.isArray(coverage?.missing_dates) ? coverage.missing_dates : [];
  const hasConnection = connection?.configured === true;
  const hasWarnings = Array.isArray(warnings) && warnings.length > 0;
  const currentSync = sync || connection?.sync;
  const syncActive = ['queued', 'pending', 'running'].includes(String(currentSync?.status || '').toLowerCase());

  return (
    <div className="marketplace-state-stack" aria-live="polite">
      {!hasConnection && (
        <div className="marketplace-notice marketplace-notice-info" data-testid="marketplace-unconfigured">
          {marketplace === 'all'
            ? 'Кабинет Ozon не подключён. В общем режиме доступны только данные подключённых маркетплейсов; итоги остаются неполными.'
            : 'Кабинет Ozon не подключён. Добавьте Client ID и API-ключ в разделе «Настройки».'}
        </div>
      )}
      {connection?.status === 'error' && connection?.error && (
        <div className="marketplace-notice marketplace-notice-error" data-testid="marketplace-connection-error">
          Ошибка подключения Ozon: {connection.error}
        </div>
      )}
      {syncActive && (
        <div className="marketplace-notice marketplace-notice-info" data-testid="marketplace-sync-running">
          Синхронизация Ozon выполняется{currentSync?.progress ? `: ${currentSync.progress}` : ''}. Данные обновятся после её завершения.
          {onRefresh && (
            <button className="btn-outline marketplace-notice-action" onClick={onRefresh} disabled={refreshing}>
              {refreshing ? 'Обновляем…' : 'Перечитать статус'}
            </button>
          )}
        </div>
      )}
      {showPartialReason({ coverage }) && (
        <div className="marketplace-notice marketplace-notice-warning" data-testid="marketplace-partial-coverage">
          <div><strong>Период загружен не полностью.</strong> Итоги за весь период неизвестны, поэтому суммы показаны прочерком.</div>
          {missingDates.length > 0 && (
            <div className="marketplace-missing-dates">
              Пропущенные дни: {missingDates.slice(0, 5).map(formatDate).join(', ')}{missingDates.length > 5 ? ` и ещё ${missingDates.length - 5}` : ''}
            </div>
          )}
        </div>
      )}
      {hasWarnings && (
        <div className="marketplace-notice marketplace-notice-warning" data-testid="marketplace-warnings">
          <strong>Замечания к данным</strong>
          <ul>{warnings.slice(0, 5).map((warning, index) => <li key={`${warningText(warning)}-${index}`}>{warningText(warning)}</li>)}</ul>
          {warnings.length > 5 && (
            <details className="marketplace-warning-overflow">
              <summary>Показать остальные замечания ({warnings.length - 5})</summary>
              <ul>{warnings.slice(5).map((warning, index) => <li key={`${warningText(warning)}-${index + 5}`}>{warningText(warning)}</li>)}</ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

function MoneyValue({ value, currency, className = '', reason = '' }) {
  if (!isAmount(value)) {
    const explanation = reason || 'Источник данных не предоставил это значение.';
    return <span className={`marketplace-unknown-value ${className}`.trim()} title={explanation} aria-label={`Неизвестно. ${explanation}`}>—</span>;
  }
  return <span className={`${className} ${Number(value) < 0 ? 'neg' : ''}`.trim()}>{formatMoney(value, currency)}</span>;
}

function MetricSummary({ totals, currency, incomplete, warnings }) {
  const revenue = totals.revenue;
  return (
    <DataTable title="Итоги за период" tag="Доли рассчитаны от общей выручки">
      <table data-testid="marketplace-metric-summary">
        <thead><tr><th className="left">Показатель</th><th>Сумма</th><th>Доля выручки</th></tr></thead>
        <tbody>
          {MONEY_FIELDS.map(([key, label]) => {
            const value = totals[key];
            const share = isAmount(value) && isAmount(revenue) && Number(revenue) !== 0
              ? Number(value) / Number(revenue) * 100
              : null;
            return (
              <tr key={key} data-testid={`marketplace-metric-${key}`}>
                <td className="left">{label}</td>
                <td><MoneyValue value={value} currency={currency} reason={unknownReason(key, { incomplete, warnings })} /></td>
                <td>{formatPercent(share)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </DataTable>
  );
}

function DailyTable({ rows, currency, incomplete, warnings }) {
  if (!rows.length) {
    return <div className="marketplace-empty" data-testid="marketplace-daily-empty">Нет дневных строк за выбранный период.</div>;
  }
  return (
    <DataTable title="Динамика по дням" tag={`${rows.length} дн.`}>
      <table className="marketplace-wide-table" data-testid="marketplace-daily-table">
        <thead><tr><th className="left">День</th>{DAILY_COLUMNS.map(([key, label]) => <th key={key}>{label}</th>)}</tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.date} data-testid={`marketplace-day-${row.date}`}>
              <td className="left">{formatDate(row.date)}</td>
              {DAILY_COLUMNS.map(([key]) => <td key={key}><MoneyValue value={row[key]} currency={currency} reason={unknownReason(key, { incomplete, warnings })} /></td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </DataTable>
  );
}

function ProductDailyDetails({ product, currency, incomplete, warnings }) {
  const daily = Array.isArray(product.daily) ? product.daily : [];
  if (!daily.length) return <div className="marketplace-empty">Дневной детализации по этому товару нет.</div>;
  return (
    <div className="marketplace-product-detail">
      <div className="marketplace-product-detail-title">Дневные начисления · {marketplaceName(product.marketplace)}</div>
      <div className="table-wrap marketplace-product-day-wrap">
        <table className="marketplace-product-days">
          <thead><tr><th className="left">День</th><th>Выручка</th><th>Комиссия</th><th>Логистика</th><th>Штрафы</th><th>Хранение</th><th>Реклама</th><th>Прочие</th><th>Компенсации</th><th>Себестоимость</th><th>Налог</th><th>Прибыль</th><th>Итого начислено</th></tr></thead>
          <tbody>{daily.map((row) => (
            <tr key={row.date}>
              <td className="left">{formatDate(row.date)}</td>
              {['revenue', 'commission', 'logistics', 'penalties', 'storage', 'ads_spend', 'other_expenses', 'compensation', 'cogs', 'tax', 'margin', 'marketplace_net'].map((key) => (
                <td key={key}><MoneyValue value={row[key]} currency={currency} reason={unknownReason(key, { incomplete, warnings })} /></td>
              ))}
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}

function ProductsTable({ products, currency, incomplete, warnings = [], showCosts = false, editableCosts = false }) {
  const [expanded, setExpanded] = useState(() => new Set());
  const [drafts, setDrafts] = useState({});
  const [savingKey, setSavingKey] = useState('');
  const [saveErrors, setSaveErrors] = useState({});
  const [saveMessages, setSaveMessages] = useState({});
  const productsWithTotals = useMemo(() => (Array.isArray(products) ? products : []).map((product) => ({
    ...product,
    identity: `${String(product.marketplace || 'unknown')}:${String(product.sku)}`,
    totals: aggregateRows(product.daily, incomplete, false),
  })), [products, incomplete]);

  useEffect(() => {
    setExpanded(new Set());
    setDrafts({});
    setSaveErrors({});
    setSaveMessages({});
  }, [products]);

  const toggle = (identity) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(identity)) next.delete(identity);
      else next.add(identity);
      return next;
    });
  };

  const setDraft = (identity, value) => setDrafts((prev) => ({ ...prev, [identity]: value }));

  const saveCost = async (product) => {
    const valueText = String(drafts[product.identity] ?? (product.cost_price == null ? '' : product.cost_price)).trim();
    const parsed = valueText === '' ? null : Number(valueText.replace(',', '.'));
    if (parsed !== null && (!Number.isFinite(parsed) || parsed < 0)) {
      setSaveErrors((prev) => ({ ...prev, [product.identity]: 'Введите число от 0 или оставьте поле пустым, чтобы убрать себестоимость.' }));
      return;
    }
    setSavingKey(product.identity);
    setSaveErrors((prev) => ({ ...prev, [product.identity]: '' }));
    try {
      const result = await api.saveMarketplaceProductCost(product.sku, parsed);
      product.cost_price = parsed;
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[product.identity];
        return next;
      });
      setSaveMessages((prev) => ({ ...prev, [product.identity]: result?.message || 'Себестоимость сохранена.' }));
    } catch (error) {
      setSaveErrors((prev) => ({ ...prev, [product.identity]: error?.message || 'Не удалось сохранить себестоимость.' }));
      setSaveMessages((prev) => ({ ...prev, [product.identity]: '' }));
    } finally {
      setSavingKey('');
    }
  };

  if (!productsWithTotals.length) {
    return <div className="marketplace-empty" data-testid="marketplace-products-empty">За выбранный период товары не найдены.</div>;
  }

  return (
    <DataTable title="Товары и начисления" tag={`${productsWithTotals.length} товаров`}>
      <table className="marketplace-products-table" data-testid="marketplace-products-table">
        <thead>
          <tr>
            <th className="left">Товар</th><th className="left">SKU / артикул</th>
            <th>Выручка</th><th>Себестоимость</th><th>Прибыль</th><th>Итого начислено</th>
            {showCosts && <th>Цена себестоимости</th>}
            <th aria-label="Детализация" />
          </tr>
        </thead>
        <tbody>
          {productsWithTotals.map((product) => (
            <ProductRow
              key={product.identity}
              product={product}
              currency={currency}
              showCosts={showCosts}
              editableCosts={editableCosts}
              expanded={expanded.has(product.identity)}
              onToggle={() => toggle(product.identity)}
              draft={drafts[product.identity]}
              onDraft={(value) => setDraft(product.identity, value)}
              onSave={() => saveCost(product)}
              saving={savingKey === product.identity}
              error={saveErrors[product.identity]}
              success={saveMessages[product.identity]}
              incomplete={incomplete}
              warnings={warnings}
            />
          ))}
        </tbody>
      </table>
    </DataTable>
  );
}

function ProductRow({ product, currency, showCosts, editableCosts, expanded, onToggle, draft, onDraft, onSave, saving, error, success, incomplete, warnings }) {
  const value = (draft === undefined) ? (product.cost_price == null ? '' : String(product.cost_price)) : draft;
  const current = product.cost_price == null ? '' : String(product.cost_price);
  const dirty = value !== current;
  const colspan = showCosts ? 8 : 7;
  return (
    <>
      <tr data-testid={`marketplace-product-${product.identity}`}>
        <td className="left">
          <div className="marketplace-product-name">{product.name || 'Без названия'}</div>
          <span className="tag tag-gray marketplace-product-market">{marketplaceName(product.marketplace)}</span>
          {product.offer_id && <div className="marketplace-offer-id">Offer ID: {product.offer_id}</div>}
        </td>
        <td className="left">{String(product.sku)}</td>
        <td><MoneyValue value={product.totals.revenue} currency={currency} reason={unknownReason('revenue', { incomplete, warnings })} /></td>
        <td><MoneyValue value={product.totals.cogs} currency={currency} reason={unknownReason('cogs', { incomplete, warnings })} /></td>
        <td><MoneyValue value={product.totals.margin} currency={currency} reason={unknownReason('margin', { incomplete, warnings })} /></td>
        <td><MoneyValue value={product.totals.marketplace_net} currency={currency} reason={unknownReason('marketplace_net', { incomplete, warnings })} /></td>
        {showCosts && (
          <td>
            {editableCosts ? (
              <div className="marketplace-cost-editor">
                <input
                  aria-label={`Себестоимость ${product.name || product.sku}`}
                  type="text"
                  inputMode="decimal"
                  placeholder="Не задана"
                  value={value}
                  onChange={(event) => onDraft(event.target.value)}
                  data-testid={`marketplace-cost-input-${product.identity}`}
                />
                <button className="btn-outline" disabled={saving || !dirty} onClick={onSave}>
                  {saving ? 'Сохраняем…' : 'Сохранить'}
                </button>
                {error && <span className="marketplace-cost-error" role="alert">{error}</span>}
                {success && <span className="marketplace-cost-success" role="status">{success}</span>}
              </div>
            ) : <MoneyValue value={product.cost_price} currency={currency} />}
          </td>
        )}
        <td>
          <button className="expand-btn" aria-expanded={expanded} onClick={onToggle} data-testid={`marketplace-product-toggle-${product.identity}`}>
            {expanded ? 'Скрыть' : 'Детали'}
          </button>
        </td>
      </tr>
      {expanded && <tr><td colSpan={colspan}><ProductDailyDetails product={product} currency={currency} incomplete={incomplete} warnings={warnings} /></td></tr>}
    </>
  );
}

function MarketplaceFinance({ view, marketplace, range, refreshTrigger }) {
  const { activeStoreOwnerId, activeStore, isOwnerStore } = useStore();
  const canManage = Boolean(activeStore && isOwnerStore);
  const [reloadVersion, setReloadVersion] = useState(0);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState('');
  const requestGeneration = useRef(0);

  const requestKey = `${activeStoreOwnerId}|${marketplace}|${range?.dateFrom}|${range?.dateTo}`;
  const incomplete = showPartialReason(data);
  const totals = useMemo(() => aggregateRows(data?.daily, incomplete), [data?.daily, incomplete]);
  const daily = Array.isArray(data?.daily) ? data.daily : [];
  const products = Array.isArray(data?.products) ? data.products : [];
  const refresh = useCallback(() => setReloadVersion((current) => current + 1), []);

  useEffect(() => {
    if (!range?.dateFrom || !range?.dateTo) return undefined;
    const controller = new AbortController();
    const generation = ++requestGeneration.current;
    setData(null);
    setError('');
    setLoading(true);
    setSyncMessage('');

    api.getMarketplaceFinance(marketplace, range.dateFrom, range.dateTo, { signal: controller.signal })
      .then((result) => {
        if (generation !== requestGeneration.current) return;
        setData(result);
      })
      .catch((requestError) => {
        if (generation !== requestGeneration.current || controller.signal.aborted) return;
        setError(requestError?.message || 'Не удалось загрузить финансовые данные.');
      })
      .finally(() => {
        if (generation === requestGeneration.current && !controller.signal.aborted) setLoading(false);
      });

    return () => {
      controller.abort();
      if (generation === requestGeneration.current) requestGeneration.current += 1;
    };
  }, [marketplace, range?.dateFrom, range?.dateTo, refreshTrigger, reloadVersion, requestKey]);

  useEffect(() => {
    const status = String(data?.sync?.status || '').toLowerCase();
    if (!['queued', 'pending', 'running'].includes(status)) return undefined;
    const timer = setInterval(() => setReloadVersion((current) => current + 1), 6000);
    return () => clearInterval(timer);
  }, [data?.sync?.status]);

  const syncAllowed = useMemo(() => {
    if (!range?.dateFrom || !range?.dateTo) return false;
    const from = Date.parse(`${range.dateFrom}T00:00:00Z`);
    const to = Date.parse(`${range.dateTo}T00:00:00Z`);
    if (!Number.isFinite(from) || !Number.isFinite(to) || to < from) return false;
    return Math.floor((to - from) / 86400000) + 1 <= 31;
  }, [range?.dateFrom, range?.dateTo]);

  const sync = async () => {
    if (!syncAllowed || syncing || !isOwnerStore) return;
    setSyncing(true);
    setSyncMessage('');
    try {
      const result = await api.syncMarketplaceFinance(range.dateFrom, range.dateTo);
      setSyncMessage(result?.status ? `Запрос принят: ${result.status}.` : 'Запрос на обновление принят.');
      refresh();
    } catch (syncError) {
      setSyncMessage(syncError?.message || 'Не удалось запустить синхронизацию.');
    } finally {
      setSyncing(false);
    }
  };

  const currency = data?.currency || 'RUB';
  const viewLabel = view === 'articles' ? 'Артикулы' : view === 'costs' ? 'Себестоимость' : 'Финансы';
  const title = marketplace === 'all' ? `Все маркетплейсы · ${viewLabel}` : `Ozon · ${viewLabel}`;
  const showCostField = view === 'costs';
  const editableCosts = showCostField && marketplace === 'ozon' && canManage;

  if (loading) {
    return <div className="loader-center" data-testid="marketplace-loading"><div className="loader-spinner" /><p>Загрузка данных {marketplace === 'all' ? 'маркетплейсов' : 'Ozon'}…</p></div>;
  }

  if (error) {
    return (
      <div className="marketplace-screen">
        <div className="marketplace-screen-heading"><h2>{title}</h2></div>
        <div className="marketplace-notice marketplace-notice-error" role="alert" data-testid="marketplace-load-error">
          Не удалось загрузить финансовые данные: {error}
          <button className="btn-outline marketplace-notice-action" onClick={refresh}>Повторить</button>
        </div>
      </div>
    );
  }

  return (
    <div className="marketplace-screen" data-testid={`marketplace-screen-${view}`}>
      <div className="marketplace-screen-heading">
        <div><h2>{title}</h2><p>{formatDate(range.dateFrom)} — {formatDate(range.dateTo)}</p></div>
        <div className="marketplace-screen-actions">
          {marketplace === 'ozon' && canManage && (
            <button className="btn-primary" onClick={sync} disabled={!syncAllowed || syncing} data-testid="marketplace-sync-button">
              {syncing ? 'Запускаем…' : 'Синхронизировать Ozon'}
            </button>
          )}
          <button className="btn-outline" onClick={refresh} data-testid="marketplace-refresh-button">Перечитать</button>
        </div>
      </div>
      {marketplace === 'ozon' && canManage && !syncAllowed && (
        <div className="marketplace-hint" data-testid="marketplace-sync-limit">Для ручной синхронизации выберите не более 31 дня и проверьте порядок дат.</div>
      )}
      {syncMessage && <div className="marketplace-notice marketplace-notice-info" role="status">{syncMessage}</div>}
      <MarketplaceState
        connection={data?.connection}
        coverage={data?.coverage}
        warnings={data?.warnings}
        sync={data?.sync}
        onRefresh={refresh}
        refreshing={loading}
        marketplace={marketplace}
      />
      <div className="marketplace-proof-note" data-testid="marketplace-proof-note">
        Данные Ozon пока не сверены с закрытым отчётом продавца. Некоторые начисления могут оставаться без уточнённой категории; без подтверждённого количества себестоимость и прибыль неизвестны.
      </div>

      {view === 'dashboard' && (
        <>
          {incomplete && <div className="marketplace-kpi-grid" data-testid="marketplace-summary-kpis">
            <KpiCard label="Выручка за весь период" value="—" delta="Есть пропущенные дни" />
            <KpiCard label="Прибыль за весь период" value="—" delta="Есть пропущенные дни" valueClassName="neg" />
            <KpiCard label="Итого начислено за весь период" value="—" delta="Есть пропущенные дни" />
          </div>}
          {!incomplete && (
            <div className="marketplace-kpi-grid" data-testid="marketplace-summary-kpis">
              <KpiCard label="Выручка" value={formatMoney(totals.revenue, currency)} delta="Сумма по выбранным датам" />
              <KpiCard label="Прибыль" value={formatMoney(totals.margin, currency)} delta="После учтённых расходов" valueClassName={isAmount(totals.margin) && totals.margin < 0 ? 'neg' : 'pos'} />
              <KpiCard label="Итого начислено маркетплейсом" value={formatMoney(totals.marketplace_net, currency)} delta="Сверяется отдельно с прибылью" />
            </div>
          )}
          {daily.length > 0 || data?.coverage?.complete ? <MetricSummary totals={totals} currency={currency} incomplete={incomplete} warnings={data?.warnings || []} /> : null}
          <DailyTable rows={daily} currency={currency} incomplete={incomplete} warnings={data?.warnings || []} />
          <ProductsTable products={products} currency={currency} incomplete={incomplete} warnings={data?.warnings || []} />
        </>
      )}

      {view === 'articles' && <ProductsTable products={products} currency={currency} incomplete={incomplete} warnings={data?.warnings || []} />}

      {view === 'costs' && (
        <>
          {!canManage && <div className="marketplace-notice marketplace-notice-info" data-testid="marketplace-cost-viewer-readonly">Себестоимость доступна для просмотра. Изменять её может владелец магазина.</div>}
          {marketplace === 'all' && <div className="marketplace-notice marketplace-notice-info">В общем режиме себестоимость показана для просмотра. Чтобы изменить цену товара Ozon, выберите Ozon отдельно.</div>}
          <ProductsTable products={products} currency={currency} incomplete={incomplete} warnings={data?.warnings || []} showCosts editableCosts={editableCosts} />
        </>
      )}
      {syncMessage && <div className="marketplace-hint" role="status">{syncMessage}</div>}
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

export default MarketplaceFinance;
