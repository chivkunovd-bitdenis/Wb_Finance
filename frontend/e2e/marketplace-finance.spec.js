import { test, expect } from '@playwright/test';

const ownerStore = { owner_user_id: '1', owner_email: 'owner@example.com', access: 'owner' };
const viewerStore = { owner_user_id: '2', owner_email: 'shared@example.com', access: 'viewer' };

function emptyDaily(date = '2026-10-10', overrides = {}) {
  return {
    date,
    revenue: 0,
    commission: 0,
    acquiring: 0,
    logistics: 0,
    penalties: 0,
    storage: 0,
    ads_spend: 0,
    other_expenses: 0,
    compensation: 0,
    cogs: 0,
    tax: 0,
    operation_expenses: 0,
    margin: 0,
    marketplace_net: 0,
    unallocated_amount: 0,
    reconciliation_difference: 0,
    sold_quantity: null,
    returned_quantity: null,
    net_quantity: null,
    accrual_classification_complete: true,
    sku_allocation_complete: true,
    profitability_complete: false,
    ...overrides,
  };
}

function makeOverview(marketplace, options = {}) {
  const ozonOpex = options.expenseAmounts?.['2026-10-10'] ?? null;
  const costPrice = Object.hasOwn(options, 'costPrice') ? options.costPrice : 4;
  const soldQuantity = options.soldQuantity ?? 5;
  const ozonCogs = options.unknownCogs || options.unknownQuantity || costPrice == null ? null : Number(costPrice) * soldQuantity;
  const ozonProductMargin = ozonCogs == null ? null : 85 - ozonCogs - 6;
  const opexKnown = ozonOpex != null || options.opexConfirmed;
  const ozonMargin = ozonProductMargin == null || !opexKnown ? null : ozonProductMargin - (ozonOpex ?? 0);
  const ozonRow = emptyDaily('2026-10-10', {
    revenue: 100,
    commission: 10,
    acquiring: 2,
    sold_quantity: options.unknownQuantity ? null : soldQuantity,
    returned_quantity: options.unknownQuantity ? null : 0,
    net_quantity: options.unknownQuantity ? null : soldQuantity,
    logistics: 5,
    penalties: 2,
    storage: 1,
    ads_spend: 0,
    other_expenses: 0,
    compensation: 3,
    cogs: ozonCogs,
    tax: 6,
    operation_expenses: options.opexConfirmed ? (ozonOpex ?? 0) : ozonOpex,
    margin: ozonMargin,
    marketplace_net: 85,
    unallocated_amount: 2,
    reconciliation_difference: 0,
    profitability_complete: ozonMargin != null,
  });
  const ozonProductRow = { ...ozonRow, operation_expenses: null, margin: ozonProductMargin };
  const wbRow = emptyDaily('2026-10-10', {
    revenue: 200,
    commission: 25,
    acquiring: null,
    logistics: 8,
    penalties: 0,
    storage: 1,
    ads_spend: 4,
    other_expenses: 2,
    compensation: 0,
    cogs: 40,
    tax: 12,
    operation_expenses: 0,
    margin: 108,
    marketplace_net: 160,
    unallocated_amount: 0,
    reconciliation_difference: 0,
  });
  const wbProductRow = { ...wbRow };
  for (const field of ['acquiring', 'sold_quantity', 'returned_quantity', 'net_quantity', 'accrual_classification_complete', 'sku_allocation_complete', 'profitability_complete']) {
    delete wbProductRow[field];
  }
  const ozonProducts = [
    { marketplace: 'ozon', sku: '42', offer_id: 'ozon-42', name: 'Ozon Cable', cost_price: options.unknownCogs ? null : costPrice, daily: [ozonProductRow] },
  ];
  if (options.extraCostProduct) {
    ozonProducts.push({
      marketplace: 'ozon', sku: '43', offer_id: 'ozon-43', name: 'Ozon Charger', cost_price: 8,
      daily: [emptyDaily('2026-10-10', { revenue: 50, commission: 5, acquiring: 1, sold_quantity: 1, returned_quantity: 0, net_quantity: 1, cogs: 8, tax: 3, margin: 33, marketplace_net: 44 })],
    });
  }
  const products = marketplace === 'all'
    ? [
      { marketplace: 'wb', sku: '42', offer_id: 'wb-42', name: 'WB Cable', cost_price: 40, daily: [wbProductRow] },
      ...ozonProducts,
    ]
    : ozonProducts;
  const daily = marketplace === 'all'
    ? [{ ...emptyDaily('2026-10-10', { revenue: 300, commission: 35, acquiring: null, sold_quantity: null, returned_quantity: null, net_quantity: null, logistics: 13, penalties: 2, storage: 2, ads_spend: 4, other_expenses: 2, compensation: 3, cogs: ozonCogs == null ? null : 40 + ozonCogs, operation_expenses: ozonOpex ?? (options.opexConfirmed ? 0 : null), margin: ozonMargin == null ? null : 108 + ozonMargin, marketplace_net: 245, unallocated_amount: 2, reconciliation_difference: 0, profitability_complete: ozonMargin != null }) }]
    : [ozonRow];

  return {
    marketplace,
    currency: 'RUB',
    accounting_basis: {
      margin: marketplace === 'all' ? 'preliminary_wb_and_ozon' : 'current_cost_estimate',
      costs: 'current_sku_cost_restates_all_dates',
      tax: 'estimated_revenue_times_wb_rate',
      wb_fully_reconciled: marketplace === 'all' ? false : null,
    },
    daily,
    products,
    warnings: Array.isArray(options.warnings) ? options.warnings : (options.warning ? [options.warning] : []),
    coverage: options.partial ? { complete: false, missing_dates: ['2026-10-09'] } : { complete: true, missing_dates: [] },
    connection: options.connection || { configured: true, status: 'ready' },
    sync: { status: 'idle' },
  };
}

