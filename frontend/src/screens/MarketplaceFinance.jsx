/* eslint react-hooks/set-state-in-effect: off -- marketplace state resets when owner, selection, or returned products change. */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import * as api from '../api';
import DataTable from '../components/DataTable';
import KpiCard from '../components/KpiCard';
import { useStore } from '../StoreContext';

const MONEY_FIELDS = [
  ['revenue', 'Выручка'],
  ['commission', 'Комиссия'],
  ['acquiring', 'Эквайринг'],
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
  ['acquiring', 'Эквайринг'],
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

const activeMarketplaceFinanceContext = { current: null };

function isAmount(value) {
  return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
}

function aggregateRows(rows, incomplete, emptyAsZero = true, sourceMarketplace = '') {
  const list = Array.isArray(rows) ? rows : [];
  const totals = {};
  for (const [key] of MONEY_FIELDS) {
    const known = (row) => {
      if (key === 'margin' && sourceMarketplace !== 'wb' && row?.profitability_complete === false) return null;
      return row?.[key];
    };
    if (incomplete || (!list.length && !emptyAsZero) || list.some((row) => !isAmount(known(row)))) {
      totals[key] = null;
    } else {
      totals[key] = list.reduce((sum, row) => sum + Number(known(row)), 0);
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

function profitLabel(marketplace, suffix = '') {
  const label = marketplace === 'all' || marketplace === 'wb' ? 'Предварительная прибыль' : 'Расчётная прибыль';
  return suffix ? `${label} ${suffix}` : label;
}

function profitBasisNotice(marketplace, accountingBasis) {
  const notes = [];
  if (accountingBasis?.costs === 'current_sku_cost_restates_all_dates') {
    notes.push('текущая себестоимость товара применяется ко всем датам');
  }
  if (accountingBasis?.tax === 'estimated_revenue_times_wb_rate') {
    notes.push('налог оценён от выручки по ставке из настроек WB');
  }
  if (marketplace === 'all' && accountingBasis?.wb_fully_reconciled === false) {
    notes.push('финансовые данные WB в общем отчёте сверены не полностью');
  }
  const basis = notes.length ? `${notes.join('; ')}.` : 'часть исходных данных может быть неполной.';
  return `${marketplace === 'all' ? 'Предварительная прибыль' : 'Расчётная прибыль'}: ${basis} Это управленческая оценка, а не бухгалтерская сверка или налоговая декларация.`;
}

function warningText(warning) {
  if (typeof warning === 'string') return warning;
  if (warning && typeof warning === 'object') {
    return warning.message || warning.detail || warning.description || warning.code || 'Есть предупреждение по данным';
  }
  return String(warning ?? 'Есть предупреждение по данным');
}

function unknownReason(key, { incomplete = false, warnings = [], marketplace = 'ozon' } = {}) {
  if (incomplete) return 'Период загружен не полностью.';
  if (key === 'acquiring' && marketplace === 'all') return 'В общем режиме отдельная сумма эквайринга неизвестна: Wildberries включает её в комиссию.';
  if (key === 'acquiring' && marketplace === 'wb') return 'Отдельная сумма эквайринга не выделяется в сохранённой витрине Wildberries.';
  if (['sold_quantity', 'returned_quantity', 'net_quantity'].includes(key) && marketplace === 'all') return 'Количество для двух маркетплейсов нельзя сопоставить в общем режиме.';
  if (['sold_quantity', 'returned_quantity', 'net_quantity'].includes(key) && marketplace === 'wb') return 'Количество не выделяется в сохранённой финансовой витрине Wildberries.';
  if (['sold_quantity', 'returned_quantity', 'net_quantity'].includes(key)) return 'Количество не подтверждено источником за этот день.';
  if (key === 'cogs' || key === 'margin') return 'Для расчёта нужны подтверждённые проданные количества и себестоимость.';
  if (key === 'operation_expenses') return 'Сумма за день не внесена, а полнота общих расходов не подтверждена в настройках Ozon.';
  if (key === 'other_expenses' || key === 'unallocated_amount') return 'Некоторые начисления Ozon пока не сопоставлены со справочником видов начислений.';
  if (warnings.length) return warningText(warnings[0]);
  return 'Источник данных не предоставил это значение.';
}

function formatQuantity(value) {
  if (!isAmount(value)) return '—';
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 3 }).format(Number(value));
}

function quantityReason(row, field, marketplace = 'ozon') {
  if (row?.[field] !== null && row?.[field] !== undefined) return '';
  if (marketplace === 'wb') return 'Количество не выделяется в сохранённой финансовой витрине Wildberries.';
  if (row?.profitability_complete === false || row?.sku_allocation_complete === false) {
    return 'Не все начисления и товарные количества позволяют подтвердить этот итог.';
  }
  return 'Количество не подтверждено источником за этот день.';
}

function amountForRow(row, key, sourceMarketplace = '') {
  if (key === 'margin' && sourceMarketplace !== 'wb' && row?.profitability_complete === false) return null;
  return row?.[key];
}

function qualityWarnings(data) {
  const rows = [
    ...(Array.isArray(data?.daily) ? data.daily : []),
    ...(Array.isArray(data?.products) ? data.products.flatMap((product) => Array.isArray(product.daily) ? product.daily : []) : []),
  ];
  const warnings = [];
  if (rows.some((row) => row.accrual_classification_complete === false)) {
    warnings.push('Есть начисления, вид которых пока нельзя подтвердить по справочнику.');
  }
  if (rows.some((row) => row.sku_allocation_complete === false)) {
    warnings.push('Часть сумм не распределена по товарам, поэтому товарная прибыль неполная.');
  }
  return warnings;
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

function MetricSummary({ totals, currency, incomplete, warnings, marketplace }) {
  const revenue = totals.revenue;
  return (
    <DataTable title="Итоги за период" tag="Доли рассчитаны от общей выручки">
      <table data-testid="marketplace-metric-summary">
        <thead><tr><th className="left">Показатель</th><th>Сумма</th><th>Доля выручки</th></tr></thead>
        <tbody>
          {MONEY_FIELDS.map(([key, label]) => {
            const value = totals[key];
            const share = !(marketplace === 'all' && key === 'acquiring') && isAmount(value) && isAmount(revenue) && Number(revenue) !== 0
              ? Number(value) / Number(revenue) * 100
              : null;
            const displayLabel = key === 'margin'
              ? profitLabel(marketplace)
              : marketplace === 'all' && key === 'acquiring' ? 'Эквайринг (не сопоставляется)' : label;
            return (
              <tr key={key} data-testid={`marketplace-metric-${key}`}>
                <td className="left">{displayLabel}</td>
                <td><MoneyValue value={value} currency={currency} reason={unknownReason(key, { incomplete, warnings, marketplace })} /></td>
                <td>{formatPercent(share)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </DataTable>
  );
}

function DailyTable({ rows, currency, incomplete, warnings, marketplace }) {
  if (!rows.length) {
    return <div className="marketplace-empty" data-testid="marketplace-daily-empty">Нет дневных строк за выбранный период.</div>;
  }
  return (
    <DataTable title="Динамика по дням" tag={`${rows.length} дн.`} expandable>
      <table className="marketplace-wide-table" data-testid="marketplace-daily-table">
        <thead><tr><th className="left">День</th><th>Эквайринг</th><th>Продано, шт.</th><th>Возвраты, шт.</th><th>Нетто, шт.</th>{DAILY_COLUMNS.filter(([key]) => key !== 'acquiring').map(([key, label]) => <th key={key}>{key === 'margin' ? profitLabel(marketplace) : label}</th>)}</tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.date} data-testid={`marketplace-day-${row.date}`}>
              <td className="left">{formatDate(row.date)}</td>
              <td><MoneyValue value={row.acquiring} currency={currency} reason={unknownReason('acquiring', { incomplete, warnings, marketplace })} /></td>
              {['sold_quantity', 'returned_quantity', 'net_quantity'].map((key) => <td key={key}><span title={isAmount(row[key]) ? undefined : unknownReason(key, { incomplete, warnings, marketplace })}>{formatQuantity(row[key])}</span></td>)}
              {DAILY_COLUMNS.filter(([key]) => key !== 'acquiring').map(([key]) => <td key={key}><MoneyValue value={amountForRow(row, key, marketplace)} currency={currency} reason={unknownReason(key, { incomplete, warnings, marketplace })} /></td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {marketplace === 'all' && <div className="marketplace-settings-note">В общем режиме отдельная сумма эквайринга неизвестна, потому что Wildberries включает её в комиссию. Количества продаж разных площадок нельзя сопоставить в одной строке.</div>}
    </DataTable>
  );
}

function datesBetween(dateFrom, dateTo) {
  if (!dateFrom || !dateTo) return [];
  const current = new Date(`${dateFrom}T00:00:00Z`);
  const end = new Date(`${dateTo}T00:00:00Z`);
  if (!Number.isFinite(current.getTime()) || !Number.isFinite(end.getTime()) || end < current) return [];
  const dates = [];
  while (current <= end) {
    dates.push(current.toISOString().slice(0, 10));
    current.setUTCDate(current.getUTCDate() + 1);
  }
  return dates;
}

function MarketplaceOperatingExpenses({ rows, dateFrom, dateTo, currency, marketplace, canManage, onSave }) {
  const [drafts, setDrafts] = useState({});
  const [errors, setErrors] = useState({});
  const [messages, setMessages] = useState({});
  const [savingDate, setSavingDate] = useState('');
  const rowsByDate = useMemo(() => new Map(rows.map((row) => [row.date, row])), [rows]);
  const dates = useMemo(() => datesBetween(dateFrom, dateTo), [dateFrom, dateTo]);
  const canEdit = marketplace === 'ozon' && canManage;

  useEffect(() => {
    setDrafts({});
    setErrors({});
    setMessages({});
  }, [rows, dateFrom, dateTo, marketplace]);

  const saveDate = async (date, amountText) => {
    if (!canEdit || savingDate) return;
    const normalized = String(amountText ?? '').trim().replace(',', '.');
    const amount = Number(normalized);
    if (!normalized || !Number.isFinite(amount) || amount < 0) {
      setErrors((current) => ({ ...current, [date]: 'Введите сумму от 0 ₽. Ноль означает, что за день расходов не было.' }));
      return;
    }

    setSavingDate(date);
    setErrors((current) => ({ ...current, [date]: '' }));
    setMessages((current) => ({ ...current, [date]: '' }));
    try {
      await onSave(date, amount);
      setDrafts((current) => {
        const next = { ...current };
        delete next[date];
        return next;
      });
      setMessages((current) => ({ ...current, [date]: 'Сумма сохранена.' }));
    } catch (error) {
      setErrors((current) => ({ ...current, [date]: error?.message || 'Не удалось сохранить сумму.' }));
    } finally {
      setSavingDate('');
    }
  };

  return (
    <>
      {marketplace === 'all' ? (
        <div className="marketplace-notice marketplace-notice-info" data-testid="marketplace-expenses-all-readonly">
          Здесь показаны общие расходы из обоих маркетплейсов. Для изменения расходов Ozon переключитесь на Ozon отдельно.
        </div>
      ) : canManage ? (
        <div className="marketplace-notice marketplace-notice-info">
          Внесите расходы по дням. Явный ввод 0 ₽ означает нулевые расходы за день. Дни без суммы остаются неизвестными, пока владелец не подтвердит полноту расходов в настройках Ozon.
        </div>
      ) : (
        <div className="marketplace-notice marketplace-notice-info" data-testid="marketplace-expenses-viewer-readonly">
          Расходы доступны только для просмотра; изменять суммы может владелец магазина.
        </div>
      )}
      <DataTable title={marketplace === 'all' ? 'Общие расходы по дням' : 'Расходы Ozon по дням'} tag={`${dates.length} дн.`}>
        <div className="table-wrap marketplace-expense-table-wrap">
          <table className="marketplace-expense-table" data-testid="marketplace-expenses-table">
            <thead><tr><th className="left">День</th><th>Общие расходы</th>{canEdit && <th>Внести или изменить</th>}</tr></thead>
            <tbody>
              {dates.map((date) => {
                const existingAmount = rowsByDate.get(date)?.operation_expenses;
                const existingText = isAmount(existingAmount) ? String(existingAmount) : '';
                const amountText = drafts[date] === undefined ? existingText : drafts[date];
                const normalizedText = amountText.trim().replace(',', '.');
                const parsedAmount = normalizedText === '' ? null : Number(normalizedText);
                const changed = parsedAmount !== null && Number.isFinite(parsedAmount)
                  && (!isAmount(existingAmount) || Number(existingAmount) !== parsedAmount);
                return (
                  <tr key={date} data-testid={`marketplace-expense-day-${date}`}>
                    <td className="left">{formatDate(date)}</td>
                    <td><MoneyValue value={existingAmount} currency={currency} reason={unknownReason('operation_expenses')} /></td>
                    {canEdit && (
                      <td>
                        <div className="marketplace-expense-editor">
                          <input
                            type="text"
                            inputMode="decimal"
                            aria-label={`Общие расходы Ozon за ${formatDate(date)}`}
                            value={amountText}
                            placeholder="Не внесены"
                            onChange={(event) => {
                              setDrafts((current) => ({ ...current, [date]: event.target.value }));
                              setErrors((current) => ({ ...current, [date]: '' }));
                              setMessages((current) => ({ ...current, [date]: '' }));
                            }}
                            disabled={savingDate !== '' && savingDate !== date}
                            data-testid={`marketplace-expense-input-${date}`}
                          />
                          <button className="btn-outline" onClick={() => saveDate(date, amountText)} disabled={savingDate === date || !changed}>
                            {savingDate === date ? 'Сохраняем…' : 'Сохранить'}
                          </button>
                          {errors[date] && <span className="marketplace-cost-error" role="alert">{errors[date]}</span>}
                          {messages[date] && <span className="marketplace-cost-success" role="status">{messages[date]}</span>}
                        </div>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </DataTable>
    </>
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
          <thead><tr><th className="left">День</th><th>Продано, шт.</th><th>Возвраты, шт.</th><th>Нетто, шт.</th><th>Выручка</th><th>Комиссия</th><th>Эквайринг</th><th>Логистика</th><th>Штрафы</th><th>Хранение</th><th>Реклама</th><th>Прочие</th><th>Компенсации</th><th>Себестоимость</th><th>Налог</th><th>{profitLabel(product.marketplace, 'до общих расходов')}</th><th>Итого начислено</th></tr></thead>
          <tbody>{daily.map((row) => (
            <tr key={row.date}>
              <td className="left">{formatDate(row.date)}</td>
              {['sold_quantity', 'returned_quantity', 'net_quantity'].map((key) => <td key={key}><span title={isAmount(row[key]) ? undefined : quantityReason(row, key, product.marketplace)}>{formatQuantity(row[key])}</span></td>)}
              {['revenue', 'commission', 'acquiring', 'logistics', 'penalties', 'storage', 'ads_spend', 'other_expenses', 'compensation', 'cogs', 'tax', 'margin', 'marketplace_net'].map((key) => (
                <td key={key}><MoneyValue value={amountForRow(row, key, product.marketplace)} currency={currency} reason={unknownReason(key, { incomplete, warnings, marketplace: product.marketplace })} /></td>
              ))}
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}

function ProductsTable({ products, currency, incomplete, warnings = [], showCosts = false, editableCosts = false, marketplace = 'ozon', stateKey = '', onCostSaved }) {
  const [expanded, setExpanded] = useState(() => new Set());
  const [drafts, setDrafts] = useState({});
  const [savedCostOverrides, setSavedCostOverrides] = useState({});
  const [savingKey, setSavingKey] = useState('');
  const [recalculatingKey, setRecalculatingKey] = useState('');
  const [saveErrors, setSaveErrors] = useState({});
  const [saveMessages, setSaveMessages] = useState({});
  const [refreshErrors, setRefreshErrors] = useState({});
  const isCurrentContext = () => activeMarketplaceFinanceContext.current === stateKey;
  const productsWithTotals = useMemo(() => (Array.isArray(products) ? products : []).map((product) => ({
    ...product,
    identity: `${String(product.marketplace || 'unknown')}:${String(product.sku)}`,
    totals: aggregateRows(product.daily, incomplete, false, product.marketplace),
    quantities: Object.fromEntries(['sold_quantity', 'returned_quantity', 'net_quantity'].map((key) => [
      key,
      Array.isArray(product.daily) && product.daily.length > 0 && product.daily.every((row) => isAmount(row?.[key]))
        ? product.daily.reduce((sum, row) => sum + Number(row[key]), 0)
        : null,
    ])),
  })), [products, incomplete]);

  useEffect(() => {
    setExpanded(new Set());
    setDrafts({});
    setSavedCostOverrides({});
    setSavingKey('');
    setRecalculatingKey('');
    setSaveErrors({});
    setSaveMessages({});
    setRefreshErrors({});
  }, [stateKey]);

  const toggle = (identity) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(identity)) next.delete(identity);
      else next.add(identity);
      return next;
    });
  };

  const setDraft = (identity, value) => setDrafts((prev) => ({ ...prev, [identity]: value }));

  const refreshSavedCost = async (product) => {
    if (!isCurrentContext()) return false;
    setRecalculatingKey(product.identity);
    try {
      const refreshed = await onCostSaved?.();
      if (!isCurrentContext()) return false;
      if (refreshed === false) {
        setRefreshErrors((prev) => ({
          ...prev,
          [product.identity]: 'Себестоимость уже сохранена, но новые суммы пока не загрузились.',
        }));
        return false;
      }
      setSavedCostOverrides((prev) => {
        const next = { ...prev };
        delete next[product.identity];
        return next;
      });
      setRefreshErrors((prev) => ({ ...prev, [product.identity]: '' }));
      return true;
    } catch {
      if (!isCurrentContext()) return false;
      setRefreshErrors((prev) => ({
        ...prev,
        [product.identity]: 'Себестоимость уже сохранена, но новые суммы пока не загрузились.',
      }));
      return false;
    } finally {
      if (isCurrentContext()) setRecalculatingKey('');
    }
  };

  const saveCost = async (product) => {
    if (!isCurrentContext()) return;
    const valueText = String(drafts[product.identity] ?? (product.cost_price == null ? '' : product.cost_price)).trim();
    const parsed = valueText === '' ? null : Number(valueText.replace(',', '.'));
    if (parsed !== null && (!Number.isFinite(parsed) || parsed < 0)) {
      setSaveErrors((prev) => ({ ...prev, [product.identity]: 'Введите число от 0 или оставьте поле пустым, чтобы убрать себестоимость.' }));
      return;
    }
    setSavingKey(product.identity);
    setSaveErrors((prev) => ({ ...prev, [product.identity]: '' }));
    setRefreshErrors((prev) => ({ ...prev, [product.identity]: '' }));
    try {
      const result = await api.saveMarketplaceProductCost(product.sku, parsed);
      if (!isCurrentContext()) return;
      const savedCostPrice = result && Object.prototype.hasOwnProperty.call(result, 'cost_price')
        ? result.cost_price
        : parsed;
      setSavedCostOverrides((prev) => ({ ...prev, [product.identity]: savedCostPrice }));
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[product.identity];
        return next;
      });
      setSaveMessages((prev) => ({ ...prev, [product.identity]: result?.message || 'Себестоимость сохранена.' }));
      await refreshSavedCost(product);
    } catch (error) {
      if (!isCurrentContext()) return;
      setSaveErrors((prev) => ({ ...prev, [product.identity]: error?.message || 'Не удалось сохранить себестоимость.' }));
      setSaveMessages((prev) => ({ ...prev, [product.identity]: '' }));
    } finally {
      if (isCurrentContext()) setSavingKey('');
    }
  };

  if (!productsWithTotals.length) {
    return <div className="marketplace-empty" data-testid="marketplace-products-empty">За выбранный период товары не найдены.</div>;
  }

  return (
    <>
    {marketplace === 'all' && <div className="marketplace-notice marketplace-notice-info">Общее количество по площадкам не суммируется; в товарных строках показаны данные своей площадки.</div>}
    <DataTable title="Товары и начисления" tag={`${productsWithTotals.length} товаров`}>
      <table className="marketplace-products-table" data-testid="marketplace-products-table">
        <thead>
          <tr>
            <th className="left">Товар</th><th className="left">SKU / артикул</th>
            <th>Продано, шт.</th><th>Возвраты, шт.</th><th>Нетто, шт.</th><th>Выручка</th><th>Себестоимость</th><th>{profitLabel(marketplace, 'до общих расходов')}</th><th>Итого начислено</th>
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
              savedCostPrice={savedCostOverrides[product.identity]}
              onDraft={(value) => setDraft(product.identity, value)}
              onSave={() => saveCost(product)}
              onRetryRecalculation={() => refreshSavedCost(product)}
              saving={savingKey === product.identity}
              anySaving={Boolean(savingKey)}
              recalculating={recalculatingKey === product.identity}
              error={saveErrors[product.identity]}
              success={saveMessages[product.identity]}
              refreshError={refreshErrors[product.identity]}
              incomplete={incomplete}
              warnings={warnings}
            />
          ))}
        </tbody>
      </table>
    </DataTable>
    </>
  );
}

function ProductRow({ product, currency, showCosts, editableCosts, expanded, onToggle, draft, savedCostPrice, onDraft, onSave, onRetryRecalculation, saving, anySaving, recalculating, error, success, refreshError, incomplete, warnings }) {
  const currentCostPrice = savedCostPrice === undefined ? product.cost_price : savedCostPrice;
  const value = (draft === undefined) ? (currentCostPrice == null ? '' : String(currentCostPrice)) : draft;
  const current = currentCostPrice == null ? '' : String(currentCostPrice);
  const dirty = value !== current;
  const colspan = showCosts ? 11 : 10;
  return (
    <>
      <tr data-testid={`marketplace-product-${product.identity}`}>
        <td className="left">
          <div className="marketplace-product-name">{product.name || 'Без названия'}</div>
          <span className="tag tag-gray marketplace-product-market">{marketplaceName(product.marketplace)}</span>
          {product.offer_id && <div className="marketplace-offer-id">Offer ID: {product.offer_id}</div>}
        </td>
        <td className="left">{String(product.sku)}</td>
        {['sold_quantity', 'returned_quantity', 'net_quantity'].map((key) => <td key={key}><span title={isAmount(product.quantities[key]) ? undefined : quantityReason(product.daily?.at(-1), key, product.marketplace)}>{formatQuantity(product.quantities[key])}</span></td>)}
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
                  disabled={saving}
                  onChange={(event) => onDraft(event.target.value)}
                  data-testid={`marketplace-cost-input-${product.identity}`}
                />
                <button className="btn-outline" disabled={anySaving || !dirty} onClick={onSave}>
                  {saving ? 'Сохраняем…' : 'Сохранить'}
                </button>
                {error && <span className="marketplace-cost-error" role="alert">{error}</span>}
                {success && <span className="marketplace-cost-success" role="status">{success}</span>}
                {refreshError && (
                  <span className="marketplace-cost-error" role="alert">
                    {refreshError} <button className="btn-outline" disabled={recalculating} onClick={onRetryRecalculation}>{recalculating ? 'Обновляем…' : 'Повторить обновление'}</button>
                  </span>
                )}
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

  const dateFrom = range?.dateFrom;
  const dateTo = range?.dateTo;
  const requestKey = `${activeStoreOwnerId}|${marketplace}|${range?.dateFrom}|${range?.dateTo}`;
  const stateKey = `${requestKey}|${view}`;
  useLayoutEffect(() => {
    activeMarketplaceFinanceContext.current = stateKey;
    return () => {
      if (activeMarketplaceFinanceContext.current === stateKey) activeMarketplaceFinanceContext.current = null;
    };
  }, [stateKey]);
  const incomplete = showPartialReason(data);
  const totals = useMemo(() => aggregateRows(data?.daily, incomplete, true, marketplace), [data?.daily, incomplete, marketplace]);
  const daily = Array.isArray(data?.daily) ? data.daily : [];
  const products = Array.isArray(data?.products) ? data.products : [];
  const warnings = useMemo(() => [...(Array.isArray(data?.warnings) ? data.warnings : []), ...qualityWarnings(data)], [data]);
  const refresh = useCallback(() => setReloadVersion((current) => current + 1), []);
  const refreshAfterCostSave = useCallback(async () => {
    if (!dateFrom || !dateTo || activeMarketplaceFinanceContext.current !== stateKey) return false;
    const generation = ++requestGeneration.current;
    const controller = new AbortController();
    try {
      const updated = await api.getMarketplaceFinance(marketplace, dateFrom, dateTo, { signal: controller.signal });
      if (generation !== requestGeneration.current || activeMarketplaceFinanceContext.current !== stateKey) return false;
      setData(updated);
      setError('');
      return true;
    } catch {
      if (generation !== requestGeneration.current || controller.signal.aborted || activeMarketplaceFinanceContext.current !== stateKey) return false;
      return false;
    } finally {
      if (generation === requestGeneration.current && activeMarketplaceFinanceContext.current === stateKey) setLoading(false);
    }
  // Keep owner changes explicit here; stateKey is also owner-scoped but this callback refreshes owner data.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeStoreOwnerId, marketplace, dateFrom, dateTo, stateKey]);

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
  const viewLabel = view === 'articles' ? 'Артикулы' : view === 'costs' ? 'Себестоимость' : view === 'expenses' ? 'Общие расходы' : 'Финансы';
  const title = marketplace === 'all' ? `Все маркетплейсы · ${viewLabel}` : `Ozon · ${viewLabel}`;
  const showCostField = view === 'costs';
  const editableCosts = showCostField && marketplace === 'ozon' && canManage;
  const productsStateKey = stateKey;

  const saveOperatingExpense = async (date, amount) => {
    await api.saveMarketplaceDailyExpense(date, amount);
    refresh();
  };

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
        warnings={warnings}
        sync={data?.sync}
        onRefresh={refresh}
        refreshing={loading}
        marketplace={marketplace}
      />
      <div className="marketplace-proof-note" data-testid="marketplace-proof-note">
        Проверено на одном кабинете: данные Ozon за один период сверены с бухгалтерскими итогами. Неизвестные начисления и неполные данные отмечаются отдельно; сопоставление возвратов на живых данных ещё не проверено.
      </div>
      <div className="marketplace-notice marketplace-notice-info" data-testid="marketplace-profit-basis-note">
        {profitBasisNotice(marketplace, data?.accounting_basis)}
      </div>

      {view === 'dashboard' && (
        <>
          {incomplete && <div className="marketplace-kpi-grid" data-testid="marketplace-summary-kpis">
            <KpiCard label="Выручка за весь период" value="—" delta="Есть пропущенные дни" />
            <KpiCard label={`${profitLabel(marketplace)} за весь период`} value="—" delta="Есть пропущенные дни" valueClassName="neg" />
            <KpiCard label="Итого начислено за весь период" value="—" delta="Есть пропущенные дни" />
          </div>}
          {!incomplete && (
            <div className="marketplace-kpi-grid" data-testid="marketplace-summary-kpis">
              <KpiCard label="Выручка" value={formatMoney(totals.revenue, currency)} delta="Сумма по выбранным датам" />
              <KpiCard label={profitLabel(marketplace)} value={formatMoney(totals.margin, currency)} delta="После учтённых расходов" valueClassName={isAmount(totals.margin) && totals.margin < 0 ? 'neg' : 'pos'} />
              <KpiCard label="Итого начислено маркетплейсом" value={formatMoney(totals.marketplace_net, currency)} delta="Сверяется отдельно с прибылью" />
            </div>
          )}
          {daily.length > 0 || data?.coverage?.complete ? <MetricSummary totals={totals} currency={currency} incomplete={incomplete} warnings={warnings} marketplace={marketplace} /> : null}
          <DailyTable rows={daily} currency={currency} incomplete={incomplete} warnings={warnings} marketplace={marketplace} />
          <ProductsTable products={products} currency={currency} incomplete={incomplete} warnings={warnings} marketplace={marketplace} stateKey={productsStateKey} />
        </>
      )}

      {view === 'articles' && <ProductsTable products={products} currency={currency} incomplete={incomplete} warnings={warnings} marketplace={marketplace} stateKey={productsStateKey} />}

      {view === 'costs' && (
        <>
          {!canManage && <div className="marketplace-notice marketplace-notice-info" data-testid="marketplace-cost-viewer-readonly">Себестоимость доступна для просмотра. Изменять её может владелец магазина.</div>}
          {marketplace === 'all' && <div className="marketplace-notice marketplace-notice-info">В общем режиме себестоимость показана для просмотра. Чтобы изменить цену товара Ozon, выберите Ozon отдельно.</div>}
          {marketplace === 'ozon' && <div className="marketplace-notice marketplace-notice-info" data-testid="marketplace-cost-history-warning">Сохранённая себестоимость применяется ко всем датам. Если изменить цену товара, расчётная прибыль пересчитается за всю историю, а не только за выбранный период.</div>}
          <ProductsTable products={products} currency={currency} incomplete={incomplete} warnings={warnings} showCosts editableCosts={editableCosts} marketplace={marketplace} stateKey={productsStateKey} onCostSaved={refreshAfterCostSave} />
        </>
      )}
      {view === 'expenses' && (
        <MarketplaceOperatingExpenses
          rows={daily}
          dateFrom={range.dateFrom}
          dateTo={range.dateTo}
          currency={currency}
          marketplace={marketplace}
          canManage={canManage}
          onSave={saveOperatingExpense}
        />
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
