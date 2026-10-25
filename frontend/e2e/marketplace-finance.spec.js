/**
 * E2E на моках API: Ozon показывается тем же дашбордом и артикулами, что и WB.
 * Реальный бэкенд и учётка не нужны: все запросы к API подменяются в браузере.
 * Числа подобраны по правилам сверки (решения владельца 2026-10-25): маржа дня = пришло на баланс − себестоимость −
 * налог, налог со знаком (в день возвратов он отрицательный), у товара без себестоимости нет себестоимости и маржи.
 * SKU без карточки каталога — отдельная строка «SKU … (нет карточки)» (исключение таких SKU отменено 2026-10-25).
 */
import { test, expect } from '@playwright/test';

const store = { owner_user_id: '1', owner_email: 'owner@example.com', access: 'owner' };

function pnlRow(date, values) {
  return {
    date,
    revenue: 0,
    commission: 0,
    logistics: 0,
    penalties: 0,
    storage: 0,
    ads_spend: 0,
    cogs: 0,
    tax: 0,
    operation_expenses: 0,
    margin: 0,
    known_cost_revenue: 0,
    ...values,
  };
}

// Sale day: balance 640 − COGS 200 − tax 60 = 380. Return day: balance −440 − COGS (−100) − tax (−30) = −310.
const PNL = [
  pnlRow('2026-10-10', { revenue: 1000, commission: 300, logistics: 60, cogs: 200, tax: 60, margin: 380, known_cost_revenue: 1000 }),
  pnlRow('2026-10-11', { revenue: -500, commission: -60, logistics: 0, cogs: -100, tax: -30, margin: -310, known_cost_revenue: -500 }),
];

function skuRow(date, nmId, values) {
  return {
    date,
    nm_id: nmId,
    marketplace: 'ozon',
    revenue: 0,
    commission: 0,
    logistics: 0,
    penalties: 0,
    storage: 0,
    ads_spend: 0,
    cogs: null,
    tax: 0,
    margin: null,
    open_count: null,
    cart_count: null,
    order_count: null,
    order_sum: null,
    ...values,
  };
}

const SKU = [
  skuRow('2026-10-10', '42', { revenue: 1000, commission: 300, logistics: 60, cogs: 200, tax: 60, margin: 380 }),
  skuRow('2026-10-11', '42', { revenue: -500, commission: -60, cogs: -100, tax: -30, margin: -310 }),
  skuRow('2026-10-10', '43', { revenue: 0, logistics: 15, tax: 0 }),
  // A SKU without a catalogue card: not in the articles list, its name comes with the SKU row.
  skuRow('2026-10-10', '44', { name: 'SKU 44 (нет карточки)', revenue: 50, logistics: 2, tax: 3 }),
];

const ARTICLES = [
  { nm_id: '42', marketplace: 'ozon', vendor_code: 'ozon-42', name: 'Ozon Cable', subject_name: null, cost_price: 100 },
  { nm_id: '43', marketplace: 'ozon', vendor_code: 'ozon-43', name: 'Ozon Charger', subject_name: null, cost_price: null },
];

async function mockApplication(page, { state = { has_data: true, has_2026: true, has_2025: true }, syncRequests = [] } = {}) {
  await page.addInitScript(() => {
    localStorage.setItem('wb_finance_token', 'e2e-access-token');
    localStorage.setItem('wb_finance_active_store_owner_id', '1');
  });
  await page.route('**/auth/me', (route) => route.fulfill({ json: { ai_module_enabled: false } }));
  await page.route('**/stores', (route) => route.fulfill({ json: { stores: [store] } }));
  await page.route('**/stores/grants', (route) => route.fulfill({ json: { grants: [] } }));
  await page.route('**/billing/status', (route) => route.fulfill({ json: { subscription_status: 'lifetime', is_access_blocked: false } }));
  await page.route('**/sync/**', (route) => {
    syncRequests.push(new URL(route.request().url()).pathname);
    return route.fulfill({ json: { task_id: 'e2e', message: 'queued' } });
  });
  await page.route('**/dashboard/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/dashboard/state') {
      return route.fulfill({ json: { finance_backfill: { status: 'complete' }, ...state } });
    }
    if (path === '/dashboard/marketplace/pnl') return route.fulfill({ json: PNL });
    if (path === '/dashboard/marketplace/sku') return route.fulfill({ json: SKU });
    if (path === '/dashboard/marketplace/articles') return route.fulfill({ json: ARTICLES });
    if (path === '/dashboard/marketplace/expenses') return route.fulfill({ json: [] });
    if (path === '/dashboard/marketplace/overview') {
      return route.fulfill({
        json: {
          marketplace: 'ozon',
          daily: [],
          products: [],
          warnings: [],
          coverage: { complete: true, missing_dates: [] },
          connection: { configured: true, status: 'ready', error: null },
          sync: { status: 'idle' },
        },
      });
    }
    if (path === '/dashboard/marketplace/connection') return route.fulfill({ json: { configured: true, status: 'ready' } });
    if (path === '/dashboard/marketplace/settings') {
      return route.fulfill({ json: { tax_rate: 0.06, tax_rate_source: 'wb', operation_expenses_confirmed: false } });
    }
    if (path === '/dashboard/pnl' || path === '/dashboard/articles' || path === '/dashboard/funnel' || path === '/dashboard/sku') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}

