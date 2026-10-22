/**
 * E2E: кнопка «Развернуть таблицу» на дневной таблице дашборда растягивает её правее бокового меню,
 * Escape возвращает обратно. Требуются E2E_EMAIL и E2E_PASSWORD (реальный аккаунт с WB ключом).
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

test.describe('Dashboard daily table fullscreen', () => {
  test.beforeAll(() => {
    test.skip(!email || !password, 'Укажите E2E_EMAIL и E2E_PASSWORD для e2e (export в shell или .env.local)');
  });

  test('expand fills the window right of the sidebar, Escape folds it back', async ({ page }) => {
    await loginIfNeeded(page);
    await page.goto('/dashboard');

    const card = page.locator('.table-card', { has: page.locator('h3', { hasText: 'Детализация по дням' }) });
    await expect(card).toBeVisible({ timeout: 120_000 });
    await card.getByRole('button', { name: 'Развернуть таблицу' }).click();

    await expect(card).toHaveClass(/table-card-expanded/);
    await expect(card.getByRole('button', { name: 'Свернуть таблицу' })).toHaveAttribute('aria-expanded', 'true');

    const sidebarRight = await page.locator('.layout > .sidebar').evaluate((el) => el.getBoundingClientRect().right);
    const viewport = page.viewportSize();
    const box = await card.boundingBox();
    expect(Math.abs(box.x - sidebarRight)).toBeLessThan(1);
    expect(Math.abs(box.y)).toBeLessThan(1);
    expect(Math.abs(box.x + box.width - viewport.width)).toBeLessThan(1);
    expect(Math.abs(box.y + box.height - viewport.height)).toBeLessThan(1);

    // Горизонтальная прокрутка уходит во внутреннюю область таблицы, а не в страницу.
    const pageOverflowX = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(pageOverflowX).toBeLessThanOrEqual(0);

    await page.keyboard.press('Escape');
    await expect(card).not.toHaveClass(/table-card-expanded/);
    await expect(card.getByRole('button', { name: 'Развернуть таблицу' })).toBeFocused();
  });
});
