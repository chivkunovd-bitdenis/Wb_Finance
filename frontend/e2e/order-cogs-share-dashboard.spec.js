/**
 * E2E (моки API): колонка «Доля себеса заказов» на дашборде WB.
 */
import { test, expect } from '@playwright/test';

const store = { owner_user_id: '1', owner_email: 'owner@example.com', access: 'owner' };

function pnlRow(date, values) {
  return {
    date,
    revenue: 1000,
    commission: 100,
    logistics: 50,
    penalties: 0,
    storage: 0,
    ads_spend: 0,
    cogs: 200,
    tax: 60,
    operation_expenses: 0,
    margin: 590,
    known_cost_revenue: 1000,
    ...values,
  };
}

const PNL = [
  pnlRow('2026-10-10'),
  pnlRow('2026-10-11'),
];

function funnelRow(date, nmId, values) {
  return {
    date,
    nm_id: nmId,
    vendor_code: `vc-${nmId}`,
    open_count: 10,
    cart_count: 5,
    order_count: 2,
    order_sum: 500,
    order_cogs: 100,
    buyout_percent: 50,
    cr_to_cart: 10,
    cr_to_order: 5,
    ...values,
  };
}

/** День 10: 100+50 cogs на 500+500 order_sum → 15%. День 11: один SKU без себеса → прочерк; итог периода тоже прочерк. */
const FUNNEL_OK = [
  funnelRow('2026-10-10', 1, { order_count: 2, order_sum: 500, order_cogs: 100 }),
  funnelRow('2026-10-10', 2, { order_count: 1, order_sum: 500, order_cogs: 50 }),
  funnelRow('2026-10-11', 1, { order_count: 0, order_sum: 0, order_cogs: 0 }),
];

const FUNNEL_WITH_GAP = [
  ...FUNNEL_OK,
  funnelRow('2026-10-11', 3, { order_count: 1, order_sum: 300, order_cogs: null }),
];

async function mockDashboard(page, { funnel = FUNNEL_OK, delayFunnelMs = 0 } = {}) {
  await page.addInitScript(() => {
    localStorage.setItem('wb_finance_token', 'e2e-access-token');
    localStorage.setItem('wb_finance_active_store_owner_id', '1');
  });

  await page.route('**/auth/me', (route) => route.fulfill({ json: { ai_module_enabled: false } }));
  await page.route('**/stores', (route) => route.fulfill({ json: { stores: [store] } }));
  await page.route('**/stores/grants', (route) => route.fulfill({ json: { grants: [] } }));
  await page.route('**/billing/status', (route) => route.fulfill({ json: { subscription_status: 'lifetime', is_access_blocked: false } }));
  await page.route('**/sync/**', (route) => route.fulfill({ json: { task_id: 'e2e', message: 'queued' } }));

  await page.route('**/dashboard/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/dashboard/state') {
      return route.fulfill({ json: { finance_backfill: { status: 'complete' }, has_data: true } });
    }
    if (path === '/dashboard/pnl') return route.fulfill({ json: PNL });
    if (path === '/dashboard/funnel') {
      if (delayFunnelMs > 0) await new Promise((r) => setTimeout(r, delayFunnelMs));
      return route.fulfill({ json: funnel });
    }
    if (path === '/dashboard/plan-fact/months') return route.fulfill({ json: [] });
    return route.fulfill({ json: [] });
  });
}

async function openWbDashboard(page) {
  await page.goto('/dashboard');
  await page.locator('input[type="date"]').first().fill('2026-10-10');
  await page.locator('input[type="date"]').nth(1).fill('2026-10-11');
  await page.getByRole('button', { name: 'Показать' }).click();
  await expect(page.getByText('Детализация по дням').first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('columnheader', { name: 'Доля себеса заказов', exact: true })).toBeVisible();
}

function columnIndex(headerTexts, name) {
  return headerTexts.findIndex((t) => (t || '').trim() === name);
}

