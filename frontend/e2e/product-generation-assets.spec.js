import { test, expect } from '@playwright/test';

const JOB_ID = 'job-assets-ready';
const ONE_PIXEL_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
  'base64',
);

const generatedAssets = Array.from({ length: 4 }, (_, i) => ({
  asset_id: `main-${i + 1}`,
  kind: 'main_frame',
  frame_index: i,
}));

const contentAssets = Array.from({ length: 7 }, (_, i) => ({
  asset_id: `content-${i + 1}`,
  kind: 'content_frame',
  series_index: i,
}));

const readyJob = {
  id: JOB_ID,
  status: 'ready_to_publish',
  pipeline_run_id: 'run-ready',
  selected_main_asset_id: 'main-1',
  description_user: 'E2E готовая задача с фото.',
  reference_paths_json: [],
  image_pipeline: {
    remote_status: 'completed',
    generated_assets: generatedAssets,
    content_assets: contentAssets,
  },
};

async function fulfillJson(route, body) {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

async function mockAppApi(page) {
  await page.addInitScript(() => {
    localStorage.setItem('wb_finance_token', 'e2e-token');
    localStorage.setItem('ai_module_selected_nm_id', '123456');
    localStorage.setItem('ai_module_onboarding_confirmed_v1', '1');
  });

  await page.route('**/stores', (route) => fulfillJson(route, {
    stores: [{ owner_user_id: 1, owner_email: 'owner@example.test', access: 'owner' }],
  }));
  await page.route('**/billing/status', (route) => fulfillJson(route, {
    subscription_status: 'lifetime',
    is_access_blocked: false,
    days_left: 999,
  }));
  await page.route('**/dashboard/state', (route) => fulfillJson(route, {
    has_data: true,
    funnel_ytd_backfill: { status: 'idle' },
    finance_backfill: { status: 'idle' },
    finance_backfill_2025: { status: 'idle' },
    finance_missing_sync: { status: 'idle' },
    funnel_tail_sync: { status: 'idle', pending: false },
  }));
  await page.route('**/auth/me', (route) => fulfillJson(route, { is_admin: true }));
  await page.route('**/ai/competitor-reports/status?*', (route) => fulfillJson(route, { status: 'ready' }));
  await page.route('**/ai/wb-credentials/status', (route) => fulfillJson(route, { status: 'ok', has_credentials: true }));
  await page.route('**/ai/wb-access/remote/status', (route) => fulfillJson(route, { status: 'ok', active: true }));
  await page.route('**/ai/wb-access/status', (route) => fulfillJson(route, { status: 'ok', has_storage_state: true }));
  await page.route('**/ai/tasks', (route) => fulfillJson(route, { items: [] }));
  await page.route('**/ai/hypotheses', (route) => fulfillJson(route, { items: [] }));

  await page.route(`**/ai/product-generation/jobs/${JOB_ID}/generated-assets/*/file`, async (route) => {
    const assetId = route.request().url().split('/generated-assets/')[1].split('/file')[0];
    await route.fulfill({
      status: 200,
      contentType: 'image/png',
      headers: {
        'content-disposition': `attachment; filename="${assetId}.png"`,
      },
      body: ONE_PIXEL_PNG,
    });
  });
  await page.route(`**/ai/product-generation/jobs/${JOB_ID}`, (route) => fulfillJson(route, readyJob));
  await page.route('**/ai/product-generation/jobs', (route) => fulfillJson(route, { items: [readyJob] }));
}

test.describe('Product generation asset gallery', () => {
  test('opens preview and downloads first 4 and content assets', async ({ page }) => {
    await mockAppApi(page);

    await page.goto('/ai-module');

    await page.getByRole('button', { name: /Генерация товара/ }).click();
    const wizard = page.getByRole('dialog', { name: 'Полная генерация товара' });
    await expect(wizard.getByText('Шаг 2 из 2')).toBeVisible();
    await expect(wizard.getByRole('button', { name: 'Скачать все 4 фото' })).toBeEnabled();
    await expect(wizard.getByRole('button', { name: 'Скачать все 7 фото' })).toBeEnabled();

    await wizard.getByRole('button', { name: 'Открыть предпросмотр: Вариант 1' }).click();
    const preview = page.getByRole('dialog', { name: 'Вариант 1' });
    await expect(preview.getByRole('img', { name: 'Вариант 1' })).toBeVisible();

    const singleDownload = page.waitForEvent('download');
    await preview.getByRole('button', { name: 'Скачать' }).click();
    expect((await singleDownload).suggestedFilename()).toBe('main-1.png');

    await preview.getByRole('button', { name: 'Закрыть' }).last().click();

    const batchDownload = page.waitForEvent('download');
    await wizard.getByRole('button', { name: 'Скачать все 7 фото' }).click();
    expect((await batchDownload).suggestedFilename()).toBe('content-1.png');

    await wizard.getByRole('button', { name: 'Открыть предпросмотр: Контент 1' }).click();
    await expect(page.getByRole('dialog', { name: 'Контент 1' }).getByRole('img', { name: 'Контент 1' })).toBeVisible();
  });
});
