/* eslint react-hooks/set-state-in-effect: off */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as api from '../api';
import { useStore } from '../StoreContext';

export default function Settings() {
  const { refreshStores, activeStore, activeStoreOwnerId, isOwnerStore } = useStore();
  const [email, setEmail] = useState('');
  const [lockedEmail, setLockedEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const isGranted = Boolean(lockedEmail);
  const canManageOzon = Boolean(activeStore && isOwnerStore);
  const value = isGranted ? lockedEmail : email;

  const loadExisting = useCallback(async () => {
    setError('');
    try {
      const data = await api.getOutgoingStoreGrants();
      const grants = Array.isArray(data?.grants) ? data.grants : [];
      const active = grants.find((g) => g.status === 'active' && !g.revoked_at) || null;
      if (active?.viewer_email) {
        setLockedEmail(String(active.viewer_email));
      } else {
        setLockedEmail('');
      }
    } catch (e) {
      setError(e?.message || 'Не удалось загрузить настройки доступа');
    }
  }, []);

  useEffect(() => {
    loadExisting();
  }, [loadExisting]);

  const helperText = useMemo(() => {
    if (isGranted) return 'Доступ выдан. Можно отключить в любой момент.';
    return 'Введите e-mail селлера (существующего в системе), которому вы хотите выдать доступ к своему магазину.';
  }, [isGranted]);

  return (
    <div style={{ maxWidth: 720 }}>
      <div style={{ fontSize: 18, fontWeight: 800, marginBottom: 12 }}>Настройки</div>

      <OzonConnectionSettings key={activeStoreOwnerId || 'self'} activeStore={activeStore} activeStoreOwnerId={activeStoreOwnerId} canManage={canManageOzon} />
      <OzonFinancialSettings key={`finance:${activeStoreOwnerId || 'self'}`} activeStoreOwnerId={activeStoreOwnerId} canManage={canManageOzon} />

      <div
        style={{
          padding: 14,
          border: '1px solid rgba(0,0,0,0.12)',
          borderRadius: 12,
          background: 'white',
        }}
      >
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Выдать доступ к магазину</div>
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 10 }}>{helperText}</div>

        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            type="text"
            value={value}
            disabled={loading || isGranted}
            placeholder="seller@example.com"
            onChange={(e) => setEmail(e.target.value)}
            style={{ minWidth: 260, flex: '1 1 260px' }}
          />

          {!isGranted ? (
            <button
              className="btn-primary"
              disabled={loading || !String(email).trim()}
              onClick={async () => {
                setLoading(true);
                setError('');
                try {
                  const target = String(email).trim();
                  await api.grantStoreAccess(target);
                  setLockedEmail(target);
                  setEmail('');
                  await refreshStores();
                } catch (e) {
                  setError(e?.message || 'Не удалось выдать доступ');
                } finally {
                  setLoading(false);
                }
              }}
            >
              {loading ? '...' : 'Выдать доступ'}
            </button>
          ) : (
            <button
              className="btn-danger"
              disabled={loading}
              onClick={async () => {
                const ok = window.confirm('Отключить доступ селлеру к вашему магазину?');
                if (!ok) return;
                setLoading(true);
                setError('');
                try {
                  await api.revokeStoreAccess(String(lockedEmail));
                  setLockedEmail('');
                  await refreshStores();
                } catch (e) {
                  setError(e?.message || 'Не удалось отключить доступ');
                } finally {
                  setLoading(false);
                }
              }}
            >
              {loading ? '...' : 'Отключить доступ'}
            </button>
          )}
        </div>

        {isGranted && (
          <div
            style={{
              marginTop: 10,
              padding: '10px 12px',
              borderRadius: 10,
              background: '#e8f4ec',
              color: '#155724',
              border: '1px solid #c3e6cb',
              fontSize: 13,
            }}
          >
            Доступ активен для: <strong>{lockedEmail}</strong>
          </div>
        )}

        {error && (
          <div style={{ marginTop: 10, color: 'var(--red)', fontSize: 13 }}>
            {error}
          </div>
        )}
      </div>
    </div>
  );
}

