/**
 * E2E: Dashboard table header sticks + applying period triggers sync for that window.
 * Требуются E2E_EMAIL и E2E_PASSWORD (реальный аккаунт с WB ключом).
 */
import { test, expect } from '@playwright/test';

const email = process.env.E2E_EMAIL?.trim();
const password = process.env.E2E_PASSWORD?.trim();

async function loginIfNeeded(page) {
  await page.goto('/');
  const token = await page.evaluate(() => localStorage.getItem('wb_finance_token'));
  if (token) return;

  await page.getByPlaceholder('Логин (Email)').fill(email);
  await page.getByPlaceholder('Пароль').fill(password);
  await page.getByRole('button', { name: 'Войти' }).click();

  await expect.poll(async () => page.evaluate(() => localStorage.getItem('wb_finance_token')), {
    timeout: 60_000,
  }).not.toBeNull();
}

function watchSyncPosts(page) {
  const hits = [];
  page.on('response', async (res) => {
    const u = res.url();
    const method = res.request().method();
    if (method !== 'POST') return;
    if (!u.includes('/sync/')) return;
    if (!u.includes('/sync/period')) return;
    hits.push({ url: u, status: res.status() });
  });
  return hits;
}

test.describe('Dashboard UX', () => {
  test.beforeAll(() => {
    test.skip(!email || !password, 'Укажите E2E_EMAIL и E2E_PASSWORD для e2e (export в shell или .env.local)');
  });

  test('sticky header in day details table + apply triggers sync', async ({ page }) => {
    const syncHits = watchSyncPosts(page);
    await loginIfNeeded(page);

    await page.goto('/dashboard');
    await expect(page.getByText('Детализация по дням').first()).toBeVisible({ timeout: 120_000 });

    const wrap = page.locator('.table-wrap').first();
    const headerCell = wrap.locator('thead th').first();

    // Scroll the table container down and ensure header stays at top of the container.
    const wrapBoxBefore = await wrap.boundingBox();
    expect(wrapBoxBefore).toBeTruthy();

    await page.waitForTimeout(300); // allow layout settle
    await wrap.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });
    await page.waitForTimeout(300);

    const wrapBox = await wrap.boundingBox();
    const headerBox = await headerCell.boundingBox();
    expect(wrapBox).toBeTruthy();
    expect(headerBox).toBeTruthy();

    // Header top should remain near container top (allow small rounding differences).
    expect(Math.abs(headerBox.y - wrapBox.y)).toBeLessThan(3);

    // Apply a wider period and verify we enqueue sync endpoints (sales/ads/funnel).
    // Use a safe period: last 14 days ending yesterday.
    const to = new Date();
    to.setDate(to.getDate() - 1);
    const from = new Date(to);
    from.setDate(from.getDate() - 13);
    const iso = (d) => d.toISOString().slice(0, 10);

    const inputs = page.locator('.date-group input[type="date"]');
    await inputs.nth(0).fill(iso(from));
    await inputs.nth(1).fill(iso(to));
    await page.getByRole('button', { name: 'Показать' }).click();

    await expect
      .poll(() => syncHits.filter((h) => h.status === 200 || h.status === 202).map((h) => h.url), { timeout: 60_000 })
      .toEqual(expect.arrayContaining([expect.stringContaining('/sync/period')]));
  });
});

