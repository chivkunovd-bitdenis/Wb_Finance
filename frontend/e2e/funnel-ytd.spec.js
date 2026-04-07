/**
 * Набор проверок фичи «YTD воронка» (аналитика / контракт).
 * Требуются E2E_EMAIL и E2E_PASSWORD (реальный аккаунт с данными WB).
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

function watchDashboardState(page) {
  const states = [];
  page.on('response', async (res) => {
    try {
      const u = res.url();
      if (!u.includes('/dashboard/state')) return;
      if (res.status() !== 200) return;
      const j = await res.json();
      states.push({
        t: Date.now(),
        status: res.status(),
        funnel_ytd: j.funnel_ytd_backfill ?? null,
        has_data: j.has_data,
        has_funnel: j.has_funnel,
      });
    } catch {
      /* ignore parse errors */
    }
  });
  return states;
}

function watchBackfillPost(page) {
  const posts = [];
  page.on('response', async (res) => {
    try {
      const u = res.url();
      if (!u.includes('/sync/funnel/backfill-ytd')) return;
      const method = res.request().method();
      if (method !== 'POST') return;
      let body = null;
      try {
        body = await res.json();
      } catch {
        body = await res.text();
      }
      posts.push({ t: Date.now(), status: res.status(), body });
    } catch {
      /* ignore */
    }
  });
  return posts;
}

test.describe('Funnel YTD feature', () => {
  test.beforeAll(() => {
    test.skip(!email || !password, 'Укажите E2E_EMAIL и E2E_PASSWORD для e2e (export в shell или .env.local)');
  });

  test('контракт state + запуск backfill после логина и reload на /funnel', async ({ page }) => {
    const stateLog = watchDashboardState(page);
    const backfillLog = watchBackfillPost(page);

    await loginIfNeeded(page);

    await page.goto('/funnel');

    // Лоадер вкладки "Воронка" должен появляться во время загрузки.
    // Не гарантируем, что он будет виден всегда (зависит от кэша/скорости сети),
    // поэтому проверяем "если появился — текст корректный".
    const funnelLoader = page.getByText('Загружаем воронку…');
    if (await funnelLoader.isVisible().catch(() => false)) {
      await expect(funnelLoader).toBeVisible();
    }

    await expect(page.getByText('Воронка по артикулам').first()).toBeVisible({
      timeout: 120_000,
    });

    await expect.poll(() => stateLog.length, { timeout: 120_000 }).toBeGreaterThan(0);

    const last = stateLog[stateLog.length - 1];
    expect(last.funnel_ytd).toBeTruthy();
    expect(last.funnel_ytd).toMatchObject({
      year: expect.any(Number),
      status: expect.stringMatching(/^(idle|running|complete|error)$/),
    });
    expect(last.funnel_ytd).toHaveProperty('through_date');
    expect(last.funnel_ytd).toHaveProperty('last_completed_date');

    /** Если не complete — ожидаем хотя бы одну попытку POST (или уже running с дедупом на API). */
    if (last.funnel_ytd.status !== 'complete') {
      await expect
        .poll(() => backfillLog.filter((x) => x.status === 200).length, { timeout: 90_000 })
        .toBeGreaterThan(0);
    }

    stateLog.length = 0;
    backfillLog.length = 0;

    await page.reload();

    await expect.poll(() => stateLog.length, { timeout: 120_000 }).toBeGreaterThan(0);

    const afterReload = stateLog[stateLog.length - 1];
    expect(afterReload.funnel_ytd).toBeTruthy();

    /** После reload снова должны увидеть валидный объект прогресса. */
    expect(afterReload.funnel_ytd.status).toMatch(/^(idle|running|complete|error)$/);

    /** Плашка не обязана всегда быть видна, но при running не должно быть «тихой» ошибки без баннера/алерта. */
    if (afterReload.funnel_ytd.status === 'error' && afterReload.funnel_ytd.error_message) {
      await expect(page.getByTestId('funnel-ytd-error')).toBeVisible({ timeout: 15_000 });
    }
  });
});
