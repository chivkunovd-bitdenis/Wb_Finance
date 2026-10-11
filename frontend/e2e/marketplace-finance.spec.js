import { test, expect } from '@playwright/test';

const ownerStore = { owner_user_id: '1', owner_email: 'owner@example.com', access: 'owner' };
const viewerStore = { owner_user_id: '2', owner_email: 'shared@example.com', access: 'viewer' };

function emptyDaily(date = '2026-10-10', overrides = {}) {
  return {
    date,
    revenue: 0,
    commission: 0,
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
    ...overrides,
  };
}

function makeOverview(marketplace, options = {}) {
  const ozonRow = emptyDaily('2026-10-10', {
    revenue: 100,
    commission: 10,
    logistics: 5,
    penalties: 2,
    storage: 1,
    ads_spend: 0,
    other_expenses: 0,
    compensation: 3,
    cogs: options.unknownCogs ? null : 20,
    tax: 6,
    operation_expenses: 0,
    margin: options.unknownCogs ? null : 59,
    marketplace_net: 85,
    unallocated_amount: 2,
    reconciliation_difference: 0,
  });
  const wbRow = emptyDaily('2026-10-10', {
    revenue: 200,
    commission: 25,
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
  const products = marketplace === 'all'
    ? [
      { marketplace: 'wb', sku: '42', offer_id: 'wb-42', name: 'WB Cable', cost_price: 40, daily: [wbRow] },
      { marketplace: 'ozon', sku: '42', offer_id: 'ozon-42', name: 'Ozon Cable', cost_price: 20, daily: [ozonRow] },
    ]
    : [{ marketplace: 'ozon', sku: '42', offer_id: 'ozon-42', name: 'Ozon Cable', cost_price: options.unknownCogs ? null : 20, daily: [ozonRow] }];
  const daily = marketplace === 'all'
    ? [{ ...emptyDaily('2026-10-10', { revenue: 300, commission: 35, logistics: 13, penalties: 2, storage: 2, ads_spend: 4, other_expenses: 2, compensation: 3, cogs: options.unknownCogs ? null : 60, tax: 18, margin: options.unknownCogs ? null : 167, marketplace_net: 245, unallocated_amount: 2, reconciliation_difference: 0 }) }]
    : [ozonRow];

  return {
    marketplace,
    currency: 'RUB',
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
      if (typeof options.handleOverview === 'function') return options.handleOverview(route, marketplace);
      const body = makeOverview(marketplace, options.overview || {});
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
    if (path === '/dashboard/marketplace/sync' && method === 'POST') {
      return route.fulfill({ status: 202, json: { status: 'queued' } });
    }
    if (path.startsWith('/dashboard/marketplace/products/') && method === 'PUT') {
      const body = route.request().postDataJSON();
      options.costWrites?.push(body.cost_price);
      return route.fulfill({ json: { cost_price: body.cost_price, message: 'Прибыль останется неизвестной до подтверждения количества проданных товаров.' } });
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
    await mockApplication(page);
    await expect(page.getByText('Детализация по дням').first()).toBeVisible();

    await page.getByTestId('marketplace-selector').selectOption('ozon');
    await expect(page.getByTestId('marketplace-summary-kpis')).toBeVisible();
    await expect(page.getByTestId('marketplace-summary-kpis').getByText('100,00 ₽')).toBeVisible();

    await page.getByTestId('marketplace-selector').selectOption('all');
    await expect(page.getByTestId('marketplace-product-wb:42')).toBeVisible();
    await expect(page.getByTestId('marketplace-product-ozon:42')).toBeVisible();
    await expect(page.getByText('WB Cable')).toBeVisible();
    await expect(page.getByText('Ozon Cable')).toBeVisible();
    await expect(page.getByTestId('marketplace-metric-commission').getByText('35,00 ₽')).toBeVisible();
    await page.screenshot({ path: 'test-results/marketplace-finance-desktop.png', fullPage: true });
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
    await mockApplication(page, { costWrites });
    await page.getByTestId('marketplace-selector').selectOption('ozon');
    await page.getByRole('link', { name: 'Себестоимость' }).click();
    const input = page.getByTestId('marketplace-cost-input-ozon:42');
    await expect(input).toBeVisible();
    await input.fill('0');
    await page.getByRole('button', { name: 'Сохранить' }).click();
    await expect(page.getByText('Прибыль останется неизвестной')).toBeVisible();
    await expect.poll(() => costWrites.length).toBe(1);
    expect(costWrites[0]).toBe(0);

    await input.fill('');
    await page.getByRole('button', { name: 'Сохранить' }).click();
    await expect.poll(() => costWrites.length).toBe(2);
    expect(costWrites[1]).toBeNull();
  });

  test('viewer sees Ozon screens but cannot change connection, costs, or start sync', async ({ page }) => {
    await mockApplication(page, { viewer: true });
    await page.goto('/settings');
    await expect(page.getByTestId('ozon-connection-viewer-readonly')).toBeVisible();
    await expect(page.getByTestId('ozon-api-key')).toHaveCount(0);
    await expect(page.getByTestId('ozon-connection-save')).toHaveCount(0);

    await page.goto('/dashboard');
    await page.getByTestId('marketplace-selector').selectOption('ozon');
    await expect(page.getByTestId('marketplace-screen-dashboard')).toBeVisible();
    await expect(page.getByTestId('marketplace-sync-button')).toHaveCount(0);
    await page.getByRole('link', { name: 'Себестоимость' }).click();
    await expect(page.getByTestId('marketplace-cost-viewer-readonly')).toBeVisible();
    await expect(page.getByTestId('marketplace-cost-input-ozon:42')).toHaveCount(0);
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