async function mockApplication(page, options = {}) {
  const storeId = options.viewer ? '2' : '1';
  const stores = [ownerStore, viewerStore];
  await page.addInitScript((activeStoreId) => {
    localStorage.setItem('wb_finance_token', 'e2e-access-token');
    localStorage.setItem('wb_finance_active_store_owner_id', activeStoreId);
  }, storeId);

  await page.route('**/auth/me', (route) => route.fulfill({ json: { ai_module_enabled: false } }));
  await page.route('**/stores', (route) => route.fulfill({ json: { stores } }));
  await page.route('**/stores/grants', (route) => route.fulfill({ json: { grants: [] } }));
  await page.route('**/billing/status', (route) => route.fulfill({ json: { subscription_status: 'lifetime', is_access_blocked: false } }));
  await page.route('**/dashboard/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    if (path === '/dashboard/state') {
      return route.fulfill({ json: { has_data: true, has_2026: true, has_2025: true, finance_backfill: { status: 'complete' } } });
    }
    if (path === '/dashboard/marketplace/overview') {
      const marketplace = url.searchParams.get('marketplace') || 'ozon';
      options.overviewRequests?.push({
        marketplace,
        date_from: url.searchParams.get('date_from'),
        date_to: url.searchParams.get('date_to'),
      });
      if (options.overviewFailure?.remaining > 0) {
        options.overviewFailure.remaining -= 1;
        return route.fulfill({ status: 503, json: { detail: 'Temporary mocked overview error' } });
      }
      if (typeof options.handleOverview === 'function') return options.handleOverview(route, marketplace);
      const body = makeOverview(marketplace, options.overview || {});
      const dateFrom = url.searchParams.get('date_from');
      const dateTo = url.searchParams.get('date_to');
      const revenue = options.overviewRevenueByDateFrom?.[dateFrom];
      if (revenue !== undefined) {
        for (const row of body.daily || []) {
          row.date = dateTo;
          row.revenue = revenue;
        }
        for (const product of body.products || []) {
          for (const row of product.daily || []) {
            row.date = dateTo;
            row.revenue = revenue;
          }
        }
      }
      return route.fulfill({ json: body });
    }
    if (path === '/dashboard/marketplace/connection') {
      if (method === 'PUT') {
        const body = route.request().postDataJSON();
        options.connectionWrites?.push({ client_id: body.client_id, api_key: body.api_key });
        return route.fulfill({ json: { configured: true, client_id: body.client_id, status: 'configured' } });
      }
      return route.fulfill({ json: options.connection || { configured: true, client_id: 'client-1', status: 'configured' } });
    }
    if (path === '/dashboard/marketplace/settings') {
      if (method === 'PUT') {
        const body = route.request().postDataJSON();
        options.financialSettingsWrites?.push(body);
        options.financialSettings = { ...(options.financialSettings || { tax_rate: 0.06, tax_rate_source: 'wb', operation_expenses_confirmed: false }), ...body };
        return route.fulfill({ json: options.financialSettings });
      }
      return route.fulfill({ json: options.financialSettings || { tax_rate: 0.06, tax_rate_source: 'wb', operation_expenses_confirmed: false } });
    }
    if (path.startsWith('/dashboard/marketplace/expenses/') && method === 'PUT') {
      const date = path.split('/').at(-1);
      const body = route.request().postDataJSON();
      options.expenseWrites?.push({ date, amount: body.amount });
      options.overview ||= {};
      options.overview.expenseAmounts ||= {};
      options.overview.expenseAmounts[date] = body.amount;
      return route.fulfill({ json: { date, amount: body.amount } });
    }
    if (path === '/dashboard/marketplace/sync' && method === 'POST') {
      return route.fulfill({ status: 202, json: { status: 'queued' } });
    }
    if (path.startsWith('/dashboard/marketplace/products/') && method === 'PUT') {
      const body = route.request().postDataJSON();
      options.costWrites?.push(body.cost_price);
      options.overview ||= {};
      const savedCostPrice = body.cost_price == null || !options.roundCostPrice
        ? body.cost_price
        : Math.round(Number(body.cost_price) * 100) / 100;
      if (options.costPutGate) {
        options.costPutGate.started = true;
        await new Promise((resolve) => { options.costPutGate.release = resolve; });
      }
      options.overview.costPrice = savedCostPrice;
      const response = await route.fulfill({ json: { cost_price: savedCostPrice, message: 'Себестоимость сохранена.' } });
      if (options.costPutGate) options.costPutGate.completed = true;
      return response;
    }
    if (path === '/dashboard/pnl' || path === '/dashboard/articles' || path === '/dashboard/funnel') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/dashboard');
  await expect(page.getByTestId('marketplace-selector')).toHaveValue('wb');
}

