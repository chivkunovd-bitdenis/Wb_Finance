const dayPattern = [0.93, 1.04, 0.98, 1.12, 1.08, 0.89, 1.02];

function isoDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function datesInRange(dateFrom, dateTo) {
  if (!dateFrom || !dateTo) return [];
  const current = new Date(`${dateFrom}T12:00:00`);
  const end = new Date(`${dateTo}T12:00:00`);
  if (Number.isNaN(current.getTime()) || Number.isNaN(end.getTime()) || end < current) return [];
  const dates = [];
  while (current <= end && dates.length < 93) {
    dates.push(isoDate(current));
    current.setDate(current.getDate() + 1);
  }
  return dates;
}

function makeWbRows(dateFrom, dateTo) {
  return datesInRange(dateFrom, dateTo).map((date, index) => {
    const revenue = Math.round((47800 + (index % 5) * 1860) * dayPattern[index % dayPattern.length]);
    const commission = Math.round(revenue * 0.175);
    const logistics = Math.round(revenue * 0.062);
    const penalties = index % 5 === 2 ? 430 : 0;
    const storage = Math.round(revenue * 0.019);
    const adsSpend = Math.round(revenue * 0.043);
    const cogs = Math.round(revenue * 0.29);
    const tax = Math.round(revenue * 0.06);
    const operationExpenses = index % 3 === 0 ? 1250 : 980;
    const margin = revenue - commission - logistics - penalties - storage - adsSpend - cogs - tax - operationExpenses;

    return {
      date,
      revenue,
      commission,
      commission_pct: 17.5,
      logistics,
      logistics_pct: 6.2,
      penalties,
      cogs,
      cogs_share: 29,
      tax,
      ads_spend: adsSpend,
      ads_pct: 4.3,
      storage,
      storage_pct: 1.9,
      wb_expenses_share: 26.0,
      operation_expenses: operationExpenses,
      margin,
      margin_pct: revenue ? (margin / revenue) * 100 : 0,
      roi: cogs ? (margin / cogs) * 100 : 0,
    };
  });
}

function makeOzonRows(dateFrom, dateTo, marketplace) {
  const wbRows = makeWbRows(dateFrom, dateTo);
  return wbRows.map((row, index) => {
    const revenue = Math.round(row.revenue * (marketplace === 'all' ? 1.56 : 1.08));
    const commission = Math.round(revenue * 0.155);
    const acquiring = Math.round(revenue * 0.018);
    const logistics = Math.round(revenue * 0.071);
    const penalties = index % 6 === 4 ? 260 : 0;
    const storage = Math.round(revenue * 0.014);
    const adsSpend = Math.round(revenue * 0.038);
    const otherExpenses = Math.round(revenue * 0.006);
    const compensation = index % 4 === 1 ? 360 : 0;
    const cogs = Math.round(revenue * 0.31);
    const tax = Math.round(revenue * 0.06);
    const operationExpenses = 720;
    const marketplaceNet = revenue - commission - acquiring - logistics - penalties - storage - adsSpend - otherExpenses + compensation;
    const margin = marketplaceNet - cogs - tax - operationExpenses;
    const sold = 28 + ((index * 7) % 19);
    const returned = index % 4 === 1 ? 2 : 1;

    return {
      date: row.date,
      revenue,
      commission,
      acquiring: marketplace === 'all' ? null : acquiring,
      logistics,
      penalties,
      storage,
      ads_spend: adsSpend,
      other_expenses: otherExpenses,
      compensation,
      cogs,
      tax,
      operation_expenses: operationExpenses,
      margin: marketplace === 'all' ? null : margin,
      marketplace_net: marketplaceNet,
      unallocated_amount: 0,
      reconciliation_difference: 0,
      sold_quantity: marketplace === 'all' ? null : sold,
      returned_quantity: marketplace === 'all' ? null : returned,
      net_quantity: marketplace === 'all' ? null : sold - returned,
      profitability_complete: marketplace !== 'all',
      sku_allocation_complete: true,
      accrual_classification_complete: true,
    };
  });
}

