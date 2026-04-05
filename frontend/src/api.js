import { lsGet } from './safeLocalStorage';

// По умолчанию используем относительный путь и проксируем через Caddy/Cloudflare в проде.
const API_BASE = import.meta.env.VITE_API_URL !== undefined && import.meta.env.VITE_API_URL !== ''
  ? import.meta.env.VITE_API_URL
  : '';

const TOKEN_KEY = 'wb_finance_token';

const FETCH_TIMEOUT_MS = 60_000;

/** fetch с таймаутом, чтобы UI не зависал при «залипшем» ответе */
async function apiFetch(input, init = {}) {
  const ctrl = new AbortController();
  const tid = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  try {
    return await fetch(input, { ...init, signal: ctrl.signal });
  } finally {
    clearTimeout(tid);
  }
}

function getToken() {
  return lsGet(TOKEN_KEY);
}

/** Человекочитаемый текст из тела ошибки FastAPI (`detail` или validation). */
export function parseApiErrorText(bodyText, status) {
  const raw = (bodyText || '').trim();
  if (!raw) return status ? `Ошибка сервера (${status})` : 'Ошибка сервера';
  try {
    const j = JSON.parse(raw);
    if (typeof j.detail === 'string') return j.detail;
    if (Array.isArray(j.detail)) {
      const parts = j.detail.map((x) => (x && typeof x.msg === 'string' ? x.msg : null)).filter(Boolean);
      if (parts.length) return parts.join('; ');
    }
  } catch {
    /* not JSON */
  }
  return raw.length > 500 ? `${raw.slice(0, 500)}…` : raw;
}

function headers(withAuth = true) {
  const h = { 'Content-Type': 'application/json' };
  const t = getToken();
  if (withAuth && t) h['Authorization'] = `Bearer ${t}`;
  return h;
}

export async function login(email, password) {
  const res = await apiFetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: headers(false),
    body: JSON.stringify({ email, password }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(getErrorMsg(data, 'Ошибка входа'));
  }
  return data;
}

function getErrorMsg(data, fallback) {
  if (!data || typeof data !== 'object') return fallback;
  const d = data.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d[0] && typeof d[0].msg === 'string') return d[0].msg;
  return fallback;
}