test.describe('Marketplace finance', () => {
  test('switches Ozon and all views while keeping equal SKUs separate', async ({ page }) => {
    await mockApplication(page, { overview: { opexConfirmed: true } });
    await expect(page.getByText('Детализация по дням').first()).toBeVisible();
    await expect(page.getByTestId('wb-profit-basis-note')).toContainText('Маржа WB — предварительная оценка');

    await page.getByTestId('marketplace-selector').selectOption('ozon');
    await expect(page.getByTestId('wb-profit-basis-note')).toHaveCount(0);
    await expect(page.getByTestId('marketplace-summary-kpis')).toBeVisible();
    await expect(page.getByTestId('marketplace-summary-kpis').getByText('100,00 ₽')).toBeVisible();
    await expect(page.getByTestId('marketplace-metric-acquiring').getByText('2,00 ₽')).toBeVisible();
    await expect(page.getByTestId('marketplace-day-2026-10-10').locator('td').nth(2)).toHaveText('5');
    await expect(page.getByTestId('marketplace-day-2026-10-10').locator('td').nth(3)).toHaveText('0');
    await expect(page.getByTestId('marketplace-day-2026-10-10').locator('td').nth(4)).toHaveText('5');
    await expect(page.getByTestId('marketplace-day-2026-10-10').locator('td').nth(1)).toContainText('2,00 ₽');
    await expect(page.getByTestId('marketplace-summary-kpis').getByText('Расчётная прибыль')).toBeVisible();
    await expect(page.getByTestId('marketplace-metric-margin').locator('td').first()).toHaveText('Расчётная прибыль');
    await expect(page.getByTestId('marketplace-proof-note')).toContainText('данные Ozon за один период сверены с бухгалтерскими итогами');
    await expect(page.getByTestId('marketplace-proof-note')).toContainText('сопоставление возвратов на живых данных ещё не проверено');
    await expect(page.getByTestId('marketplace-profit-basis-note')).toContainText('текущая себестоимость товара применяется ко всем датам');
    await expect(page.getByTestId('marketplace-profit-basis-note')).toContainText('налог оценён от выручки по ставке из настроек WB');
    await expect(page.getByTestId('marketplace-profit-basis-note')).toContainText('не бухгалтерская сверка или налоговая декларация');

    await page.getByTestId('marketplace-selector').selectOption('all');
    await expect(page.getByTestId('marketplace-summary-kpis').getByText('Предварительная прибыль')).toBeVisible();
    await expect(page.getByTestId('marketplace-metric-margin').locator('td').first()).toHaveText('Предварительная прибыль');
    await expect(page.getByTestId('marketplace-profit-basis-note')).toContainText('финансовые данные WB в общем отчёте сверены не полностью');
    await expect(page.getByTestId('marketplace-product-wb:42')).toBeVisible();
    await expect(page.getByTestId('marketplace-product-ozon:42')).toBeVisible();
    await expect(page.getByText('WB Cable')).toBeVisible();
    await expect(page.getByText('Ozon Cable')).toBeVisible();
    const wbProduct = page.getByTestId('marketplace-product-wb:42');
    const ozonProduct = page.getByTestId('marketplace-product-ozon:42');
    await expect(wbProduct.locator('td').nth(2)).toHaveText('—');
    await expect(wbProduct.locator('td').nth(2).locator('span')).toHaveAttribute('title', /не выделяется в сохранённой финансовой витрине Wildberries/);
    await expect(ozonProduct.locator('td').nth(2)).toHaveText('5');
    await expect(wbProduct.locator('td').nth(6)).toContainText('40,00 ₽');
    await expect(wbProduct.locator('td').nth(7)).toContainText('108,00 ₽');
    await expect(ozonProduct.locator('td').nth(6)).toContainText('20,00 ₽');
    await expect(ozonProduct.locator('td').nth(7)).toContainText('59,00 ₽');
    await expect(page.getByTestId('marketplace-products-table').getByText('Предварительная прибыль до общих расходов')).toBeVisible();
    await expect(page.getByTestId('marketplace-metric-cogs').locator('td').nth(1)).toContainText('60,00 ₽');
    await expect(page.getByTestId('marketplace-metric-margin').locator('td').nth(1)).toContainText('167,00 ₽');
    await expect(page.getByText('Общее количество по площадкам не суммируется; в товарных строках показаны данные своей площадки.')).toBeVisible();
    await expect(page.getByTestId('marketplace-metric-commission').getByText('35,00 ₽')).toBeVisible();
    await expect(page.getByTestId('marketplace-metric-acquiring')).toContainText('Эквайринг (не сопоставляется)');
    await expect(page.getByTestId('marketplace-metric-acquiring').locator('td').nth(1).getByText('—')).toBeVisible();
    await expect(page.getByTestId('marketplace-metric-acquiring').locator('td').nth(1).locator('span')).toHaveAttribute('title', /Wildberries включает её в комиссию/);
    await expect(page.getByTestId('marketplace-metric-acquiring').locator('td').nth(2)).toContainText('—');
    await expect(page.getByTestId('marketplace-day-2026-10-10').locator('td').nth(1).locator('span')).toHaveAttribute('title', /Wildberries включает её в комиссию/);
    await expect(page.getByTestId('marketplace-day-2026-10-10').locator('td').nth(2).locator('span')).toHaveAttribute('title', /нельзя сопоставить/);
    await page.screenshot({ path: 'test-results/marketplace-finance-desktop.png', fullPage: true });

    await page.getByTestId('marketplace-product-toggle-wb:42').click();
    await expect(page.locator('.marketplace-product-days').first().locator('thead th').nth(15)).toHaveText('Предварительная прибыль до общих расходов');
    await page.getByTestId('marketplace-product-toggle-ozon:42').click();
    await expect(page.locator('.marketplace-product-days').nth(1).locator('thead th').nth(15)).toHaveText('Расчётная прибыль до общих расходов');
  });

  test('Ozon finance remains readable on a narrow screen', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockApplication(page);
    await page.getByTestId('marketplace-selector').selectOption('ozon');
    await expect(page.getByTestId('marketplace-screen-dashboard')).toBeVisible();
    await expect(page.getByTestId('marketplace-summary-kpis')).toBeVisible();
    const layoutCheck = await page.evaluate(() => {
      const screen = document.querySelector('[data-testid="marketplace-screen-dashboard"]');
      const content = screen?.closest('.content');
      const syncButton = document.querySelector('[data-testid="marketplace-sync-button"]');
      const buttonBounds = syncButton?.getBoundingClientRect();
      return {
        viewportWidth: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        contentWidth: content?.getBoundingClientRect().width || 0,
        syncButtonLeft: buttonBounds?.left ?? null,
        syncButtonRight: buttonBounds?.right ?? null,
      };
    });
    expect(layoutCheck.documentWidth).toBeLessThanOrEqual(layoutCheck.viewportWidth);
    expect(layoutCheck.contentWidth).toBeGreaterThanOrEqual(320);
    expect(layoutCheck.syncButtonLeft).not.toBeNull();
    expect(layoutCheck.syncButtonLeft).toBeGreaterThanOrEqual(0);
    expect(layoutCheck.syncButtonRight).toBeLessThanOrEqual(layoutCheck.viewportWidth);
    await page.screenshot({ path: 'test-results/marketplace-finance-mobile.png', fullPage: true });
  });

  test('shows disconnected and partial states without turning unknown amounts into zero', async ({ page }) => {
    await mockApplication(page, {
      overview: {
        partial: true,
        unknownCogs: true,
        unknownQuantity: true,
        warning: 'Нет подтверждённого количества проданных единиц.',
        connection: { configured: false, status: 'missing' },
      },
    });

    await page.getByTestId('marketplace-selector').selectOption('ozon');
    await expect(page.getByTestId('marketplace-unconfigured')).toBeVisible();
    await expect(page.getByTestId('marketplace-partial-coverage')).toContainText('09.10.2026');
    await expect(page.getByTestId('marketplace-warnings')).toContainText('Нет подтверждённого количества');
    await expect(page.getByTestId('marketplace-summary-kpis').getByText('—').first()).toBeVisible();
    await expect(page.getByTestId('marketplace-metric-cogs').getByText('—').first()).toBeVisible();
    await expect(page.getByTestId('marketplace-day-2026-10-10').getByText('0,00 ₽').first()).toBeVisible();
    await expect(page.getByTestId('marketplace-day-2026-10-10').locator('td').nth(2)).toHaveText('—');
    await expect(page.getByTestId('marketplace-day-2026-10-10').locator('td').nth(2).locator('span')).toHaveAttribute('title', /Период загружен не полностью/);
  });

  test('keeps unconfirmed quantities and COGS unknown even when daily coverage is complete', async ({ page }) => {
    await mockApplication(page, { overview: { unknownQuantity: true } });
    await page.getByTestId('marketplace-selector').selectOption('ozon');
    const day = page.getByTestId('marketplace-day-2026-10-10');
    await expect(day.locator('td').nth(2)).toHaveText('—');
    await expect(day.locator('td').nth(2).locator('span')).toHaveAttribute('title', /Количество не подтверждено/);
    await expect(day.locator('td').filter({ hasText: '20,00 ₽' })).toHaveCount(0);
    await expect(page.getByTestId('marketplace-metric-cogs').locator('td').nth(1).getByText('—')).toBeVisible();
  });

  test('shows warnings beyond the first five after explicit expansion', async ({ page }) => {
    await mockApplication(page, {
      overview: {
        warnings: [
          'Предупреждение 1',
          'Предупреждение 2',
          'Предупреждение 3',
          'Предупреждение 4',
          'Предупреждение 5',
          'Расхождение последнего начисления: сумма не совпала с итогом операции.',
        ],
      },
    });

    await page.getByTestId('marketplace-selector').selectOption('ozon');
    const warnings = page.getByTestId('marketplace-warnings');
    const lastWarning = warnings.getByText('Расхождение последнего начисления: сумма не совпала с итогом операции.');
    await expect(warnings.locator('ul').first().locator('li')).toHaveCount(5);
    await expect(lastWarning).toBeHidden();
    await expect(warnings.locator('details summary')).toHaveText('Показать остальные замечания (1)');
    await warnings.locator('details summary').click();
    await expect(lastWarning).toBeVisible();
  });

  test('shows a recoverable error when the overview endpoint fails', async ({ page }) => {
    let shouldFail = true;
    await mockApplication(page, {
      handleOverview: (route, marketplace) => {
        if (marketplace === 'ozon' && shouldFail) {
          return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Финансовый сервис временно недоступен.' }) });
        }
        return route.fulfill({ json: makeOverview(marketplace) });
      },
    });

    await page.getByTestId('marketplace-selector').selectOption('ozon');
    await expect(page.getByTestId('marketplace-load-error')).toContainText('Финансовый сервис временно недоступен.');
    shouldFail = false;
    await page.getByRole('button', { name: 'Повторить' }).click();
    await expect(page.getByTestId('marketplace-summary-kpis')).toBeVisible();
  });

  test('owner connection form clears the API key after save and never writes it to storage', async ({ page }) => {
    const connectionWrites = [];
    await mockApplication(page, { connectionWrites });
    await page.goto('/settings');

    const clientId = page.getByTestId('ozon-client-id');
    const apiKey = page.getByTestId('ozon-api-key');
    await expect(clientId).toBeVisible();
    await expect(page.getByTestId('ozon-connection-status')).toHaveText('Данные сохранены');
    await clientId.fill('client-new');
    await apiKey.fill('secret-e2e-key-value');
    await page.getByTestId('ozon-connection-save').click();
    await expect(page.getByTestId('ozon-connection-saved')).toBeVisible();
    await expect(page.getByTestId('ozon-connection-status')).toHaveText('Данные сохранены');
    await expect(page.getByTestId('ozon-connection-status')).not.toHaveText('Подключён');
    await expect(apiKey).toHaveValue('');
    await expect.poll(() => connectionWrites.length).toBe(1);
    expect(connectionWrites[0]).toEqual({ client_id: 'client-new', api_key: 'secret-e2e-key-value' });
    expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain('secret-e2e-key-value');
  });

  test('owner reads the WB tax rate and can confirm or revoke expense completeness', async ({ page }) => {
    const financialSettingsWrites = [];
    await mockApplication(page, { financialSettingsWrites });
    await page.goto('/settings');

    const confirmOpex = page.getByTestId('ozon-opex-confirm');
    const save = page.getByTestId('ozon-financial-settings-save');
    await expect(page.getByTestId('ozon-tax-rate-readonly')).toContainText('Ставка налога из настроек WB: 6%');
    await expect(page.getByTestId('ozon-tax-rate')).toHaveCount(0);
    await confirmOpex.check();
    await save.click();
    await expect(page.getByTestId('ozon-financial-settings-saved')).toBeVisible();
    await expect.poll(() => financialSettingsWrites.length).toBe(1);
    expect(financialSettingsWrites[0]).toEqual({ operation_expenses_confirmed: true });

    await confirmOpex.uncheck();
    await save.click();
    await expect.poll(() => financialSettingsWrites.length).toBe(2);
    expect(financialSettingsWrites[1]).toEqual({ operation_expenses_confirmed: false });
  });

  test('connection status distinguishes saved, checking, ready, error and missing states', async ({ page }) => {
    const connection = { configured: true, client_id: 'client-1', status: 'configured' };
    await mockApplication(page, { connection });
    await page.goto('/settings');
    const status = page.getByTestId('ozon-connection-status');
    await expect(status).toHaveText('Данные сохранены');

    connection.status = 'syncing';
    await page.reload();
    await expect(status).toHaveText('Проверяем');

    connection.status = 'ready';
    await page.reload();
    await expect(status).toHaveText('Подключён');

    connection.status = 'error';
    connection.error = 'Проверьте доступ к кабинету.';
    await page.reload();
    await expect(status).toHaveText('Ошибка');

    connection.configured = false;
    connection.status = 'missing';
    delete connection.error;
    await page.reload();
    await expect(status).toHaveText('Не подключён');
  });

  test('cost editor preserves explicit zero and can clear a saved amount', async ({ page }) => {
    const costWrites = [];
    const overviewFailure = { remaining: 0 };
    await mockApplication(page, { costWrites, overviewFailure, roundCostPrice: true, overview: { opexConfirmed: true, extraCostProduct: true } });
    await page.getByTestId('marketplace-selector').selectOption('ozon');
    await page.getByRole('link', { name: 'Себестоимость' }).click();
    await expect(page.getByTestId('marketplace-cost-history-warning')).toContainText('расчётная прибыль пересчитается за всю историю');
    const product = page.getByTestId('marketplace-product-ozon:42');
    const input = page.getByTestId('marketplace-cost-input-ozon:42');
    const otherInput = page.getByTestId('marketplace-cost-input-ozon:43');
    await expect(input).toBeVisible();
    await expect(product.locator('td').nth(6)).toContainText('20,00 ₽');
    await expect(product.locator('td').nth(7)).toContainText('59,00 ₽');
    await otherInput.fill('11');

    await input.fill('0');
    await product.getByRole('button', { name: 'Сохранить' }).click();
    await expect(product.locator('td').nth(6)).toContainText('0,00 ₽');
    await expect(product.locator('td').nth(7)).toContainText('79,00 ₽');
    await expect(otherInput).toHaveValue('11');
    await expect(product.getByRole('status')).toHaveText('Себестоимость сохранена.');
    await expect.poll(() => costWrites.length).toBe(1);
    expect(costWrites[0]).toBe(0);

    await input.fill('1.239');
    await product.getByRole('button', { name: 'Сохранить' }).click();
    await expect(input).toHaveValue('1.24');
    await expect(product.locator('td').nth(6)).toContainText('6,20 ₽');
    await expect(product.locator('td').nth(7)).toContainText('72,80 ₽');
    await expect(otherInput).toHaveValue('11');
    await expect.poll(() => costWrites.length).toBe(2);
    expect(costWrites[1]).toBe(1.239);

    overviewFailure.remaining = 1;
    await input.fill('');
    await product.getByRole('button', { name: 'Сохранить' }).click();
    await expect(product.getByRole('alert')).toContainText('Себестоимость уже сохранена, но новые суммы пока не загрузились.');
    await expect(input).toHaveValue('');
    await expect(product.locator('td').nth(6)).toContainText('6,20 ₽');
    await expect(otherInput).toHaveValue('11');
    await product.getByRole('button', { name: 'Повторить обновление' }).click();
    await expect(product.locator('td').nth(6)).toHaveText('—');
    await expect(product.locator('td').nth(7)).toHaveText('—');
    await expect(otherInput).toHaveValue('11');
    await expect(product.getByRole('alert')).toHaveCount(0);
    await expect.poll(() => costWrites.length).toBe(3);
    expect(costWrites[2]).toBeNull();
  });

  test('a pending cost save cannot refresh or overwrite a newly selected date range', async ({ page }) => {
    const costPutGate = { started: false, completed: false, release: null };
    const overviewRequests = [];
    await mockApplication(page, {
      costPutGate,
      overviewRequests,
      overviewRevenueByDateFrom: { '2026-09-01': 201 },
      overview: { opexConfirmed: true },
    });
    await page.getByTestId('marketplace-selector').selectOption('ozon');
    await page.getByRole('link', { name: 'Себестоимость' }).click();

    const product = page.getByTestId('marketplace-product-ozon:42');
    const costInput = page.getByTestId('marketplace-cost-input-ozon:42');
    await costInput.fill('0');
    const costPutResponse = page.waitForResponse((response) => (
      response.request().method() === 'PUT'
      && new URL(response.url()).pathname === '/dashboard/marketplace/products/42/cost'
    ));
    await product.getByRole('button', { name: 'Сохранить' }).click();
    await expect.poll(() => costPutGate.started).toBe(true);

    const dateInputs = page.locator('.date-group input[type="date"]');
    await dateInputs.nth(0).fill('2026-09-01');
    await dateInputs.nth(1).fill('2026-09-01');
    await page.getByRole('button', { name: 'Показать' }).click();

    const septemberProduct = page.getByTestId('marketplace-product-ozon:42');
    await expect(page.getByTestId('marketplace-screen-costs')).toBeVisible();
    await expect(septemberProduct.locator('td').nth(5)).toContainText('201,00 ₽');
    expect(overviewRequests).toContainEqual({ marketplace: 'ozon', date_from: '2026-09-01', date_to: '2026-09-01' });
    const requestsAfterNewRangeLoaded = overviewRequests.length;

    costPutGate.release();
    await costPutResponse;
    await expect.poll(() => costPutGate.completed).toBe(true);
    await page.waitForTimeout(150);

    expect(overviewRequests).toHaveLength(requestsAfterNewRangeLoaded);
    await expect(page.getByTestId('marketplace-screen-costs')).toBeVisible();
    await expect(septemberProduct.locator('td').nth(5)).toContainText('201,00 ₽');
    await expect(septemberProduct.getByRole('status')).toHaveCount(0);
    await expect(septemberProduct.getByRole('alert')).toHaveCount(0);
    await expect(costInput).toHaveValue('4');
  });

  test('cost-save refresh clears loading when it supersedes a same-range refetch', async ({ page }) => {
    const costPutGate = { started: false, completed: false, release: null };
    let releaseRefetch;
    let blockNextOzonOverview = false;
    let refetchReached = false;
    const blockedRefetch = new Promise((resolve) => { releaseRefetch = resolve; });
    const overviewRequests = [];
    const overview = { opexConfirmed: true };
    await mockApplication(page, {
      costPutGate,
      overview,
      overviewRequests,
      handleOverview: async (route, marketplace) => {
        if (marketplace === 'ozon' && blockNextOzonOverview) {
          blockNextOzonOverview = false;
          refetchReached = true;
          await blockedRefetch;
        }
        return route.fulfill({ json: makeOverview(marketplace, overview) });
      },
    });
    await page.getByTestId('marketplace-selector').selectOption('ozon');
    await page.getByRole('link', { name: 'Себестоимость' }).click();

    const product = page.getByTestId('marketplace-product-ozon:42');
    const input = page.getByTestId('marketplace-cost-input-ozon:42');
    await expect(product).toBeVisible();
    await input.fill('0');
    const costPutResponse = page.waitForResponse((response) => (
      response.request().method() === 'PUT'
      && new URL(response.url()).pathname === '/dashboard/marketplace/products/42/cost'
    ));
    await product.getByRole('button', { name: 'Сохранить' }).click();
    await expect.poll(() => costPutGate.started).toBe(true);

    blockNextOzonOverview = true;
    await page.getByTestId('marketplace-refresh-button').click();
    await expect.poll(() => refetchReached).toBe(true);
    await expect(page.getByTestId('marketplace-loading')).toBeVisible();

    costPutGate.release();
    await costPutResponse;
    await expect.poll(() => costPutGate.completed).toBe(true);
    await expect(page.getByTestId('marketplace-screen-costs')).toBeVisible();
    const refreshedProduct = page.getByTestId('marketplace-product-ozon:42');
    await expect(refreshedProduct.locator('td').nth(6)).toContainText('0,00 ₽');
    await expect(refreshedProduct.locator('td').nth(7)).toContainText('79,00 ₽');

    releaseRefetch();
    await page.waitForTimeout(100);
    await expect(refreshedProduct).toBeVisible();
  });

  test('Ozon daily expenses keep missing values unknown, accept explicit zero, and remain readonly in all mode', async ({ page }) => {
    const expenseWrites = [];
    await mockApplication(page, { expenseWrites });
    await page.getByTestId('marketplace-selector').selectOption('ozon');
    await page.getByRole('link', { name: 'Опер. расходы' }).click();
    const day = '2026-10-10';
    const input = page.getByTestId(`marketplace-expense-input-${day}`);
    await expect(page.getByTestId(`marketplace-expense-day-${day}`).locator('td').nth(1)).toContainText('—');
    await input.fill('0');
    await page.getByTestId(`marketplace-expense-day-${day}`).getByRole('button', { name: 'Сохранить' }).click();
    await expect.poll(() => expenseWrites.length).toBe(1);
    expect(expenseWrites[0]).toEqual({ date: day, amount: 0 });
    await expect(page.getByTestId(`marketplace-expense-day-${day}`).locator('td').nth(1)).toContainText('0,00 ₽');

    await page.getByTestId('marketplace-selector').selectOption('all');
    await expect(page.getByTestId('marketplace-expenses-all-readonly')).toBeVisible();
    await expect(page.getByTestId(`marketplace-expense-input-${day}`)).toHaveCount(0);
  });

  test('viewer sees Ozon screens but cannot change connection, costs, or start sync', async ({ page }) => {
    await mockApplication(page, { viewer: true });
    await page.goto('/settings');
    await expect(page.getByTestId('ozon-connection-viewer-readonly')).toBeVisible();
    await expect(page.getByTestId('ozon-api-key')).toHaveCount(0);
    await expect(page.getByTestId('ozon-connection-save')).toHaveCount(0);
    await expect(page.getByTestId('ozon-financial-settings-viewer-readonly')).toContainText('Ставка налога из настроек WB: 6%');
    await expect(page.getByTestId('ozon-tax-rate')).toHaveCount(0);
    await expect(page.getByTestId('ozon-financial-settings-save')).toHaveCount(0);

    await page.goto('/dashboard');
    await page.getByTestId('marketplace-selector').selectOption('ozon');
    await expect(page.getByTestId('marketplace-screen-dashboard')).toBeVisible();
    await expect(page.getByTestId('marketplace-sync-button')).toHaveCount(0);
    await page.getByRole('link', { name: 'Себестоимость' }).click();
    await expect(page.getByTestId('marketplace-cost-viewer-readonly')).toBeVisible();
    await expect(page.getByTestId('marketplace-cost-input-ozon:42')).toHaveCount(0);
    await page.getByRole('link', { name: 'Опер. расходы' }).click();
    await expect(page.getByTestId('marketplace-expenses-viewer-readonly')).toBeVisible();
    await expect(page.getByTestId('marketplace-expense-input-2026-10-10')).toHaveCount(0);
  });

  test('a fast switch cannot show a late response from the previous marketplace', async ({ page }) => {
    let releaseOzon;
    let markOzonRequested;
    const ozonRequested = new Promise((resolve) => { markOzonRequested = resolve; });
    const blockedOzon = new Promise((resolve) => { releaseOzon = resolve; });
    await mockApplication(page, {
      handleOverview: async (route, marketplace) => {
        if (marketplace === 'ozon') {
          markOzonRequested();
          await blockedOzon;
          try {
            return route.fulfill({ json: makeOverview('ozon', { warning: 'Late Ozon response' }) });
          } catch {
            return undefined;
          }
        }
        return route.fulfill({ json: makeOverview('all') });
      },
    });

    await page.getByTestId('marketplace-selector').selectOption('ozon');
    await ozonRequested;
    await page.getByTestId('marketplace-selector').selectOption('all');
    await expect(page.getByTestId('marketplace-product-wb:42')).toBeVisible();
    releaseOzon();
    await expect(page.getByTestId('marketplace-warnings')).toHaveCount(0);
    await expect(page.getByText('Late Ozon response')).toHaveCount(0);
  });
});