function makeOzonDashboardRows(dateFrom, dateTo) {
  return datesInRange(dateFrom, dateTo).map((date, index) => {
    const revenue = Math.round((58400 + (index % 6) * 2140) * dayPattern[index % dayPattern.length]);
    const baseCommission = Math.round(revenue * 0.155);
    const acquiring = Math.round(revenue * 0.018);
    const commission = baseCommission + acquiring;
    const logistics = Math.round(revenue * 0.071);
    const penalties = index % 6 === 4 ? 260 : 0;
    const storage = Math.round(revenue * 0.014);
    const adsSpend = Math.round(revenue * 0.038);
    const otherExpenses = Math.round(revenue * 0.006);
    const compensation = index % 4 === 1 ? 360 : 0;
    const operationExpenses = 760 + otherExpenses - compensation;
    const cogs = Math.round(revenue * 0.31);
    const tax = Math.round(revenue * 0.06);
    const margin = revenue - commission - logistics - penalties - storage - adsSpend - cogs - tax - operationExpenses;
    const percent = (amount) => revenue ? (amount / revenue) * 100 : 0;

    return {
      date,
      revenue,
      commission,
      commission_pct: percent(commission),
      logistics,
      logistics_pct: percent(logistics),
      penalties,
      cogs,
      cogs_share: percent(cogs),
      tax,
      ads_spend: adsSpend,
      ads_pct: percent(adsSpend),
      storage,
      storage_pct: percent(storage),
      wb_expenses_share: percent(commission + logistics + penalties + storage),
      operation_expenses,
      margin,
      margin_pct: percent(margin),
      roi: cogs ? (margin / cogs) * 100 : 0,
    };
  });
}

function sumDashboardRows(...rowSets) {
  const rowsByDate = new Map();
  rowSets.flat().forEach((row) => {
    const current = rowsByDate.get(row.date) || { date: row.date };
    for (const key of ['revenue', 'commission', 'logistics', 'penalties', 'storage', 'ads_spend', 'cogs', 'tax', 'operation_expenses', 'margin']) {
      current[key] = (current[key] || 0) + (Number(row[key]) || 0);
    }
    rowsByDate.set(row.date, current);
  });

  return [...rowsByDate.values()].map((row) => {
    const percent = (amount) => row.revenue ? (amount / row.revenue) * 100 : 0;
    return {
      ...row,
      commission_pct: percent(row.commission),
      logistics_pct: percent(row.logistics),
      cogs_share: percent(row.cogs),
      ads_pct: percent(row.ads_spend),
      storage_pct: percent(row.storage),
      wb_expenses_share: percent(row.commission + row.logistics + row.penalties + row.storage),
      margin_pct: percent(row.margin),
      roi: row.cogs ? (row.margin / row.cogs) * 100 : 0,
    };
  });
}

function dashboardRowsForMarketplace(dateFrom, dateTo, marketplace) {
  const wbRows = makeWbRows(dateFrom, dateTo);
  const ozonRows = makeOzonDashboardRows(dateFrom, dateTo);
  if (marketplace === 'ozon') return ozonRows;
  if (marketplace === 'all') return sumDashboardRows(wbRows, ozonRows);
  return wbRows;
}

export async function getMe() {
  return { user_id: 'prototype-user', email: 'demo@wb-finance.local', ai_module_enabled: false };
}

export async function getStores() {
  return {
    stores: [{ owner_user_id: 'prototype-store', owner_email: 'demo@wb-finance.local', access: 'owner' }],
  };
}

export async function getDashboardState() {
  return {
    has_data: true,
    has_2025: true,
    has_2026: true,
    finance_backfill: { status: 'idle' },
    finance_backfill_2025: { status: 'idle' },
    finance_missing_sync: { status: 'idle' },
    funnel_tail_sync: { status: 'idle' },
    funnel_ytd_backfill: { status: 'idle' },
  };
}

export async function getPnl(dateFrom, dateTo, marketplace = 'wb') {
  return dashboardRowsForMarketplace(dateFrom, dateTo, marketplace);
}

