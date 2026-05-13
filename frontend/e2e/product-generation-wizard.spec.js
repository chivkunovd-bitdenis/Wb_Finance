/**
 * E2E (опционально): мастер полной генерации → сначала запуск фото, затем создание товара.
 * Требует E2E_EMAIL / E2E_PASSWORD и аккаунт с is_admin; сборка без VITE_PRODUCT_GEN_UI_STUB=1.
 */
import { test, expect } from '@playwright/test';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURE_PNG = join(__dirname, 'fixtures', 'one-pixel.png');

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

test.describe('Product generation wizard (admin)', () => {
  test.beforeAll(() => {
    test.skip(!email || !password, 'Укажите E2E_EMAIL и E2E_PASSWORD');
  });

  test('мастер: запуск фото → в списке статус «В процессе»', async ({ page }) => {
    await loginIfNeeded(page);
    await page.goto('/ai-module');

    const masterBtn = page.getByRole('button', { name: 'Мастер: новая генерация' });
    try {
      await masterBtn.waitFor({ state: 'visible', timeout: 12_000 });
    } catch {
      test.skip(
        true,
        'Кнопка мастера не видна: нужен администратор и сборка без VITE_PRODUCT_GEN_UI_STUB=1',
      );
    }

    const startPromise = page.waitForResponse(
      (res) =>
        res.request().method() === 'POST' &&
        res.url().includes('/ai/product-generation/jobs/') &&
        res.url().endsWith('/start') &&
        res.ok(),
      { timeout: 120_000 },
    );

    await masterBtn.click();

    const dlg = page.locator('[role="dialog"]').filter({ hasText: 'Шаг 1 из 2' });
    await expect(dlg).toBeVisible({ timeout: 15_000 });

    await dlg.locator('input[type="file"][accept="image/*"]').setInputFiles(FIXTURE_PNG);
    await dlg.getByPlaceholder('Опишите товар, материал, особенности').fill(
      'E2E: тестовое описание товара для полной генерации.',
    );
    await dlg.getByRole('button', { name: 'Запустить генерацию фото' }).click();

    const startRes = await startPromise;
    expect(startRes.status()).toBe(200);
    await expect(dlg.getByText('Шаг 2 из 2')).toBeVisible({ timeout: 20_000 });

    const statusCell = page.locator('table.custom-table').getByText('В процессе').first();
    await expect(statusCell).toBeVisible({ timeout: 30_000 });
  });
});