async function openOzon(page, pathname) {
  await page.goto(pathname);
  await page.getByTestId('marketplace-selector').selectOption('ozon');
  await page.locator('input[type="date"]').first().fill('2026-10-10');
  await page.locator('input[type="date"]').nth(1).fill('2026-10-11');
  await page.getByRole('button', { name: 'Показать' }).click();
}

test.describe('Ozon screens shaped like WB', () => {
  test('dashboard sums day margins from the balance and keeps a negative tax on a return day', async ({ page }) => {
    await mockApplication(page);
    await openOzon(page, '/dashboard');

    const margin = page.locator('.kpi-card').filter({ has: page.getByText('Маржа', { exact: true }) });
    await expect(margin).toContainText('70 ₽');
    await expect(margin).toContainText('пришло на баланс − себестоимость − налог');
    await expect(page.locator('.kpi-card').filter({ has: page.getByText('Выручка', { exact: true }) })).toContainText('500 ₽');

    const card = page.locator('.table-card', { has: page.locator('h3', { hasText: 'Детализация по дням' }) });
    const header = await card.locator('thead th').allInnerTexts();
    const column = (name) => header.findIndex((text) => text.trim().toUpperCase() === name);
    const returnDay = card.locator('tbody tr', { hasText: '11.10' });
    await expect(returnDay.locator('td').nth(column('НАЛОГ'))).toHaveText('-30');
    await expect(returnDay.locator('td').nth(column('МАРЖА'))).toHaveText('-310');
    const total = card.locator('tr', { hasText: 'ИТОГО' });
    await expect(total.locator('td').nth(column('НАЛОГ'))).toHaveText('30');
    await expect(total.locator('td').nth(column('МАРЖА'))).toHaveText('70');
  });

  test('articles show no cost and no margin for a product without an entered cost or without a card', async ({ page }) => {
    await mockApplication(page);
    await openOzon(page, '/articles');

    const table = page.locator('table').first();
    const header = await table.locator('thead th').allInnerTexts();
    const column = (name) => header.findIndex((text) => text.trim().toUpperCase() === name);
    const priced = table.locator('tbody tr', { hasText: 'Ozon Cable' });
    await expect(priced.locator('td').nth(column('СЕБЕС'))).toHaveText('100');
    await expect(priced.locator('td').nth(column('МАРЖА'))).toHaveText('70');
    const unpriced = table.locator('tbody tr', { hasText: 'Ozon Charger' });
    await expect(unpriced.locator('td').nth(column('СЕБЕС'))).toHaveText('—');
    await expect(unpriced.locator('td').nth(column('МАРЖА'))).toHaveText('—');
    const uncarded = table.locator('tbody tr', { hasText: 'SKU 44 (нет карточки)' });
    await expect(uncarded.locator('td').nth(column('ВЫРУЧКА'))).toHaveText('50');
    await expect(uncarded.locator('td').nth(column('СЕБЕС'))).toHaveText('—');
    await expect(uncarded.locator('td').nth(column('МАРЖА'))).toHaveText('—');
  });

  test('an Ozon store without WB data opens on Ozon, and a rejected WB key shows its error without a new first sync', async ({ page }) => {
    const syncRequests = [];
    await mockApplication(page, { state: { has_data: false, wb_auth_error: 'wb_http_401' }, syncRequests });
    await page.goto('/dashboard');

    await expect(page.getByTestId('marketplace-selector')).toHaveValue('ozon');
    await expect(page.locator('.kpi-card').filter({ has: page.getByText('Маржа', { exact: true }) })).toBeVisible();

    await page.getByTestId('marketplace-selector').selectOption('wb');
    await expect(page.getByText('Wildberries отклонил API-ключ (ошибка 401)')).toBeVisible();
    await expect(page.getByText('Первая синхронизация данных с Wildberries')).toHaveCount(0);
    expect(syncRequests).toEqual([]);
  });
});