export async function getFunnel(dateFrom, dateTo, marketplace = 'wb') {
  const wbRows = makeWbRows(dateFrom, dateTo).map((row) => ({ date: row.date, order_sum: Math.round(row.revenue * 0.88) }));
  const ozonRows = makeOzonDashboardRows(dateFrom, dateTo).map((row) => ({ date: row.date, order_sum: Math.round(row.revenue * 0.94) }));
  if (marketplace === 'ozon') return ozonRows;
  if (marketplace === 'all') {
    const ordersByDate = new Map(wbRows.map((row) => [row.date, row.order_sum]));
    return ozonRows.map((row) => ({ date: row.date, order_sum: row.order_sum + (ordersByDate.get(row.date) || 0) }));
  }
  return wbRows;
}

export async function getPlanFactMonths() {
  return [];
}

export async function getBillingStatus() {
  return { subscription_status: 'lifetime', days_left: null, is_access_blocked: false };
}

export async function getMarketplaceFinance(marketplace, dateFrom, dateTo, { signal } = {}) {
  if (signal?.aborted) throw new DOMException('Запрос остановлен', 'AbortError');
  const daily = makeOzonRows(dateFrom, dateTo, marketplace);
  const productRows = daily.map((row, index) => ({
    ...row,
    revenue: Math.round(row.revenue * 0.61),
    sold_quantity: row.sold_quantity == null ? null : Math.round(row.sold_quantity * 0.61),
    returned_quantity: row.returned_quantity == null ? null : Math.round(row.returned_quantity * 0.61),
    net_quantity: row.net_quantity == null ? null : Math.round(row.net_quantity * 0.61),
    marketplace_net: Math.round(row.marketplace_net * 0.61),
    profitability_complete: marketplace !== 'all',
    product_index: index,
  }));

  return {
    marketplace,
    currency: 'RUB',
    daily,
    products: [{
      marketplace: marketplace === 'all' ? 'ozon' : marketplace,
      sku: '810245731',
      name: 'Органайзер для хранения, набор 3 шт.',
      offer_id: 'ORG-03-GR',
      cost_price: 890,
      daily: productRows,
    }],
    coverage: { complete: true, missing_dates: [] },
    connection: { configured: true, status: 'ready' },
    sync: { status: 'idle' },
    warnings: [],
    accounting_basis: { costs: 'current_sku_cost_restates_all_dates', tax: 'estimated_revenue_times_wb_rate', wb_fully_reconciled: false },
  };
}

const unavailable = (name) => async () => {
  throw new Error(`${name} недоступно в локальном прототипе.`);
};