test.describe('Dashboard: доля себеса заказов', () => {
  test('скрытие новой колонки сохраняется после перезагрузки', async ({ page }) => {
    await mockDashboard(page);
    await openWbDashboard(page);
    await page.getByRole('button', { name: 'Настройки таблицы' }).click();
    await page.getByRole('menu', { name: 'Настройки таблицы' }).getByText('Доля себеса заказов', { exact: true }).click();
    await expect(page.getByRole('columnheader', { name: 'Доля себеса заказов', exact: true })).toHaveCount(0);
    await page.reload();
    await expect(page.getByText('Детализация по дням').first()).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Доля себеса заказов', exact: true })).toHaveCount(0);
  });

  test('WB: считает день и итог как отношение сумм', async ({ page }) => {
    await mockDashboard(page, { funnel: FUNNEL_OK });
    await openWbDashboard(page);

    const card = page.locator('.table-card', { has: page.locator('h3', { hasText: 'Детализация по дням' }) });
    await expect.poll(async () => columnIndex(await card.locator('thead th').allTextContents(), 'Доля себеса заказов')).toBeGreaterThan(-1);
    const header = await card.locator('thead th').allTextContents();
    const idx = columnIndex(header, 'Доля себеса заказов');
    expect(idx).toBeGreaterThan(columnIndex(header, 'Доля себеса'));

    const day10 = card.locator('tbody tr', { hasText: '10.10' });
    await expect(day10.locator('td').nth(idx)).toHaveText('15.0%');

    const total = card.locator('tr', { hasText: 'ИТОГО' });
    await expect(total.locator('td').nth(idx)).toHaveText('15.0%');

    const headerCell = card.locator('thead th').nth(idx);
    await expect(headerCell).toHaveAttribute('title', /Себестоимость единицы/);
  });

  test('WB: неизвестная себестоимость заказа — прочерк в день и в итоге', async ({ page }) => {
    await mockDashboard(page, { funnel: FUNNEL_WITH_GAP });
    await openWbDashboard(page);

    const card = page.locator('.table-card', { has: page.locator('h3', { hasText: 'Детализация по дням' }) });
    await expect.poll(async () => columnIndex(await card.locator('thead th').allTextContents(), 'Доля себеса заказов')).toBeGreaterThan(-1);
    const header = await card.locator('thead th').allTextContents();
    const idx = columnIndex(header, 'Доля себеса заказов');

    const day11 = card.locator('tbody tr', { hasText: '11.10' });
    await expect(day11.locator('td').nth(idx)).toHaveText('—');

    const total = card.locator('tr', { hasText: 'ИТОГО' });
    await expect(total.locator('td').nth(idx)).toHaveText('—');
  });

  test('отсутствующий день не даёт частичный итог', async ({ page }) => {
    await mockDashboard(page, { funnel: FUNNEL_OK.filter((row) => row.date === '2026-10-10') });
    await openWbDashboard(page);
    const card = page.locator('.table-card', { has: page.locator('h3', { hasText: 'Детализация по дням' }) });
    const idx = columnIndex(await card.locator('thead th').allTextContents(), 'Доля себеса заказов');
    expect(idx).toBeGreaterThan(-1);
    await expect(card.locator('tr', { hasText: 'ИТОГО' }).locator('td').nth(idx)).toHaveText('—');
  });

  test('Ozon: колонка видна, значения — прочерк', async ({ page }) => {
    await mockDashboard(page);
    await page.route('**/dashboard/marketplace/**', async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/dashboard/marketplace/pnl') return route.fulfill({ json: PNL });
      if (path === '/dashboard/marketplace/settings') {
        return route.fulfill({ json: { tax_rate: 0.06, tax_rate_source: 'wb', operation_expenses_confirmed: false } });
      }
      if (path === '/dashboard/marketplace/connection') {
        return route.fulfill({ json: { configured: true, status: 'ready' } });
      }
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
      return route.fulfill({ json: [] });
    });
    await page.goto('/dashboard');
    await page.getByTestId('marketplace-selector').selectOption('ozon');
    await page.locator('input[type="date"]').first().fill('2026-10-10');
    await page.locator('input[type="date"]').nth(1).fill('2026-10-10');
    await page.getByRole('button', { name: 'Показать' }).click();

    const card = page.locator('.table-card', { has: page.locator('h3', { hasText: 'Детализация по дням' }) });
    await expect(card.getByRole('columnheader', { name: 'Доля себеса заказов' })).toBeVisible({ timeout: 30_000 });

    await expect.poll(async () => columnIndex(await card.locator('thead th').allTextContents(), 'Доля себеса заказов')).toBeGreaterThan(-1);
    const header = await card.locator('thead th').allTextContents();
    const idx = columnIndex(header, 'Доля себеса заказов');
    const day = card.locator('tbody tr', { hasText: '10.10' });
    await expect(day.locator('td').nth(idx)).toHaveText('—');
  });

  test('пока воронка грузится — не показываем 0%', async ({ page }) => {
    await mockDashboard(page, { funnel: FUNNEL_OK, delayFunnelMs: 2500 });
    await openWbDashboard(page);

    const card = page.locator('.table-card', { has: page.locator('h3', { hasText: 'Детализация по дням' }) });
    await expect.poll(async () => columnIndex(await card.locator('thead th').allTextContents(), 'Доля себеса заказов')).toBeGreaterThan(-1);
    const header = await card.locator('thead th').allTextContents();
    const idx = columnIndex(header, 'Доля себеса заказов');
    const day10 = card.locator('tbody tr', { hasText: '10.10' });

    await expect(day10.locator('td').nth(idx)).toHaveText('—', { timeout: 1500 });
    await expect(day10.locator('td').nth(idx)).toHaveText('15.0%', { timeout: 15_000 });
  });
});