export async function register(email, password, wb_api_key, promo_code) {
  const res = await apiFetch(`${API_BASE}/auth/register`, {
    method: 'POST',
    headers: headers(false),
    body: JSON.stringify({
      email,
      password,
      wb_api_key: wb_api_key || null,
      promo_code: promo_code || null,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(getErrorMsg(data, `Ошибка регистрации (${res.status})`));
  }
  return data;
}

export async function getPnl(dateFrom, dateTo) {
  const p = new URLSearchParams();
  if (dateFrom) p.set('date_from', dateFrom);
  if (dateTo) p.set('date_to', dateTo);
  const res = await apiFetch(`${API_BASE}/dashboard/pnl?${p}`, { headers: headers() });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getArticles() {
  const res = await apiFetch(`${API_BASE}/dashboard/articles`, { headers: headers() });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function saveArticlesCost(items) {
  const res = await apiFetch(`${API_BASE}/dashboard/articles/cost`, {
    method: 'PUT',
    headers: headers(),
    body: JSON.stringify(items),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getDashboardState() {
  const res = await apiFetch(`${API_BASE}/dashboard/state`, { headers: headers() });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) {
    const t = await res.text();
    throw new Error(parseApiErrorText(t, res.status));
  }
  return res.json();
}

export async function getFunnel(dateFrom, dateTo) {
  const p = new URLSearchParams();
  if (dateFrom) p.set('date_from', dateFrom);
  if (dateTo) p.set('date_to', dateTo);
  const res = await apiFetch(`${API_BASE}/dashboard/funnel?${p}`, { headers: headers() });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getSku(dateFrom, dateTo) {
  const p = new URLSearchParams();
  if (dateFrom) p.set('date_from', dateFrom);
  if (dateTo) p.set('date_to', dateTo);
  const res = await apiFetch(`${API_BASE}/dashboard/sku?${p}`, { headers: headers() });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getOperationalExpenses(dateFrom, dateTo) {
  const p = new URLSearchParams();
  if (dateFrom) p.set('date_from', dateFrom);
  if (dateTo) p.set('date_to', dateTo);
  const res = await apiFetch(`${API_BASE}/dashboard/operational-expenses?${p}`, { headers: headers() });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function createOperationalExpense({ date, amount, comment }) {
  const res = await apiFetch(`${API_BASE}/dashboard/operational-expenses`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({ date, amount, comment: comment || null }),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateOperationalExpense(expenseId, { date, amount, comment }) {
  const res = await apiFetch(`${API_BASE}/dashboard/operational-expenses/${expenseId}`, {
    method: 'PUT',
    headers: headers(),
    body: JSON.stringify({ date, amount, comment: comment || null }),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function triggerSyncSales(dateFrom, dateTo) {
  const res = await apiFetch(`${API_BASE}/sync/sales`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({ date_from: dateFrom, date_to: dateTo }),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function triggerSyncAds(dateFrom, dateTo) {
  const res = await apiFetch(`${API_BASE}/sync/ads`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({ date_from: dateFrom, date_to: dateTo }),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function triggerSyncFunnel(dateFrom, dateTo) {
  const res = await apiFetch(`${API_BASE}/sync/funnel`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({ date_from: dateFrom, date_to: dateTo }),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function triggerSyncPeriod(dateFrom, dateTo) {
  const res = await apiFetch(`${API_BASE}/sync/period`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({ date_from: dateFrom, date_to: dateTo }),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function triggerSyncRecalculate(dateFrom, dateTo) {
  const res = await apiFetch(`${API_BASE}/sync/recalculate`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({ date_from: dateFrom, date_to: dateTo }),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function triggerInitialSync() {
  const res = await apiFetch(`${API_BASE}/sync/initial`, {
    method: 'POST',
    headers: headers(),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) {
    const t = await res.text();
    throw new Error(parseApiErrorText(t, res.status));
  }
  return res.json();
}

export async function triggerRecentSync() {
  const res = await apiFetch(`${API_BASE}/sync/recent`, {
    method: 'POST',
    headers: headers(),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function triggerBackfill2026() {
  const res = await apiFetch(`${API_BASE}/sync/backfill/2026`, {
    method: 'POST',
    headers: headers(),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function triggerBackfill2025() {
  const res = await apiFetch(`${API_BASE}/sync/backfill/2025`, {
    method: 'POST',
    headers: headers(),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/** Фоновая догрузка воронки с 1 янв. текущего года (WB sales-funnel/products по дням). */
export async function triggerFunnelBackfillYtd() {
  const res = await apiFetch(`${API_BASE}/sync/funnel/backfill-ytd`, {
    method: 'POST',
    headers: headers(),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ── Daily Brief ───────────────────────────────────────────────────────────────

/**
 * Получить текущую ежедневную AI-сводку (за вчера).
 * Возвращает { date_for, status, text, error_message, generated_at }.
 */
export async function getDailyBrief() {
  const res = await apiFetch(`${API_BASE}/dashboard/daily-brief`, {
    headers: headers(),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/**
 * Инициировать генерацию сводки за вчера.
 * Возвращает { status, message, date_for }.
 */
export async function triggerDailyBrief() {
  const res = await apiFetch(`${API_BASE}/dashboard/daily-brief/generate`, {
    method: 'POST',
    headers: headers(),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ─────────────────────────────────────────────────────────────────────────────

export async function getBillingStatus() {
  const res = await apiFetch(`${API_BASE}/billing/status`, { headers: headers() });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function createCheckout(amount, returnUrl) {
  const res = await apiFetch(`${API_BASE}/billing/checkout`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({ amount, return_url: returnUrl || null }),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getBillingReminders() {
  const res = await apiFetch(`${API_BASE}/billing/reminders`, { headers: headers() });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function updateWbApiKey(wbApiKey) {
  const res = await apiFetch(`${API_BASE}/auth/wb-key`, {
    method: 'PUT',
    headers: headers(),
    body: JSON.stringify({ wb_api_key: wbApiKey }),
  });
  if (res.status === 401) throw new Error('unauthorized');
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