export const login = unavailable('Вход');
export const register = unavailable('Регистрация');
export const getArticles = unavailable('Загрузка артикулов');
export const saveArticlesCost = unavailable('Изменение себестоимости');
export const savePlanFactMonth = unavailable('Сохранение плана');
export const getSku = unavailable('Загрузка SKU');
export const getOperationalExpenses = unavailable('Загрузка операционных расходов');
export const createOperationalExpense = unavailable('Создание операционного расхода');
export const updateOperationalExpense = unavailable('Изменение операционного расхода');
export const triggerSyncSales = unavailable('Синхронизация продаж');
export const triggerSyncAds = unavailable('Синхронизация рекламы');
export const triggerSyncFunnel = unavailable('Синхронизация воронки');
export const triggerSyncPeriod = unavailable('Синхронизация периода');
export const triggerSyncRecalculate = unavailable('Пересчёт периода');
export const triggerInitialSync = unavailable('Первичная синхронизация');
export const triggerRecentSync = unavailable('Обновление данных');
export const triggerBackfill2026 = unavailable('Догрузка финансов');
export const triggerBackfill2025 = unavailable('Догрузка финансов');
export const triggerFunnelBackfillYtd = unavailable('Догрузка воронки');
export const getDailyBrief = unavailable('Загрузка сводки');
export const triggerDailyBrief = unavailable('Создание сводки');
export const getOfferAiStatus = unavailable('Статус AI');
export const uploadOfferAiFile = unavailable('Загрузка файла');
export const askOfferAi = unavailable('AI-запрос');
export const startOfferAiChat = unavailable('Начало чата');
export const resetOfferAiChat = unavailable('Сброс чата');
export const getOfferAiChatHistory = unavailable('История чата');
export const askOfferAiChat = unavailable('AI-запрос');
export const createCheckout = unavailable('Оплата');
export const syncYookassaReturn = unavailable('Проверка оплаты');
export const getBillingReminders = unavailable('Напоминания об оплате');
export const getAiTasks = unavailable('Задачи AI-модуля');
export const listProductGenerationJobs = unavailable('Задачи генерации');
export const getProductGenerationJob = unavailable('Задача генерации');
export const createProductGenerationJob = unavailable('Создание задачи генерации');
export const uploadProductGenerationJobReferences = unavailable('Загрузка референсов');
export const startProductGenerationJob = unavailable('Запуск генерации');
export const stopProductGenerationJob = unavailable('Остановка генерации');
export const generateProductGenerationContent = unavailable('Генерация контента');
export const downloadProductGenerationReference = unavailable('Загрузка референса');
export const downloadProductGenerationGeneratedAsset = unavailable('Загрузка результата');
export const updateProductGenerationJob = unavailable('Изменение задачи генерации');
export const updateAiTaskStatus = unavailable('Изменение статуса задачи AI');
export const getAiHypotheses = unavailable('Гипотезы AI');
export const startAiHypothesis = unavailable('Запуск гипотезы');
export const finishAiHypothesis = unavailable('Завершение гипотезы');
export const getAiHypothesisDailyLog = unavailable('Журнал гипотезы');
export const upsertAiHypothesisDailyLog = unavailable('Изменение журнала гипотезы');
export const getAiCompetitorReportActions = unavailable('Отчёт конкурентов');
export const getAiCompetitorReportStatus = unavailable('Статус отчёта конкурентов');
export const getAiCompetitorReportDetail = unavailable('Детали отчёта конкурентов');
export const requestAiCompetitorReportRefresh = unavailable('Обновление отчёта конкурентов');
export const executeAiTask = unavailable('Запуск задачи AI');
export const syncAiReviewReplies = unavailable('Загрузка отзывов');
export const getAiPendingReviewReplies = unavailable('Загрузка отзывов');
export const publishAiReviewReply = unavailable('Публикация ответа');
export const upsertAiWbCredentials = unavailable('Настройка WB');
export const getAiWbCredentialsStatus = unavailable('Статус WB');
export const grantAiWbAccessInteractive = unavailable('Доступ WB');
export const uploadAiWbAccessFile = unavailable('Загрузка файла доступа');
export const startAiWbRemoteAuth = unavailable('Авторизация WB');
export const saveAiWbRemoteAuth = unavailable('Сохранение авторизации WB');
export const getAiWbRemoteAuthStatus = unavailable('Статус авторизации WB');
export const getAiWbAccessStatus = unavailable('Доступ WB');
export const updateWbApiKey = unavailable('Обновление ключа WB');
export const getTaxRate = unavailable('Загрузка налоговой ставки');
export const saveTaxRate = unavailable('Изменение налоговой ставки');
export const getMarketplaceConnection = unavailable('Подключение маркетплейса');
export const getMarketplaceSettings = unavailable('Настройки маркетплейса');
export const saveMarketplaceSettings = unavailable('Изменение настроек маркетплейса');
export const saveMarketplaceDailyExpense = unavailable('Изменение расходов маркетплейса');
export const saveMarketplaceConnection = unavailable('Подключение маркетплейса');
export const syncMarketplaceFinance = unavailable('Синхронизация маркетплейса');
export const saveMarketplaceProductCost = unavailable('Изменение себестоимости товара');
export const getOutgoingStoreGrants = unavailable('Доступы магазина');
export const grantStoreAccess = unavailable('Предоставление доступа к магазину');
export const revokeStoreAccess = unavailable('Отзыв доступа к магазину');
export const getAssistantHistory = unavailable('История AI-чата');
export const askAssistant = unavailable('AI-запрос');
export const runCfoAnalysis = unavailable('Анализ CFO');