function getOzonConnectionStatus(connection, { loading = false, saving = false, loadError = '' } = {}) {
  const status = String(connection?.status || '').toLowerCase();
  if (loading || saving || ['syncing', 'queued', 'pending', 'running'].includes(status)) {
    return { label: 'Проверяем', className: 'tag-marketplace-syncing' };
  }
  if (loadError || ['error', 'needs_reauth', 'failed'].includes(status)) {
    return { label: 'Ошибка', className: 'tag-marketplace-error' };
  }
  if (status === 'ready') {
    return { label: 'Подключён', className: 'tag-marketplace-connected' };
  }
  if (status === 'configured' || connection?.configured === true) {
    return { label: 'Данные сохранены', className: 'tag-gray' };
  }
  return { label: 'Не подключён', className: 'tag-gray' };
}

function formatTaxRatePercent(rate) {
  if (rate === null || rate === undefined || !Number.isFinite(Number(rate))) return 'не задана';
  return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 4 }).format(Number(rate) * 100)}%`;
}

function OzonFinancialSettings({ activeStoreOwnerId, canManage }) {
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [expensesConfirmedDraft, setExpensesConfirmedDraft] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const requestGeneration = useRef(0);
  const originalTaxRate = settings?.tax_rate == null ? null : Number(settings.tax_rate);
  const originalExpensesConfirmed = settings?.operation_expenses_confirmed === true;
  const isDirty = settings !== null && expensesConfirmedDraft !== originalExpensesConfirmed;

  useEffect(() => {
    const controller = new AbortController();
    const generation = ++requestGeneration.current;
    setLoading(true);
    setSettings(null);
    setExpensesConfirmedDraft(false);
    setError('');
    setSaved(false);

    api.getMarketplaceSettings({ signal: controller.signal })
      .then((result) => {
        if (generation !== requestGeneration.current) return;
        setSettings(result);
        setExpensesConfirmedDraft(result?.operation_expenses_confirmed === true);
      })
      .catch((loadError) => {
        if (generation !== requestGeneration.current || controller.signal.aborted) return;
        setError(loadError?.message || 'Не удалось загрузить налоговые и расходные настройки Ozon.');
      })
      .finally(() => {
        if (generation === requestGeneration.current && !controller.signal.aborted) setLoading(false);
      });

    return () => {
      controller.abort();
      if (generation === requestGeneration.current) requestGeneration.current += 1;
    };
  }, [activeStoreOwnerId]);

  const reset = () => {
    setExpensesConfirmedDraft(originalExpensesConfirmed);
    setError('');
    setSaved(false);
  };

  const save = async (event) => {
    event.preventDefault();
    if (!canManage || saving) return;
    const payload = {
      operation_expenses_confirmed: expensesConfirmedDraft,
    };
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      const result = await api.saveMarketplaceSettings(payload);
      const next = {
        tax_rate: result?.tax_rate === undefined ? originalTaxRate : result.tax_rate,
        tax_rate_source: result?.tax_rate_source === undefined ? settings?.tax_rate_source : result.tax_rate_source,
        operation_expenses_confirmed: result?.operation_expenses_confirmed === undefined
          ? payload.operation_expenses_confirmed
          : result.operation_expenses_confirmed,
      };
      setSettings(next);
      setExpensesConfirmedDraft(next.operation_expenses_confirmed === true);
      setSaved(true);
    } catch (saveError) {
      setError(saveError?.message || 'Не удалось сохранить настройки Ozon.');
    } finally {
      setSaving(false);
    }
  };

  const expensesConfirmedLabel = originalExpensesConfirmed ? 'Подтверждены' : 'Не подтверждены';

  return (
    <section className="marketplace-settings-card" data-testid="ozon-financial-settings">
      <div className="marketplace-settings-heading">
        <div>
          <h3>Налог и общие расходы Ozon</h3>
          <p>Ставка налога берётся из настроек Wildberries; отдельно здесь настраивается только полнота собственных расходов Ozon.</p>
        </div>
        <span className="tag tag-gray" data-testid="ozon-financial-settings-status">
          {loading || saving ? 'Проверяем' : error ? 'Ошибка' : 'Настроено'}
        </span>
      </div>

      {error && <div className="marketplace-notice marketplace-notice-error" role="alert" data-testid="ozon-financial-settings-error">{error}</div>}
      {loading ? (
        <div className="marketplace-settings-note">Загружаем настройки магазина…</div>
      ) : canManage ? (
        <form className="marketplace-connection-form" onSubmit={save}>
          <div className="marketplace-settings-note" data-testid="ozon-tax-rate-readonly">
            Ставка налога из настроек WB: {formatTaxRatePercent(originalTaxRate)}. Измените её в общих настройках учёта Wildberries.
          </div>
          <label className="marketplace-confirmation-label">
            <input
              type="checkbox"
              checked={expensesConfirmedDraft}
              onChange={(event) => { setExpensesConfirmedDraft(event.target.checked); setSaved(false); }}
              disabled={saving}
              data-testid="ozon-opex-confirm"
            />
            <span>Подтверждаю, что все общие операционные расходы Ozon внесены.</span>
          </label>
          <div className="marketplace-settings-note">Если подтверждение включено, день без отдельной суммы считается днём с расходами 0 ₽. Пока оно выключено, незаполненная сумма остаётся неизвестной.</div>
          <div className="marketplace-connection-actions">
            <button className="btn-primary" type="submit" disabled={saving || !isDirty} data-testid="ozon-financial-settings-save">
              {saving ? 'Сохраняем…' : 'Сохранить настройки'}
            </button>
            <button className="btn-outline" type="button" disabled={saving || !isDirty} onClick={reset}>Сбросить</button>
          </div>
        </form>
      ) : (
        <div className="marketplace-settings-readonly" data-testid="ozon-financial-settings-viewer-readonly">
          Ставка налога из настроек WB: {formatTaxRatePercent(originalTaxRate)}. Общие расходы: {expensesConfirmedLabel.toLowerCase()}.
        </div>
      )}
      {saved && <div className="marketplace-notice marketplace-notice-success" role="status" data-testid="ozon-financial-settings-saved">Настройки сохранены.</div>}
    </section>
  );
}

function OzonConnectionSettings({ activeStore, activeStoreOwnerId, canManage }) {
  const [connection, setConnection] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [clientId, setClientId] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [loadError, setLoadError] = useState('');
  const [formError, setFormError] = useState('');
  const [saved, setSaved] = useState(false);
  const requestGeneration = useRef(0);
  const savedClientId = String(connection?.client_id || '');
  const isDirty = clientId.trim() !== savedClientId || apiKey.length > 0;
  const connectionStatus = getOzonConnectionStatus(connection, { loading, saving, loadError });

  useEffect(() => {
    const controller = new AbortController();
    const generation = ++requestGeneration.current;
    setLoading(true);
    setConnection(null);
    setClientId('');
    setApiKey('');
    setLoadError('');
    setFormError('');
    setSaved(false);

    api.getMarketplaceConnection({ signal: controller.signal })
      .then((result) => {
        if (generation !== requestGeneration.current) return;
        setConnection(result);
        setClientId(String(result?.client_id || ''));
      })
      .catch((error) => {
        if (generation !== requestGeneration.current || controller.signal.aborted) return;
        setLoadError(error?.message || 'Не удалось загрузить подключение Ozon.');
      })
      .finally(() => {
        if (generation === requestGeneration.current && !controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      if (generation === requestGeneration.current) requestGeneration.current += 1;
    };
  }, [activeStoreOwnerId]);

  const resetForm = () => {
    setClientId(savedClientId);
    setApiKey('');
    setFormError('');
    setSaved(false);
  };

  const save = async (event) => {
    event.preventDefault();
    if (!canManage || saving) return;
    const normalizedClientId = clientId.trim();
    const normalizedKey = apiKey;
    if (!normalizedClientId) {
      setFormError('Укажите Client ID из кабинета продавца Ozon.');
      return;
    }
    if (!normalizedKey.trim()) {
      setFormError('Введите API-ключ. Он не отображается повторно после сохранения.');
      return;
    }

    setSaving(true);
    setFormError('');
    setSaved(false);
    try {
      const result = await api.saveMarketplaceConnection(normalizedClientId, normalizedKey);
      setConnection((current) => ({
        ...current,
        ...result,
        configured: result?.configured ?? true,
        client_id: result?.client_id ?? normalizedClientId,
      }));
      setClientId(String(result?.client_id ?? normalizedClientId));
      setApiKey('');
      setSaved(true);
    } catch (error) {
      setFormError(error?.message || 'Не удалось сохранить подключение. Проверьте Client ID и ключ, затем повторите.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="marketplace-settings-card" data-testid="ozon-connection-settings">
      <div className="marketplace-settings-heading">
        <div>
          <h3>Подключение Ozon</h3>
          <p>Ключ нужен только для чтения начислений, товаров и финансовых деталей.</p>
        </div>
        <span className={`tag ${connectionStatus.className}`} data-testid="ozon-connection-status">
          {connectionStatus.label}
        </span>
      </div>

      {loadError && <div className="marketplace-notice marketplace-notice-error" role="alert">{loadError}</div>}
      {['error', 'needs_reauth'].includes(String(connection?.status || '').toLowerCase()) && connection?.error && (
        <div className="marketplace-notice marketplace-notice-error" role="alert">Ошибка подключения: {connection.error}</div>
      )}

      {canManage ? (
        <form className="marketplace-connection-form" onSubmit={save}>
          <label>
            <span>Client ID</span>
            <input
              autoComplete="off"
              value={clientId}
              onChange={(event) => { setClientId(event.target.value); setSaved(false); setFormError(''); }}
              disabled={loading || saving}
              placeholder="Client ID продавца"
              data-testid="ozon-client-id"
            />
          </label>
          <label>
            <span>API-ключ</span>
            <input
              type="password"
              autoComplete="new-password"
              value={apiKey}
              onChange={(event) => { setApiKey(event.target.value); setSaved(false); setFormError(''); }}
              disabled={loading || saving}
              placeholder={connection?.configured ? 'Введите новый ключ для замены' : 'Вставьте API-ключ'}
              data-testid="ozon-api-key"
            />
          </label>
          <div className="marketplace-connection-actions">
            <button className="btn-primary" type="submit" disabled={loading || saving || !isDirty || !clientId.trim() || !apiKey.trim()} data-testid="ozon-connection-save">
              {saving ? 'Сохраняем…' : 'Сохранить подключение'}
            </button>
            <button className="btn-outline" type="button" disabled={saving || !isDirty} onClick={resetForm}>Сбросить</button>
          </div>
          <div className="marketplace-settings-note">API-ключ не сохраняется в браузере и очищается из формы после отправки.</div>
        </form>
      ) : (
        <div className="marketplace-settings-readonly" data-testid="ozon-connection-viewer-readonly">
          {activeStore?.owner_email ? `Магазин ${activeStore.owner_email}.` : 'Подключением может управлять только владелец магазина.'}
          {' '}Статус кабинета: {connectionStatus.label.toLowerCase()}.
        </div>
      )}
      {formError && <div className="marketplace-notice marketplace-notice-error" role="alert" data-testid="ozon-connection-form-error">{formError}</div>}
      {saved && <div className="marketplace-notice marketplace-notice-success" role="status" data-testid="ozon-connection-saved">Данные сохранены. API-ключ очищен из формы.</div>}
    </section>
  );
}

